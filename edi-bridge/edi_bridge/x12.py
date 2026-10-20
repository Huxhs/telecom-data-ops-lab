"""Parse and validate an ANSI X12 interchange.

An X12 file is a set of nested envelopes:

    ISA  interchange header ...... who sends to whom + interchange control number
      GS   functional group ...... what kind of documents (PO = purchase orders)
        ST   transaction set ..... one business document, e.g. one 850
          BEG, N1, PO1 ... ....... the document itself
        SE   set trailer ......... segment count + the ST control number again
      GE   group trailer ......... number of sets + the GS control number again
    IEA  interchange trailer ..... number of groups + the ISA control number again

Every trailer repeats its header's control number and counts what it closes.
That is how a receiver proves nothing was lost or cut off in transit, and it is
what `validate` checks. Problems come back as EdiError objects, not exceptions:
a translator has to report a bad file, never crash on it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ISA_LENGTH = 106  # the ISA is fixed width: 105 characters plus the segment terminator

# A tiny "implementation guide": what each transaction set must contain.
REQUIRED_SEGMENTS = {
    "850": ["BEG", "PO1"],
    "855": ["BAK"],
    "856": ["BSN", "HL"],
    "997": ["AK1", "AK9"],
}
# Elements that must have a value when their segment is present (BEG03 = PO number).
REQUIRED_ELEMENTS = {"BEG": [1, 2, 3, 5], "PO1": [2, 3]}
# Elements that must be numbers when present (PO102 quantity, PO104 unit price).
NUMERIC_ELEMENTS = {"PO1": [2, 4], "SN1": [2], "CTT": [1]}
_NUMBER = re.compile(r"-?(\d+(\.\d*)?|\.\d+)")


@dataclass
class Delimiters:
    element: str = "*"    # between elements:  BEG*00*SA
    component: str = ">"  # inside a composite element (ISA16)
    segment: str = "~"    # ends every segment


@dataclass
class Segment:
    tag: str             # segment ID, e.g. "BEG"
    elements: list[str]  # elements[0] is BEG01, elements[1] is BEG02 ...
    position: int        # 1-based position of the segment in the file

    def get(self, n: int) -> str:
        """Element n in X12 numbering (BEG03 -> get(3)); "" when it is absent."""
        return self.elements[n - 1] if 1 <= n <= len(self.elements) else ""


@dataclass
class TransactionSet:
    st: Segment
    segments: list[Segment] = field(default_factory=list)  # ST..SE inclusive
    se: Segment | None = None

    @property
    def set_id(self) -> str:
        return self.st.get(1)  # "850", "855" ...

    @property
    def control_number(self) -> str:
        return self.st.get(2)

    def find(self, tag: str) -> Segment | None:
        return next((s for s in self.segments if s.tag == tag), None)

    def position_in_set(self, seg: Segment) -> int:
        """Position counted from ST = 1, the numbering a 997 uses in AK302."""
        return seg.position - self.st.position + 1


@dataclass
class FunctionalGroup:
    gs: Segment
    transactions: list[TransactionSet] = field(default_factory=list)
    ge: Segment | None = None

    @property
    def functional_id(self) -> str:
        return self.gs.get(1)  # PO = 850s, PR = 855s, SH = 856s, FA = 997s

    @property
    def control_number(self) -> str:
        return self.gs.get(6)


@dataclass
class Interchange:
    isa: Segment
    delimiters: Delimiters
    groups: list[FunctionalGroup] = field(default_factory=list)
    iea: Segment | None = None

    # ISA06 and ISA08 are padded with spaces to 15 characters, hence strip().
    @property
    def sender_qualifier(self) -> str:
        return self.isa.get(5)

    @property
    def sender_id(self) -> str:
        return self.isa.get(6).strip()

    @property
    def receiver_qualifier(self) -> str:
        return self.isa.get(7)

    @property
    def receiver_id(self) -> str:
        return self.isa.get(8).strip()

    @property
    def control_number(self) -> str:
        return self.isa.get(13)

    @property
    def usage(self) -> str:
        return self.isa.get(15)  # T = test, P = production


@dataclass
class EdiError:
    code: str                        # stable machine code, e.g. "SE_SEGMENT_COUNT"
    message: str                     # explanation for a human
    segment: str = ""                # segment ID involved
    position: int | None = None      # segment position in the file (1-based)
    group: str | None = None         # GS06 of the group; None = interchange level
    transaction: str | None = None   # ST02 of the set; None = group or interchange level
    set_position: int | None = None  # position inside ST..SE (ST = 1), for 997 AK302
    element: int | None = None       # element number in the segment, for 997 AK401
    value: str = ""                  # the offending value, for 997 AK404

    def __str__(self) -> str:
        where = f"segment {self.position} ({self.segment})" if self.position else self.segment
        return f"{self.code} at {where}: {self.message}"


def detect_delimiters(text: str) -> Delimiters:
    """Read the delimiters from their fixed places in the ISA segment.

    Because the ISA is fixed width they never move:
      index 3   element separator   (the character right after "ISA")
      index 104 component separator (ISA16)
      index 105 segment terminator  (the character right after ISA16)
    """
    if not text.startswith("ISA") or len(text) < ISA_LENGTH:
        raise ValueError("not an X12 interchange: expected a 106-character ISA segment first")
    return Delimiters(element=text[3], component=text[104], segment=text[105])


def split_segments(text: str, d: Delimiters) -> list[list[str]]:
    """Cut the text at every segment terminator, then cut each segment into elements."""
    rows = []
    for raw in text.split(d.segment):
        raw = raw.strip("\r\n")  # many senders put a line break after each terminator
        if raw.strip():
            rows.append(raw.split(d.element))
    return rows


def parse(text: str) -> tuple[Interchange | None, list[EdiError]]:
    """Build the envelope tree. Returns (None, errors) when there is no usable ISA."""
    text = text.lstrip("﻿ \t\r\n")  # ignore a byte-order mark or blank lines
    try:
        delims = detect_delimiters(text)
    except ValueError as exc:
        return None, [EdiError("NO_ISA", str(exc), "ISA", 1)]
    rows = split_segments(text, delims)
    isa = Segment("ISA", rows[0][1:], 1)
    if len(isa.elements) != 16:
        msg = f"ISA must have 16 elements, found {len(isa.elements)}"
        return None, [EdiError("ISA_MALFORMED", msg, "ISA", 1)]

    ic = Interchange(isa=isa, delimiters=delims)
    errors: list[EdiError] = []
    group: FunctionalGroup | None = None
    ts: TransactionSet | None = None
    for pos, parts in enumerate(rows[1:], start=2):
        seg = Segment(parts[0].strip(), parts[1:], pos)
        tag = seg.tag
        if ic.iea is not None:
            msg = f"{tag} after IEA: only one interchange per file is supported"
            errors.append(EdiError("UNEXPECTED_SEGMENT", msg, tag, pos))
            break
        # A new header or an outer trailer also ends any envelope left open inside it.
        if tag in ("ST", "GS", "GE", "IEA") and ts is not None:
            errors.append(_never_closed(ts.st, "SE", group.control_number, ts.control_number))
            ts = None
        if tag in ("GS", "IEA") and group is not None:
            errors.append(_never_closed(group.gs, "GE", group.control_number))
            group = None

        if tag == "GS":
            group = FunctionalGroup(gs=seg)
            ic.groups.append(group)
        elif tag == "ST" and group is not None:
            ts = TransactionSet(st=seg, segments=[seg])
            group.transactions.append(ts)
        elif tag == "SE" and ts is not None:
            ts.segments.append(seg)
            ts.se, ts = seg, None
        elif tag == "GE" and group is not None:
            group.ge, group = seg, None
        elif tag == "IEA":
            ic.iea = seg
        elif ts is not None:
            ts.segments.append(seg)  # an ordinary data segment (BEG, PO1 ...)
        else:
            msg = f"{tag} is outside the envelope it belongs in"
            gnum = group.control_number if group else None
            errors.append(EdiError("UNEXPECTED_SEGMENT", msg, tag, pos, gnum))

    # End of file: close whatever is still open.
    if ts is not None:
        errors.append(_never_closed(ts.st, "SE", group.control_number, ts.control_number))
    if group is not None:
        errors.append(_never_closed(group.gs, "GE", group.control_number))
    if ic.iea is None:
        errors.append(EdiError("IEA_MISSING", "the file ends without an IEA trailer", "IEA"))
    return ic, errors


def _never_closed(header: Segment, trailer: str, group: str | None,
                  transaction: str | None = None) -> EdiError:
    msg = f"{header.tag} at segment {header.position} is never closed by {trailer}"
    return EdiError(f"{trailer}_MISSING", msg, trailer, header.position, group, transaction)


def validate(ic: Interchange) -> list[EdiError]:
    """Check control numbers, counts and required content of a parsed interchange."""
    errors = []
    if ic.iea is not None:
        if not _same_number(ic.iea.get(2), ic.control_number):
            msg = f"IEA02 {ic.iea.get(2)!r} does not match ISA13 {ic.control_number!r}"
            errors.append(EdiError("ISA_IEA_CONTROL_MISMATCH", msg, "IEA", ic.iea.position))
        if not _same_number(ic.iea.get(1), len(ic.groups)):
            msg = f"IEA01 says {ic.iea.get(1)!r} group(s) but the file has {len(ic.groups)}"
            errors.append(EdiError("IEA_GROUP_COUNT", msg, "IEA", ic.iea.position))

    for group in ic.groups:
        gnum, ge = group.control_number, group.ge
        if ge is not None:
            if not _same_number(ge.get(2), gnum):
                msg = f"GE02 {ge.get(2)!r} does not match GS06 {gnum!r}"
                errors.append(EdiError("GS_GE_CONTROL_MISMATCH", msg, "GE", ge.position, gnum))
            if not _same_number(ge.get(1), len(group.transactions)):
                msg = f"GE01 says {ge.get(1)!r} set(s) but the group has {len(group.transactions)}"
                errors.append(EdiError("GE_SET_COUNT", msg, "GE", ge.position, gnum))
        for ts in group.transactions:
            errors += _validate_set(ts, gnum)
    return errors


def _validate_set(ts: TransactionSet, gnum: str) -> list[EdiError]:
    errors = []
    tnum, se = ts.control_number, ts.se

    def add(code: str, msg: str, seg: Segment, element: int | None = None,
            value: str = "", tag: str = "") -> None:
        errors.append(EdiError(code, msg, tag or seg.tag, seg.position, gnum, tnum,
                               ts.position_in_set(seg), element, value))

    if se is not None:
        # ST02/SE02 are alphanumeric, so they must match exactly ("1" is not "0001").
        if se.get(2).strip() != tnum.strip():
            add("ST_SE_CONTROL_MISMATCH", f"SE02 {se.get(2)!r} does not match ST02 {tnum!r}", se)
        if not _same_number(se.get(1), len(ts.segments)):
            add("SE_SEGMENT_COUNT",
                f"SE01 says {se.get(1)!r} segments but ST..SE holds {len(ts.segments)}", se)

    for tag in REQUIRED_SEGMENTS.get(ts.set_id, []):
        if ts.find(tag) is None:
            # Reported at the end of the set: that is where the reader noticed the gap.
            add("REQUIRED_SEGMENT_MISSING",
                f"{ts.set_id} {tnum} has no {tag} (noticed at the end of the set)",
                ts.segments[-1], tag=tag)

    for seg in ts.segments:
        for n in REQUIRED_ELEMENTS.get(seg.tag, []):
            if not seg.get(n).strip():
                add("REQUIRED_ELEMENT_MISSING", f"{seg.tag}{n:02d} is required", seg, n)
        for n in NUMERIC_ELEMENTS.get(seg.tag, []):
            value = seg.get(n).strip()
            if value and not _NUMBER.fullmatch(value):
                add("INVALID_NUMBER", f"{seg.tag}{n:02d} must be numeric, got {value!r}",
                    seg, n, value)
    return errors


def _same_number(a: str | int, b: str | int) -> bool:
    """Counts and ISA/GS control numbers are numeric: "000000101" equals "101"."""
    try:
        return int(str(a).strip()) == int(str(b).strip())
    except ValueError:
        return str(a).strip() == str(b).strip()


def read_x12(text: str) -> tuple[Interchange | None, list[EdiError]]:
    """Parse and validate in one call; the usual entry point."""
    ic, errors = parse(text)
    if ic is not None:
        errors += validate(ic)
    return ic, errors


def errors_for(errors: list[EdiError], group: FunctionalGroup,
               ts: TransactionSet | None = None) -> list[EdiError]:
    """Errors of one transaction set, or (ts=None) the group-level errors of a group."""
    tnum = ts.control_number if ts is not None else None
    return [e for e in errors if e.group == group.control_number and e.transaction == tnum]
