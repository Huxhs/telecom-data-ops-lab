"""The pretty printer and the command line, end to end."""

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import SAMPLES, parse_ok, read_sample
from edi_bridge import pretty
from edi_bridge.cli import main

ROOT = Path(__file__).resolve().parent.parent


def sample(name: str) -> str:
    return str(SAMPLES / name)


def read(path: Path) -> str:
    return path.read_text(encoding="latin-1")


# --- pretty ------------------------------------------------------------------------

def test_pretty_labels_elements_and_codes(good):
    out = pretty.pretty(good)
    beg = next(line for line in out.splitlines() if "BEG01" in line)
    assert "BEG  BEG01=00 (Original)  BEG02=SA (Stand-alone order)  BEG03=PO45001 [PO number]" in beg
    assert "BEG05=20261019 [PO date]" in beg and "BEG04" not in beg
    assert "PO103=EA (Each)" in out and "PO106=VP (Vendor part)" in out
    assert "ISA15=T (Test)" in out


def test_pretty_shows_positions_and_nesting(good):
    lines = pretty.pretty(good).splitlines()
    assert lines[1].startswith("  1  ISA") and lines[2].startswith("  2    GS")
    assert lines[4].startswith("  4        BEG")


def test_pretty_knows_every_required_segment():
    for tag in ("ISA GS ST BEG DTM N1 PO1 CTT SE GE IEA BAK ACK BSN HL TD5 REF PRF LIN "
                "SN1 AK1 AK2 AK5 AK9").split():
        assert tag in pretty.SEGMENTS


def test_pretty_labels_unknown_segments_by_position(good):
    out = pretty.pretty(good.replace("CTT*2~", "XYZ*abc~"))
    assert "XYZ01=abc   # unknown segment" in out


def test_pretty_works_on_broken_files():
    assert "SE02=0002" in pretty.pretty(read_sample("850_bad_control.x12"))


# --- validate / pretty commands ----------------------------------------------------

def test_validate_good_file(capsys):
    assert main(["validate", sample("850_good.x12")]) == 0
    assert "OK" in capsys.readouterr().out


def test_validate_bad_file_prints_each_error(capsys):
    assert main(["validate", sample("850_bad_control.x12")]) == 1
    out = capsys.readouterr().out
    assert "ST_SE_CONTROL_MISMATCH" in out and "SE_SEGMENT_COUNT" in out


def test_pretty_command(capsys):
    assert main(["pretty", sample("850_good.x12")]) == 0
    assert "BEG01=00 (Original)" in capsys.readouterr().out


def test_unreadable_input_is_an_error_not_a_traceback(tmp_path, capsys):
    junk = tmp_path / "junk.x12"
    junk.write_text("not edi")
    assert main(["pretty", str(junk)]) == 1
    assert main(["validate", str(tmp_path / "missing.x12")]) == 1
    assert "error:" in capsys.readouterr().err


# --- inbound ---------------------------------------------------------------------

def test_inbound_good_file_end_to_end(tmp_path, capsys):
    assert main(["inbound", sample("850_good.x12"), "--out", str(tmp_path)]) == 0
    ack = parse_ok(read(tmp_path / "997.x12"))
    ack_set = ack.groups[0].transactions[0]
    assert ack_set.find("AK5").elements == ["A"]
    assert ack_set.find("AK9").elements == ["A", "1", "1", "1"]

    po_ack = parse_ok(read(tmp_path / "855.x12")).groups[0].transactions[0]
    assert po_ack.find("BAK").elements[:3] == ["00", "AC", "PO45001"]

    orders = json.loads((tmp_path / "orders.json").read_text())
    assert orders[0]["po_number"] == "PO45001" and len(orders[0]["lines"]) == 2
    erp = json.loads((tmp_path / "erp_orders.json").read_text())
    assert erp[0]["co_num"] == "EDIPO45001"
    with open(tmp_path / "co_lines.csv", newline="") as f:
        assert [r["item"] for r in csv.DictReader(f)] == ["UICC-TRIO-128K", "ESIM-PROF-A"]
    assert "EDIPO45001" in capsys.readouterr().out


def test_inbound_bad_control_still_writes_a_rejecting_997(tmp_path):
    assert main(["inbound", sample("850_bad_control.x12"), "--out", str(tmp_path)]) == 1
    ack = parse_ok(read(tmp_path / "997.x12")).groups[0].transactions[0]
    assert ack.find("AK5").elements == ["R", "3", "4"]
    assert ack.find("AK9").elements[0] == "R"
    for name in ("855.x12", "orders.json", "erp_orders.json", "co_header.csv"):
        assert not (tmp_path / name).exists()


def test_inbound_missing_beg(tmp_path):
    assert main(["inbound", sample("850_missing_beg.x12"), "--out", str(tmp_path)]) == 1
    assert "AK3*BEG*7**3~" in read(tmp_path / "997.x12")
    assert not (tmp_path / "erp_orders.json").exists()


def test_inbound_unknown_item_rejects_in_855_not_997(tmp_path, capsys):
    assert main(["inbound", sample("850_unknown_item.x12"), "--out", str(tmp_path)]) == 1
    assert "AK5*A~" in read(tmp_path / "997.x12")  # syntax was fine
    po_ack = read(tmp_path / "855.x12")
    assert "BAK*00*RJ*PO45002" in po_ack
    assert "ACK*IA*500*EA" in po_ack and "ACK*IR*250*EA" in po_ack
    assert not (tmp_path / "erp_orders.json").exists()
    assert "UNKNOWN_ITEM" in capsys.readouterr().out


def test_inbound_unknown_partner(tmp_path):
    partners = tmp_path / "partners.csv"
    partners.write_text("isa_sender_id,customer_code,name\nOTHER,OTHER,Other\n")
    out = tmp_path / "out"
    assert main(["inbound", sample("850_good.x12"), "--out", str(out),
                 "--partners", str(partners)]) == 1
    po_ack = read(out / "855.x12")
    assert "BAK*00*RJ" in po_ack and po_ack.count("ACK*IR") == 2


def test_inbound_multi_maps_both_orders(tmp_path):
    assert main(["inbound", sample("850_multi.x12"), "--out", str(tmp_path)]) == 0
    with open(tmp_path / "co_header.csv", newline="") as f:
        assert [r["cust_po"] for r in csv.DictReader(f)] == ["PO45003", "PO45004"]
    po_acks = parse_ok(read(tmp_path / "855.x12")).groups[0].transactions
    assert [ts.find("BAK").get(3) for ts in po_acks] == ["PO45003", "PO45004"]
    assert "AK9*A*2*2*2~" in read(tmp_path / "997.x12")


def test_inbound_control_numbers_increase_between_runs(tmp_path):
    main(["inbound", sample("850_good.x12"), "--out", str(tmp_path)])
    first = parse_ok(read(tmp_path / "997.x12")).control_number
    main(["inbound", sample("850_good.x12"), "--out", str(tmp_path)])
    second = parse_ok(read(tmp_path / "997.x12")).control_number
    assert int(second) > int(first)


def test_inbound_removes_stale_outputs(tmp_path):
    main(["inbound", sample("850_good.x12"), "--out", str(tmp_path)])
    main(["inbound", sample("850_bad_control.x12"), "--out", str(tmp_path)])
    assert not (tmp_path / "erp_orders.json").exists()


def test_inbound_garbage_file_writes_nothing(tmp_path):
    junk = tmp_path / "junk.x12"
    junk.write_text("this is not an interchange")
    assert main(["inbound", str(junk), "--out", str(tmp_path / "out")]) == 1
    assert not (tmp_path / "out" / "997.x12").exists()


# --- asn -------------------------------------------------------------------------

def test_asn_command_writes_valid_856(tmp_path):
    assert main(["asn", sample("shipment_summary.json"), "--out", str(tmp_path),
                 "--carrier", "ABCD"]) == 0
    ic = parse_ok(read(tmp_path / "856_B26101901.x12"))
    assert (ic.sender_id, ic.receiver_id) == ("GDMOBILE", "MNONORTH")
    assert ic.groups[0].transactions[0].find("TD5").get(3) == "ABCD"


def test_asn_command_with_bad_summary(tmp_path, capsys):
    bad = tmp_path / "s.json"
    bad.write_text("{}")
    assert main(["asn", str(bad), "--out", str(tmp_path)]) == 1
    assert "missing" in capsys.readouterr().err


# --- python -m edi_bridge --------------------------------------------------------

@pytest.mark.parametrize("name, code", [("850_good.x12", 0), ("850_missing_beg.x12", 1)])
def test_runs_as_module(name, code):
    result = subprocess.run([sys.executable, "-m", "edi_bridge", "validate", sample(name)],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == code
