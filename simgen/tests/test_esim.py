import csv
import json
import re
from pathlib import Path

import pytest
from simgen.db import OrderDB
from simgen.events import EventLog
from simgen.generator import process_order, qc_activation_file, qc_output_file, read_output_file
from simgen.inputfile import InputFileError, parse_input_file
from simgen.keys import KeyStore, compute_opc, decrypt_from_transport, write_lab_keys

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
BASE = "MNO-NORTH_E26101908"
CODE = re.compile(r"LPA:1\$smdp\.lab\.example\$[A-Z0-9]{5}(-[A-Z0-9]{5}){3}")


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


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


# --- input file ------------------------------------------------------------

def test_parse_esim_order():
    order = parse_input_file(SAMPLES / "order_esim.txt")
    assert order.is_esim and order.profile_kind == "esim"
    assert order.smdp_address == "smdp.lab.example" and order.quantity == 1000
    assert parse_input_file(SAMPLES / "order_good.txt").profile_kind == "uicc"


def test_esim_order_needs_smdp_address(tmp_path):
    bad = (SAMPLES / "order_esim.txt").read_text().replace("SMDP_Address: smdp.lab.example\n", "")
    (tmp_path / "bad.txt").write_text(bad)
    with pytest.raises(InputFileError) as e:
        parse_input_file(tmp_path / "bad.txt")
    assert e.value.code == "E_MISSING_FIELD" and "SMDP_Address" in e.value.message


@pytest.mark.parametrize("address", ["smdp", "smdp lab.example", "smdp.lab.example$x", "-smdp.lab.example"])
def test_smdp_address_must_be_a_hostname(tmp_path, address):
    bad = (SAMPLES / "order_esim.txt").read_text().replace("smdp.lab.example", address)
    (tmp_path / "bad.txt").write_text(bad)
    with pytest.raises(InputFileError) as e:
        parse_input_file(tmp_path / "bad.txt")
    assert e.value.code == "E_BAD_VALUE"


# --- pipeline --------------------------------------------------------------

def test_esim_order_end_to_end(env):
    tmp, keys, db, log = env
    summary = process_order(SAMPLES / "order_esim.txt", tmp / "out", keys, db, log)
    out = tmp / "out"
    assert sorted(p.name for p in out.iterdir()) == [
        f"{BASE}.activation.csv", f"{BASE}.out", f"{BASE}.smdp.csv", f"{BASE}.summary.json"]
    _, rows = read_output_file(out / f"{BASE}.out")
    smdp = read_csv(out / f"{BASE}.smdp.csv")
    activation = read_csv(out / f"{BASE}.activation.csv")
    assert len(rows) == len(smdp) == len(activation) == 1000 == summary["quantity"]
    assert list(activation[0]) == ["ICCID", "MATCHING_ID", "ACTIVATION_CODE"]
    assert all(CODE.fullmatch(a["ACTIVATION_CODE"]) for a in activation)
    assert all(a["ACTIVATION_CODE"].endswith("$" + a["MATCHING_ID"]) for a in activation)
    assert len({a["MATCHING_ID"] for a in activation}) == 1000
    # the SM-DP+ file pairs each card with the same MatchingID the operator got
    assert [(s["ICCID"], s["MATCHING_ID"]) for s in smdp] == [(a["ICCID"], a["MATCHING_ID"]) for a in activation]
    # and its KI/OPC are encrypted like the perso file: perso key, OPc derived from Ki
    ki = decrypt_from_transport(keys.perso_key, smdp[0]["KI_PERSO"])
    assert compute_opc(ki, keys.op) == decrypt_from_transport(keys.perso_key, smdp[0]["OPC_PERSO"])
    assert ki == decrypt_from_transport(keys.transport_key("TK01"), rows[0]["KI"])
    # and the downloaded profile gets the same PINs and PUKs the operator was sent
    pins = ("PIN1", "PUK1", "PIN2", "PUK2", "ADM1")
    assert [[s[c] for c in pins] for s in smdp] == [[r[c] for c in pins] for r in rows]
    assert qc_output_file(out / f"{BASE}.out", keys) == []
    assert qc_activation_file(out / f"{BASE}.out", out / f"{BASE}.activation.csv", "smdp.lab.example") == []
    assert db.get("ORD-20261019-0008")[1] == "DELIVERED"


def test_qc_catches_duplicate_matching_id_and_wrong_address(env):
    tmp, keys, db, log = env
    process_order(SAMPLES / "order_esim.txt", tmp / "out", keys, db, log)
    out, act = tmp / "out" / f"{BASE}.out", tmp / "out" / f"{BASE}.activation.csv"
    rows = read_csv(act)
    rows[1]["MATCHING_ID"], rows[1]["ACTIVATION_CODE"] = rows[0]["MATCHING_ID"], rows[0]["ACTIVATION_CODE"]
    rows[2]["ACTIVATION_CODE"] = rows[2]["ACTIVATION_CODE"].replace("smdp.lab.example", "smdp.evil.example")
    write_csv(act, rows)
    problems = qc_activation_file(out, act, "smdp.lab.example")
    assert any("duplicate MatchingID" in p for p in problems)
    assert any("smdp.evil.example" in p for p in problems)
    assert not any(rows[0]["MATCHING_ID"] in p for p in problems)  # problems are logged


def test_qc_catches_iccids_that_differ_from_the_output_file(env):
    tmp, keys, db, log = env
    process_order(SAMPLES / "order_esim.txt", tmp / "out", keys, db, log)
    out, act = tmp / "out" / f"{BASE}.out", tmp / "out" / f"{BASE}.activation.csv"
    write_csv(act, read_csv(act)[1:])  # one card lost
    assert any("ICCIDs" in p for p in qc_activation_file(out, act, "smdp.lab.example"))


def test_repeated_matching_id_rejects_the_order(env, monkeypatch):
    tmp, keys, db, log = env
    monkeypatch.setattr("simgen.generator.matching_id", lambda: "AAAAA-BBBBB-CCCCC-DDDDD")
    with pytest.raises(InputFileError) as e:
        process_order(SAMPLES / "order_esim.txt", tmp / "out", keys, db, log)
    assert e.value.code == "E_QC"
    assert sorted(p.name for p in (tmp / "out").iterdir()) == [
        f"{BASE}.activation.csv.rejected", f"{BASE}.out.rejected", f"{BASE}.smdp.csv.rejected"]
    assert db.get("ORD-20261019-0008")[1] == "FAILED"
    assert "AAAAA-BBBBB" not in (tmp / "events.log").read_text()


def test_profile_kind_in_summary_and_event_and_no_secrets(env):
    tmp, keys, db, log = env
    esim = process_order(SAMPLES / "order_esim.txt", tmp / "out", keys, db, log)
    uicc = process_order(SAMPLES / "order_good.txt", tmp / "out", keys, db, log)
    assert (esim["profile_kind"], uicc["profile_kind"]) == ("esim", "uicc")
    received = [e for e in events(tmp) if e["event"] == "order_received"]
    assert [e["profile_kind"] for e in received] == ["esim", "uicc"]
    assert not (tmp / "out" / f"{BASE}.perso.csv").exists()
    assert (tmp / "out" / "MNO-NORTH_B26101901.perso.csv").exists()
    _, rows = read_output_file(tmp / "out" / f"{BASE}.out")
    smdp = read_csv(tmp / "out" / f"{BASE}.smdp.csv")
    text = (tmp / "events.log").read_text() + (tmp / "out" / f"{BASE}.summary.json").read_text()
    for secret in (rows[0]["KI"], rows[0]["OPC"], rows[0]["PUK1"], rows[0]["ADM1"],
                   smdp[0]["KI_PERSO"], smdp[0]["MATCHING_ID"], "LPA:1$"):
        assert secret not in text
