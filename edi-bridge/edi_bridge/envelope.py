"""Build outbound X12: wrap transaction sets in ST/SE, GS/GE and ISA/IEA.

The builders elsewhere (997, 855, 856) only produce the *body* of a transaction
set as lists of elements, e.g. ["BAK", "00", "AC", "PO45001", "20261019"].
This module adds the envelopes, the counts and the control numbers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .x12 import ISA_LENGTH, Delimiters, Interchange

VERSION_ISA = "00401"   # ISA12: X12 version 4010, still the most common in manufacturing
VERSION_GS = "004010"   # GS08: same version, in the longer GS format


@dataclass
class Party:
    id: str                 # e.g. "GDMOBILE"
    qualifier: str = "ZZ"   # ZZ = mutually defined, the usual choice between partners


def reply_parties(ic: Interchange) -> tuple[Party, Party]:
    """Sender and receiver for a reply: the received interchange, swapped round."""
    return (Party(ic.receiver_id, ic.receiver_qualifier),
            Party(ic.sender_id, ic.sender_qualifier))


class ControlNumbers:
    """Hands out increasing control numbers and remembers the last ones in a JSON file.

    Trading partners use control numbers to spot duplicates and gaps, so they
    must keep increasing across runs, not restart at 1 every time.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def next(self, name: str) -> int:
        data = json.loads(self.path.read_text()) if self.path.exists() else {}
        data[name] = int(data.get(name, 0)) + 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2) + "\n")
        return data[name]


def segment(elements: list[str], d: Delimiters) -> str:
    """Join one segment. Trailing empty elements are dropped, as X12 requires."""
    elements = list(elements)
    while len(elements) > 1 and elements[-1] == "":
        elements.pop()
    for value in elements:
        # A delimiter inside a value would shift every element after it.
        if any(ch in value for ch in (d.element, d.component, d.segment)):
            raise ValueError(f"value {value!r} contains an X12 delimiter")
    return d.element.join(elements) + d.segment


def build_isa(sender: Party, receiver: Party, control: int, when: datetime,
              usage: str = "T", d: Delimiters = Delimiters()) -> str:
    """The ISA is fixed width: every field is padded so the segment is 106 characters."""
    for party in (sender, receiver):
        if len(party.id) > 15:
            raise ValueError(f"ISA IDs are at most 15 characters: {party.id!r}")
    fields = [
        "00", " " * 10,                         # ISA01/02 authorization info: none
        "00", " " * 10,                         # ISA03/04 security info: none
        sender.qualifier, sender.id.ljust(15),  # ISA05/06
        receiver.qualifier, receiver.id.ljust(15),  # ISA07/08
        when.strftime("%y%m%d"), when.strftime("%H%M"),  # ISA09/10
        "U", VERSION_ISA,                       # ISA11 standards id, ISA12 version
        f"{control:09d}",                       # ISA13 interchange control number
        "0",                                    # ISA14 0 = no TA1 acknowledgment wanted
        usage,                                  # ISA15 T = test, P = production
        d.component,                            # ISA16 component separator
    ]
    isa = "ISA" + d.element + d.element.join(fields) + d.segment
    assert len(isa) == ISA_LENGTH, len(isa)
    return isa


def build_interchange(functional_id: str, sets: list[tuple[str, list[list[str]]]],
                      sender: Party, receiver: Party, counter: ControlNumbers,
                      when: datetime | None = None, usage: str = "T",
                      d: Delimiters = Delimiters()) -> str:
    """Wrap transaction-set bodies, e.g. [("855", body)], in one GS group and one ISA.

    ST02 restarts at 0001 in every group (it only has to be unique inside the
    group); ISA13 and GS06 come from the counter file and keep increasing.
    """
    when = when or datetime.now()
    isa_no = counter.next("interchange")
    gs_no = counter.next("group")
    out = [build_isa(sender, receiver, isa_no, when, usage, d),
           segment(["GS", functional_id, sender.id, receiver.id, when.strftime("%Y%m%d"),
                    when.strftime("%H%M"), str(gs_no), "X", VERSION_GS], d)]
    for n, (set_id, body) in enumerate(sets, start=1):
        st_no = f"{n:04d}"
        rows = [["ST", set_id, st_no], *body]
        rows.append(["SE", str(len(rows) + 1), st_no])  # SE01 counts ST..SE inclusive
        out += [segment(row, d) for row in rows]
    out.append(segment(["GE", str(len(sets)), str(gs_no)], d))
    out.append(segment(["IEA", "1", f"{isa_no:09d}"], d))
    return "\n".join(out) + "\n"  # a line break after each terminator, for humans
