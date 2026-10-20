import pytest
from conftest import codes, parse_ok, read_sample
from edi_bridge import x12

# --- delimiter detection -----------------------------------------------------

def test_detects_default_delimiters(good):
    d = x12.detect_delimiters(good)
    assert (d.element, d.component, d.segment) == ("*", ">", "~")


def test_detects_non_default_delimiters(good):
    text = good.replace("~\n", "!").replace("*", "|").replace(">", "^")
    d = x12.detect_delimiters(text)
    assert (d.element, d.component, d.segment) == ("|", "^", "!")
    ic = parse_ok(text)
    assert ic.groups[0].transactions[0].find("BEG").get(3) == "PO45001"


def test_newline_as_segment_terminator(good):
    text = good.replace("~\n", "\n")
    assert x12.detect_delimiters(text).segment == "\n"
    assert len(parse_ok(text).groups[0].transactions[0].segments) == 8


@pytest.mark.parametrize("variant", ["", "\r\n", "\n\n"])
def test_tolerates_line_breaks_after_terminators(good, variant):
    text = good.replace("~\n", "~" + variant)
    assert len(parse_ok(text).groups[0].transactions[0].segments) == 8


# --- the envelope tree -------------------------------------------------------

def test_parse_builds_envelope_tree(good):
    ic = parse_ok(good)
    assert (ic.sender_id, ic.receiver_id, ic.control_number, ic.usage) == (
        "MNONORTH", "GDMOBILE", "000000101", "T")
    group = ic.groups[0]
    assert (group.functional_id, group.control_number) == ("PO", "101")
    ts = group.transactions[0]
    assert (ts.set_id, ts.control_number) == ("850", "0001")
    assert [s.tag for s in ts.segments] == ["ST", "BEG", "DTM", "N1", "PO1", "PO1", "CTT", "SE"]
    assert ts.segments[1].position == 4  # positions count from ISA = 1
    assert ts.position_in_set(ts.segments[1]) == 2


def test_multi_sample_has_two_transaction_sets():
    ic = parse_ok(read_sample("850_multi.x12"))
    assert [ts.control_number for ts in ic.groups[0].transactions] == ["0001", "0002"]


def test_segment_get_returns_empty_for_missing_element(good):
    beg = parse_ok(good).groups[0].transactions[0].find("BEG")
    assert beg.get(4) == "" and beg.get(99) == ""


# --- envelope validation: one test per error type -----------------------------

@pytest.mark.parametrize("old, new, expected", [
    ("IEA*1*000000101", "IEA*1*000000999", "ISA_IEA_CONTROL_MISMATCH"),
    ("IEA*1*000000101", "IEA*2*000000101", "IEA_GROUP_COUNT"),
    ("GE*1*101", "GE*1*999", "GS_GE_CONTROL_MISMATCH"),
    ("GE*1*101", "GE*3*101", "GE_SET_COUNT"),
    ("SE*8*0001", "SE*8*0002", "ST_SE_CONTROL_MISMATCH"),
    ("SE*8*0001", "SE*9*0001", "SE_SEGMENT_COUNT"),
    ("SE*8*0001", "SE*7*0001", "SE_SEGMENT_COUNT"),
    ("PO1*1*500*", "PO1*1*5O0*", "INVALID_NUMBER"),
    ("BEG*00*SA*PO45001**", "BEG*00*SA***", "REQUIRED_ELEMENT_MISSING"),
])
def test_each_envelope_error_is_caught_alone(good, old, new, expected):
    _, errors = x12.read_x12(good.replace(old, new))
    assert codes(errors) == [expected]


def test_control_numbers_compare_numerically_but_st_se_exactly(good):
    _, errors = x12.read_x12(good.replace("GE*1*101", "GE*1*0101"))
    assert errors == []  # GS06/GE02 are numbers: 101 == 0101
    _, errors = x12.read_x12(good.replace("SE*8*0001", "SE*8*1"))
    assert codes(errors) == ["ST_SE_CONTROL_MISMATCH"]  # ST02/SE02 are text


def test_bad_control_sample_fails_for_the_stated_reasons():
    _, errors = x12.read_x12(read_sample("850_bad_control.x12"))
    assert codes(errors) == ["ST_SE_CONTROL_MISMATCH", "SE_SEGMENT_COUNT"]
    assert all(e.position == 9 and e.segment == "SE" for e in errors)
    assert (errors[0].group, errors[0].transaction) == ("102", "0001")


def test_missing_beg_sample():
    _, errors = x12.read_x12(read_sample("850_missing_beg.x12"))
    assert codes(errors) == ["REQUIRED_SEGMENT_MISSING"]
    assert errors[0].segment == "BEG"
    assert errors[0].set_position == 7  # reported at SE, where the gap was noticed


def test_missing_po1_is_reported(good):
    lines = [ln for ln in good.splitlines() if not ln.startswith("PO1")]
    text = "\n".join(lines).replace("SE*8*0001", "SE*6*0001")
    _, errors = x12.read_x12(text)
    assert codes(errors) == ["REQUIRED_SEGMENT_MISSING"]
    assert errors[0].segment == "PO1"


def test_invalid_number_carries_element_detail(good):
    _, errors = x12.read_x12(good.replace("PO1*1*500*", "PO1*1*5O0*"))
    e = errors[0]
    assert (e.segment, e.position, e.set_position, e.element, e.value) == ("PO1", 7, 5, 2, "5O0")


@pytest.mark.parametrize("line, expected", [
    ("SE*8*0001~\n", "SE_MISSING"),
    ("GE*1*101~\n", "GE_MISSING"),
    ("IEA*1*000000101~\n", "IEA_MISSING"),
])
def test_missing_trailers(good, line, expected):
    _, errors = x12.read_x12(good.replace(line, ""))
    assert expected in codes(errors)


def test_segment_after_iea_is_unexpected(good):
    _, errors = x12.read_x12(good + "GS*PO*X*Y*20261019*0930*7*X*004010~\n")
    assert codes(errors) == ["UNEXPECTED_SEGMENT"]


def test_data_segment_outside_transaction_set(good):
    _, errors = x12.read_x12(good.replace("GE*1*101~", "REF*ZZ*stray~\nGE*1*101~"))
    assert codes(errors) == ["UNEXPECTED_SEGMENT"]
    assert errors[0].group == "101" and errors[0].transaction is None


@pytest.mark.parametrize("text", ["", "hello world", "ISA*00*short~", "\x00\xff" * 80])
def test_garbage_never_crashes(text):
    ic, errors = x12.read_x12(text)
    assert ic is None and codes(errors) == ["NO_ISA"]


def test_isa_with_wrong_element_count():
    text = "ISA*" + "x" * 100 + ">~GS*PO~"  # 106 characters, but only 1 element
    ic, errors = x12.read_x12(text)
    assert ic is None and codes(errors) == ["ISA_MALFORMED"]


def test_truncated_file_reports_missing_trailers(good):
    ic, errors = x12.read_x12(good[: good.index("CTT")])
    assert ic is not None
    assert codes(errors) == ["SE_MISSING", "GE_MISSING", "IEA_MISSING"]


def test_good_samples_are_valid():
    for name in ("850_good.x12", "850_unknown_item.x12", "850_multi.x12"):
        parse_ok(read_sample(name))
