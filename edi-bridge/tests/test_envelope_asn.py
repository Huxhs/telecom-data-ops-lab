"""Outbound envelopes, control numbers and the 856 ship notice."""

import json
from datetime import datetime

import pytest
from conftest import SAMPLES, parse_ok
from edi_bridge import asn856, envelope
from edi_bridge.envelope import ControlNumbers, Party
from edi_bridge.x12 import Delimiters

WHEN = datetime(2026, 10, 19, 15, 30)
US, THEM = Party("GDMOBILE"), Party("MNONORTH")


# --- envelope ----------------------------------------------------------------------

def test_isa_is_exactly_106_characters():
    isa = envelope.build_isa(US, THEM, 1, WHEN)
    assert len(isa) == 106
    assert isa[3] == "*" and isa[104] == ">" and isa[105] == "~"
    assert isa.split("*")[13] == "000000001"


def test_isa_stays_106_with_other_delimiters_and_long_ids():
    d = Delimiters(element="|", component="^", segment="\n")
    isa = envelope.build_isa(Party("A" * 15, "01"), Party("B"), 123456789, WHEN, "P", d)
    assert len(isa) == 106 and isa.endswith("^\n")


def test_isa_rejects_ids_longer_than_15():
    with pytest.raises(ValueError):
        envelope.build_isa(Party("X" * 16), THEM, 1, WHEN)


def test_generated_file_starts_with_a_106_character_isa(tmp_path):
    text = envelope.build_interchange("PR", [("855", [["BAK", "00", "AC", "PO1", "20261019"]])],
                                      US, THEM, ControlNumbers(tmp_path / "c.json"), WHEN)
    assert len(text.splitlines()[0]) == 106
    parse_ok(text)


def test_control_numbers_increment_across_runs(tmp_path):
    path = tmp_path / ".edi_control.json"
    sets = [("997", [["AK1", "PO", "1"], ["AK9", "A", "0", "0", "0"]])]
    first = parse_ok(envelope.build_interchange("FA", sets, US, THEM, ControlNumbers(path), WHEN))
    # A brand-new ControlNumbers object stands for the next run of the program.
    second = parse_ok(envelope.build_interchange("FA", sets, US, THEM, ControlNumbers(path), WHEN))
    assert first.control_number == "000000001" and second.control_number == "000000002"
    assert (first.groups[0].control_number, second.groups[0].control_number) == ("1", "2")
    assert json.loads(path.read_text()) == {"interchange": 2, "group": 2}


def test_st_numbers_restart_in_each_group(tmp_path):
    sets = [("855", [["BAK", "00", "AC", "A", "20261019"]]), ("855", [["BAK", "00", "AC", "B", "20261019"]])]
    ic = parse_ok(envelope.build_interchange("PR", sets, US, THEM, ControlNumbers(tmp_path / "c"), WHEN))
    assert [ts.control_number for ts in ic.groups[0].transactions] == ["0001", "0002"]
    assert ic.groups[0].ge.get(1) == "2"


def test_segment_drops_trailing_empty_elements():
    assert envelope.segment(["ACK", "IR", "250", "EA", "", ""], Delimiters()) == "ACK*IR*250*EA~"
    assert envelope.segment(["PO1", "1", "", "EA"], Delimiters()) == "PO1*1**EA~"


def test_segment_refuses_values_containing_delimiters():
    with pytest.raises(ValueError):
        envelope.segment(["N1", "ST", "Smith*Sons"], Delimiters())


# --- 856 -------------------------------------------------------------------------

@pytest.fixture
def summary() -> dict:
    return asn856.load_summary(SAMPLES / "shipment_summary.json")


def make_asn(summary, tmp_path, **options):
    text = asn856.make_856(summary, US, THEM, ControlNumbers(tmp_path / "c.json"), WHEN, **options)
    ic = parse_ok(text)  # envelope, SE01 and control numbers are all correct
    return ic.groups[0].transactions[0]


def test_856_hl_hierarchy(summary):
    hls = [row for row in asn856.build_856(summary) if row[0] == "HL"]
    assert hls == [["HL", "1", "", "S"], ["HL", "2", "1", "O"], ["HL", "3", "2", "I"]]
    ids = set()
    for _, hl_id, parent, _level in hls:  # every parent is an HL that came earlier
        assert parent == "" or parent in ids
        ids.add(hl_id)


def test_856_levels_hold_the_right_segments(summary):
    body = asn856.build_856(summary)
    tags = [row[0] for row in body]
    shipment = tags[tags.index("HL"): tags.index("HL", 2)]
    assert shipment == ["HL", "TD1", "TD5", "REF", "DTM", "N1"]
    assert ["PRF", "PO45001"] in body
    assert ["LIN", "", "VP", "UICC-TRIO-128K"] in body
    assert ["SN1", "", "500", "EA"] in body
    assert ["REF", "BT", "B26101901"] in body
    assert ["REF", "SE", "8900101000000000019", "FIRST ICCID"] in body
    assert ["REF", "SE", "8900101000000005000", "LAST ICCID"] in body
    assert ["BSN", "00", "SHB26101901", "20261019", "1400"] in body
    assert ["DTM", "011", "20261019"] in body


def test_856_segment_count_and_ctt(summary, tmp_path):
    ts = make_asn(summary, tmp_path)
    assert ts.set_id == "856"
    # ST BSN HL TD1 TD5 REF DTM N1 HL PRF HL LIN SN1 REF REF REF CTT SE
    assert int(ts.se.get(1)) == len(ts.segments) == 18
    assert ts.find("CTT").get(1) == "3"


def test_856_options(summary, tmp_path):
    ts = make_asn(summary, tmp_path, carrier="ABCD", bol="BOL-77",
                  ship_to_name="North DC", ship_to_id="NDC01")
    assert ts.find("TD5").elements == ["", "2", "ABCD"]
    assert ts.find("REF").elements == ["BM", "BOL-77"]
    assert ts.find("N1").elements == ["ST", "North DC", "92", "NDC01"]


def test_856_summary_with_missing_key_is_reported(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"order_id": "X", "quantity": 5}))
    with pytest.raises(ValueError, match="iccid_first"):
        asn856.load_summary(path)
