import json
import os
import shutil
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from simgen.cli import main
from simgen.db import OrderDB
from simgen.events import EventLog
from simgen.generator import process_order, qc_output_file, read_output_file
from simgen.identifiers import iccid_range, imsi_range, luhn_check_digit, luhn_valid
from simgen.inputfile import InputFileError, parse_input_file
from simgen.keys import KeyStore, compute_opc, decrypt_from_transport, write_lab_keys
from simgen.watcher import ensure_folders, requeue_stale, run_once

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.fixture
def env(tmp_path):
    write_lab_keys(tmp_path / "keys.json")
    keys = KeyStore.load(tmp_path / "keys.json")
    db = OrderDB(str(tmp_path / "orders.db"))
    log = EventLog(str(tmp_path / "events.log"))
    yield tmp_path, keys, db, log
    db.close()


def events(tmp_path):
    return [json.loads(line) for line in (tmp_path / "events.log").read_text().splitlines()]


# --- identifiers -----------------------------------------------------------

def test_luhn_known_values():
    assert luhn_check_digit("7992739871") == "3"  # the textbook Luhn example
    assert luhn_valid("79927398713")
    assert not luhn_valid("79927398710")


def test_iccid_range_is_sequential_and_valid():
    ids = list(iccid_range("890010100000000001", 3))
    assert [i[:-1] for i in ids] == ["890010100000000001", "890010100000000002", "890010100000000003"]
    assert all(len(i) == 19 and luhn_valid(i) for i in ids)


def test_imsi_range_refuses_prefix_overflow():
    with pytest.raises(ValueError):
        list(imsi_range("001019999999999", 2))


# --- keys ------------------------------------------------------------------

def test_opc_matches_3gpp_ts_35_208_test_set_1():
    ki = bytes.fromhex("465b5ce8b199b49faa5f0a2ee238a6bc")
    op = bytes.fromhex("cdc202d5123e20f62b6d676ac72cb318")
    assert compute_opc(ki, op).hex() == "cd63cb71954a9f4e48a5994e37a02baf"


def test_lab_key_file_is_private(tmp_path):
    write_lab_keys(tmp_path / "k.json")
    assert oct((tmp_path / "k.json").stat().st_mode & 0o777) == "0o600"


# --- input file ------------------------------------------------------------

def test_parse_good_input():
    order = parse_input_file(SAMPLES / "order_good.txt")
    assert order.quantity == 500 and order.order_id == "ORD-20261019-0001"


@pytest.mark.parametrize("sample, code", [
    ("order_missing_quantity.txt", "E_MISSING_FIELD"),
    ("order_truncated.txt", "E_TRUNCATED"),
])
def test_bad_inputs_have_clear_codes(sample, code):
    with pytest.raises(InputFileError) as e:
        parse_input_file(SAMPLES / sample)
    assert e.value.code == code


def test_quantity_must_be_a_number(tmp_path):
    bad = (SAMPLES / "order_good.txt").read_text().replace("Quantity: 500", "Quantity: 5O0")
    (tmp_path / "bad.txt").write_text(bad)
    with pytest.raises(InputFileError) as e:
        parse_input_file(tmp_path / "bad.txt")
    assert e.value.code == "E_BAD_VALUE"


# --- pipeline --------------------------------------------------------------

def test_good_order_end_to_end(env):
    tmp, keys, db, log = env
    summary = process_order(SAMPLES / "order_good.txt", tmp / "out", keys, db, log)
    header, rows = read_output_file(tmp / "out" / "MNO-NORTH_B26101901.out")
    assert len(rows) == 500 == summary["quantity"]
    assert summary["iccid_first"] == rows[0]["ICCID"] and summary["iccid_last"] == rows[-1]["ICCID"]
    # the operator can decrypt KI with the agreed transport key and gets a real 16-byte key
    assert len(decrypt_from_transport(keys.transport_key("TK01"), rows[0]["KI"])) == 16
    assert qc_output_file(tmp / "out" / "MNO-NORTH_B26101901.out", keys) == []
    assert db.get("ORD-20261019-0001")[1] == "DELIVERED"
    assert [e["event"] for e in events(tmp)] == ["order_received", "generation_started", "order_completed"]


def test_no_secrets_in_logs_or_summary(env):
    tmp, keys, db, log = env
    process_order(SAMPLES / "order_good.txt", tmp / "out", keys, db, log)
    _, rows = read_output_file(tmp / "out" / "MNO-NORTH_B26101901.out")
    text = (tmp / "events.log").read_text() + (tmp / "out" / "MNO-NORTH_B26101901.summary.json").read_text()
    assert rows[0]["KI"] not in text and rows[0]["PUK1"] not in text
    with pytest.raises(ValueError):
        log.emit("oops", ki="00")



def test_worker_events_also_go_to_stdout(tmp_path, capsys):
    log = EventLog(str(tmp_path / "events.log"), echo=True)
    log.emit("worker_started", worker="w1")
    printed = capsys.readouterr().out
    assert json.loads(printed)["event"] == "worker_started"
    assert printed.strip() == (tmp_path / "events.log").read_text().strip()

def test_duplicate_order_is_refused(env):
    tmp, keys, db, log = env
    process_order(SAMPLES / "order_good.txt", tmp / "out", keys, db, log)
    with pytest.raises(InputFileError) as e:
        process_order(SAMPLES / "order_good.txt", tmp / "out", keys, db, log)
    assert e.value.code == "E_DUPLICATE_ORDER"


def test_overlapping_range_is_caught_before_generation(env):
    tmp, keys, db, log = env
    process_order(SAMPLES / "order_good.txt", tmp / "out", keys, db, log)
    with pytest.raises(InputFileError) as e:
        process_order(SAMPLES / "order_overlap.txt", tmp / "out", keys, db, log)
    assert e.value.code == "E_RANGE_OVERLAP"
    assert not (tmp / "out" / "MNO-NORTH_B26101902.out").exists()
    assert db.get("ORD-20261019-0002")[1] == "FAILED"


def test_unknown_transport_key_fails_cleanly(env):
    tmp, keys, db, log = env
    with pytest.raises(InputFileError) as e:
        process_order(SAMPLES / "order_unknown_key.txt", tmp / "out", keys, db, log)
    assert e.value.code == "E_UNKNOWN_KEY"


def test_failed_order_can_be_resubmitted(env):
    tmp, keys, db, log = env
    with pytest.raises(InputFileError):
        process_order(SAMPLES / "order_unknown_key.txt", tmp / "out", keys, db, log)
    fixed = (SAMPLES / "order_unknown_key.txt").read_text().replace("TK99", "TK01")
    (tmp / "fixed.txt").write_text(fixed)
    process_order(tmp / "fixed.txt", tmp / "out", keys, db, log)
    assert db.get("ORD-20261019-0005")[1] == "DELIVERED"



def test_range_is_reserved_while_another_order_is_generating(env):
    # Worker A has reserved ORD-0001's ranges and is still generating when worker B
    # picks up an overlapping order: B must be refused, not wait for A to finish.
    tmp, keys, db, log = env
    order = parse_input_file(SAMPLES / "order_good.txt")
    db.receive(order, "2026-10-20T00:00:00Z")
    iccids = list(iccid_range(order.iccid_start, order.quantity))
    assert db.reserve(order.order_id, [("ICCID", iccids[0], iccids[-1])]) is None
    with pytest.raises(InputFileError) as e:
        process_order(SAMPLES / "order_overlap.txt", tmp / "out", keys, db, log)
    assert e.value.code == "E_RANGE_OVERLAP" and order.order_id in e.value.message


def test_parallel_reservations_of_one_range_let_exactly_one_win(env):
    tmp, keys, db, log = env
    order = parse_input_file(SAMPLES / "order_good.txt")
    ids = [f"ORD-RACE-{n}" for n in range(6)]
    for order_id in ids:
        db.receive(replace(order, order_id=order_id), "2026-10-20T00:00:00Z")
    start = threading.Barrier(len(ids))
    results = {}

    def worker(order_id):
        own = OrderDB(str(tmp / "orders.db"))  # each worker has its own connection
        start.wait()
        results[order_id] = own.reserve(order_id, [("IMSI", "001010000000001", "001010000001000")])
        own.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [i for i, clash in results.items() if clash is None]
    assert len(winners) == 1
    assert all(clash[3] == winners[0] for clash in results.values() if clash)


def test_failed_order_releases_its_ranges(env):
    tmp, keys, db, log = env
    with pytest.raises(InputFileError):
        process_order(SAMPLES / "order_unknown_key.txt", tmp / "out", keys, db, log)
    assert db.execute("SELECT COUNT(*) FROM allocations").fetchone()[0] == 0
    # a new order (different Order_ID) can now use the same numbers
    fixed = (SAMPLES / "order_unknown_key.txt").read_text().replace("TK99", "TK01")
    (tmp / "new.txt").write_text(fixed.replace("ORD-20261019-0005", "ORD-20261019-0009"))
    process_order(tmp / "new.txt", tmp / "out", keys, db, log)
    assert db.get("ORD-20261019-0009")[1] == "DELIVERED"

def test_qc_catches_a_tampered_file(env):
    tmp, keys, db, log = env
    process_order(SAMPLES / "order_good.txt", tmp / "out", keys, db, log)
    out = tmp / "out" / "MNO-NORTH_B26101901.out"
    lines = out.read_text().splitlines()
    first_row = next(i for i, ln in enumerate(lines) if ln.startswith("89"))
    lines.insert(first_row + 1, lines[first_row])  # duplicate a card
    out.write_text("\n".join(lines) + "\n")
    problems = qc_output_file(out, keys)
    assert any("duplicate ICCID" in p for p in problems)
    assert any("row count" in p for p in problems)


# --- watcher ---------------------------------------------------------------

def test_watcher_routes_good_and_bad_files(env):
    tmp, keys, db, log = env
    root = tmp / "data"
    ensure_folders(root)
    shutil.copy(SAMPLES / "order_good.txt", root / "inbox")
    shutil.copy(SAMPLES / "order_truncated.txt", root / "inbox")
    (root / "inbox" / "upload.txt.part").write_text("still uploading")
    assert run_once(root, keys, db, log, "w1") == 2
    assert (root / "archive" / "order_good.txt").exists()
    assert (root / "error" / "order_truncated.txt").exists()
    assert "E_TRUNCATED" in (root / "error" / "order_truncated.txt.reason").read_text()
    assert (root / "outbox" / "MNO-NORTH_B26101901.out").exists()
    assert (root / "inbox" / "upload.txt.part").exists()  # partial uploads are left alone


def test_stale_claims_are_requeued(env):
    tmp, keys, db, log = env
    root = tmp / "data"
    ensure_folders(root)
    claim = root / "processing" / "order_good.txt@dead-worker"
    shutil.copy(SAMPLES / "order_good.txt", claim)
    old = time.time() - 3600
    os.utime(claim, (old, old))
    assert requeue_stale(root, 600, log) == 1
    assert (root / "inbox" / "order_good.txt").exists()


# --- cli -------------------------------------------------------------------

def test_cli_generate_and_status(tmp_path, capsys):
    keys = str(tmp_path / "k.json")
    assert main(["init-keys", keys]) == 0
    common = ["--keys", keys, "--db", str(tmp_path / "o.db"), "--log", str(tmp_path / "e.log")]
    assert main(["generate", str(SAMPLES / "order_good.txt"), "--out", str(tmp_path / "out"), *common]) == 0
    assert main(["generate", str(SAMPLES / "order_overlap.txt"), "--out", str(tmp_path / "out"), *common]) == 1
    assert main(["status", "--db", str(tmp_path / "o.db")]) == 0
    out = capsys.readouterr().out
    assert "DELIVERED" in out and "E_RANGE_OVERLAP" in out


def test_unexpected_error_parks_file_and_stops_worker(env, monkeypatch):
    tmp, keys, db, log = env
    root = tmp / "data"
    ensure_folders(root)
    shutil.copy(SAMPLES / "order_good.txt", root / "inbox")

    def broken(*args, **kwargs):
        raise ConnectionError("database unreachable")

    monkeypatch.setattr("simgen.watcher.process_order", broken)
    with pytest.raises(ConnectionError):
        run_once(root, keys, db, log, "w1")
    assert (root / "error" / "order_good.txt").exists()
    assert "E_INTERNAL" in (root / "error" / "order_good.txt.reason").read_text()
    assert [e["event"] for e in events(tmp)][-1] == "worker_error"
