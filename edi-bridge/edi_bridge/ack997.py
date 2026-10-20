"""Generate a 997 Functional Acknowledgment for a received interchange.

A 997 answers one question: "did your file arrive and is its syntax right?"
It says nothing about the business content (prices, items): that is the 855's
job. One 997 transaction set is sent back per functional group received:

    AK1  which group we are answering (GS01 + GS06)
    AK2  which transaction set (ST01 + ST02)        one per set
    AK3  a segment in error  (+ AK4 for the element) when we know the detail
    AK5  verdict for that set: A accepted / R rejected (+ reason codes)
    AK9  verdict for the group: A / P partially / R, with counts
"""

from __future__ import annotations

from datetime import datetime

from .envelope import ControlNumbers, build_interchange, reply_parties
from .x12 import EdiError, Interchange, errors_for

# Our error codes -> the X12 code lists used inside the 997.
AK3_CODES = {  # AK304 segment syntax error
    "REQUIRED_SEGMENT_MISSING": "3",  # mandatory segment missing
    "REQUIRED_ELEMENT_MISSING": "8",  # segment has data element errors
    "INVALID_NUMBER": "8",
}
AK4_CODES = {  # AK403 element syntax error
    "REQUIRED_ELEMENT_MISSING": "1",  # mandatory data element missing
    "INVALID_NUMBER": "6",            # invalid character in data element
}
AK5_CODES = {  # AK502 transaction set syntax error; anything else -> "5"
    "SE_MISSING": "2",               # transaction set trailer missing
    "ST_SE_CONTROL_MISMATCH": "3",   # control numbers in ST and SE do not match
    "SE_SEGMENT_COUNT": "4",         # number of included segments does not match
}
AK9_CODES = {  # AK905 functional group syntax error
    "GE_MISSING": "3",               # functional group trailer missing
    "GS_GE_CONTROL_MISMATCH": "4",   # group control numbers do not agree
    "GE_SET_COUNT": "5",             # number of included sets does not match
}
# Data element reference numbers (AK402), from the X12 data dictionary.
ELEMENT_REFS = {("BEG", 1): "353", ("BEG", 2): "92", ("BEG", 3): "324", ("BEG", 5): "373",
                ("PO1", 2): "330", ("PO1", 3): "355", ("PO1", 4): "212"}


def build_997(ic: Interchange, errors: list[EdiError]) -> list[list[list[str]]]:
    """Return one 997 body (a list of segments) per functional group."""
    # Problems with the ISA/IEA envelope itself are normally answered with a TA1.
    # To keep the lab small we reject every group instead, so nothing is imported
    # silently and the partner knows the file must be resent.
    interchange_broken = any(e.group is None for e in errors)
    bodies = []
    for group in ic.groups:
        body = [["AK1", group.functional_id, group.control_number]]
        accepted = 0
        for ts in group.transactions:
            ts_errors = errors_for(errors, group, ts)
            body.append(["AK2", ts.set_id, ts.control_number])
            for e in ts_errors:
                if e.code in AK3_CODES:
                    body.append(["AK3", e.segment, str(e.set_position or ""), "", AK3_CODES[e.code]])
                if e.code in AK4_CODES:
                    body.append(["AK4", str(e.element), ELEMENT_REFS.get((e.segment, e.element), ""),
                                 AK4_CODES[e.code], e.value])
            if ts_errors:
                codes = list(dict.fromkeys(AK5_CODES.get(e.code, "5") for e in ts_errors))
                body.append(["AK5", "R", *codes[:5]])
            else:
                body.append(["AK5", "A"])
                accepted += 1

        group_errors = errors_for(errors, group)
        received = len(group.transactions)
        included = group.ge.get(1) if group.ge and group.ge.get(1).isdigit() else str(received)
        if group_errors or interchange_broken or accepted == 0:
            status = "R"  # AK904 still reports how many sets were fine on their own
        elif accepted < received:
            status = "P"
        else:
            status = "A"
        codes = list(dict.fromkeys(AK9_CODES[e.code] for e in group_errors if e.code in AK9_CODES))
        body.append(["AK9", status, included, str(received), str(accepted), *codes[:5]])
        bodies.append(body)
    return bodies


def make_997(ic: Interchange, errors: list[EdiError], counter: ControlNumbers,
             when: datetime | None = None) -> str:
    """The complete outbound 997 file, addressed back to whoever sent `ic`."""
    sender, receiver = reply_parties(ic)
    sets = [("997", body) for body in build_997(ic, errors)]
    return build_interchange("FA", sets, sender, receiver, counter, when, ic.usage or "T")
