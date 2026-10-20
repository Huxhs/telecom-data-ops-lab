"""997 and 855 acknowledgments."""

from datetime import datetime

from conftest import parse_ok, read_sample
from edi_bridge import ack855, ack997, m850, x12
from edi_bridge.envelope import ControlNumbers

WHEN = datetime(2026, 10, 19, 15, 30)


def body_997(text: str) -> list[list[str]]:
    ic, errors = x12.read_x12(text)
    bodies = ack997.build_997(ic, errors)
    assert len(bodies) == 1
    return bodies[0]


def rows(ts: x12.TransactionSet, tag: str) -> list[list[str]]:
    return [s.elements for s in ts.segments if s.tag == tag]


# --- 997 -------------------------------------------------------------------------

def test_997_accepts_good_file(good):
    assert body_997(good) == [
        ["AK1", "PO", "101"],
        ["AK2", "850", "0001"],
        ["AK5", "A"],
        ["AK9", "A", "1", "1", "1"],
    ]


def test_997_rejects_bad_control_numbers_with_ak5_codes():
    body = body_997(read_sample("850_bad_control.x12"))
    assert ["AK5", "R", "3", "4"] in body  # 3 = ST/SE control mismatch, 4 = count wrong
    assert body[-1] == ["AK9", "R", "1", "1", "0"]


def test_997_reports_missing_segment_with_ak3():
    body = body_997(read_sample("850_missing_beg.x12"))
    assert ["AK3", "BEG", "7", "", "3"] in body  # 3 = mandatory segment missing
    assert ["AK5", "R", "5"] in body
    assert body[-1][:2] == ["AK9", "R"]


def test_997_reports_element_error_with_ak4(good):
    body = body_997(good.replace("PO1*1*500*", "PO1*1*5O0*"))
    i = body.index(["AK3", "PO1", "5", "", "8"])
    assert body[i + 1] == ["AK4", "2", "330", "6", "5O0"]  # element 330 = quantity ordered


def test_997_partial_acceptance_counts():
    text = read_sample("850_multi.x12").replace("SE*7*0002", "SE*6*0002")
    body = body_997(text)
    assert [r for r in body if r[0] == "AK5"] == [["AK5", "A"], ["AK5", "R", "4"]]
    assert body[-1] == ["AK9", "P", "2", "2", "1"]


def test_997_group_level_error_rejects_group(good):
    body = body_997(good.replace("GE*1*101", "GE*2*101"))
    assert ["AK5", "A"] in body  # the set itself was fine
    assert body[-1] == ["AK9", "R", "2", "1", "1", "5"]  # 5 = set count does not match


def test_997_interchange_error_rejects_every_group(good):
    body = body_997(good.replace("IEA*1*000000101", "IEA*1*000000999"))
    assert body[-1][:2] == ["AK9", "R"]


def test_997_envelope_is_swapped_and_valid(good, tmp_path):
    ic, errors = x12.read_x12(good)
    text = ack997.make_997(ic, errors, ControlNumbers(tmp_path / "c.json"), WHEN)
    reply = parse_ok(text)
    assert (reply.sender_id, reply.receiver_id) == ("GDMOBILE", "MNONORTH")
    assert reply.groups[0].functional_id == "FA"
    assert reply.groups[0].gs.get(2) == "GDMOBILE"
    ts = reply.groups[0].transactions[0]
    assert ts.set_id == "997"
    assert rows(ts, "AK1") == [["PO", "101"]]


def test_997_for_bad_file_is_still_valid_x12(tmp_path):
    ic, errors = x12.read_x12(read_sample("850_bad_control.x12"))
    reply = parse_ok(ack997.make_997(ic, errors, ControlNumbers(tmp_path / "c.json"), WHEN))
    assert rows(reply.groups[0].transactions[0], "AK5") == [["R", "3", "4"]]


# --- 855 -------------------------------------------------------------------------

def order_from(text: str) -> tuple[m850.Order, x12.Interchange]:
    ic = parse_ok(text)
    return m850.map_850(ic.groups[0].transactions[0], ic), ic


def test_855_accepts_all_lines(good):
    order, _ = order_from(good)
    assert ack855.build_855(order, set()) == [
        ["BAK", "00", "AC", "PO45001", "20261019"],
        ["PO1", "1", "500", "EA", "0.85", "", "VP", "UICC-TRIO-128K", "BP", "MN-SIM-0128"],
        ["ACK", "IA", "500", "EA", "067", "20261102"],
        ["PO1", "2", "1000", "EA", "0.40", "", "VP", "ESIM-PROF-A", "BP", "MN-ESIM-A01"],
        ["ACK", "IA", "1000", "EA", "067", "20261102"],
        ["CTT", "2"],
    ]


def test_855_rejects_when_a_line_fails():
    order, _ = order_from(read_sample("850_unknown_item.x12"))
    body = ack855.build_855(order, {"2"})
    assert body[0][:3] == ["BAK", "00", "RJ"]
    assert [r[:2] for r in body if r[0] == "ACK"] == [["ACK", "IA"], ["ACK", "IR"]]
    assert ["ACK", "IR", "250", "EA"] in body


def test_855_file_is_valid_and_addressed_back(good, tmp_path):
    order, ic = order_from(good)
    text = ack855.make_855([(order, set())], ic, ControlNumbers(tmp_path / "c.json"), WHEN)
    reply = parse_ok(text)  # counts and control numbers are right
    assert (reply.sender_id, reply.receiver_id) == ("GDMOBILE", "MNONORTH")
    ts = reply.groups[0].transactions[0]
    assert (reply.groups[0].functional_id, ts.set_id) == ("PR", "855")
    assert ts.se.get(1) == "8"
