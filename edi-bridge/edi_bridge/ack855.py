"""Generate an 855 Purchase Order Acknowledgment from an Order.

Where the 997 says "your file was readable", the 855 is the business answer:
"we accept (or reject) your order, line by line, and here is the date".

    BAK  header: BAK02 = AC (accepted with detail) or RJ (rejected)
    PO1  echo of each ordered line
    ACK  status of that line: IA = item accepted, IR = item rejected
    CTT  number of PO1 lines

Partners differ here: some want RD (reject with detail), some want AC with IR
lines. Their implementation guide decides; this lab uses RJ for any bad line.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .envelope import ControlNumbers, build_interchange, reply_parties
from .m850 import Order
from .x12 import Interchange


def _fmt(value: Decimal | None) -> str:
    return "" if value is None else str(value)


def build_855(order: Order, rejected_lines: set[str]) -> list[list[str]]:
    """Body of one 855. `rejected_lines` holds PO101 line numbers that failed mapping."""
    status = "RJ" if rejected_lines or not order.lines else "AC"
    body = [["BAK", "00", status, order.po_number, order.po_date.replace("-", "")]]
    promise = (order.requested_delivery or "").replace("-", "")
    for line in order.lines:
        po1 = ["PO1", line.line_no, _fmt(line.quantity), line.uom, _fmt(line.unit_price), ""]
        for qualifier, value in (("VP", line.vendor_part), ("BP", line.buyer_part), ("UP", line.upc)):
            if value:
                po1 += [qualifier, value]
        body.append(po1)
        if line.line_no in rejected_lines:
            body.append(["ACK", "IR", _fmt(line.quantity), line.uom])
        else:
            # 067 = current schedule delivery: we promise the date the buyer asked for.
            body.append(["ACK", "IA", _fmt(line.quantity), line.uom,
                         "067" if promise else "", promise])
    body.append(["CTT", str(len(order.lines))])
    return body


def make_855(acks: list[tuple[Order, set[str]]], ic: Interchange, counter: ControlNumbers,
             when: datetime | None = None) -> str:
    """One outbound file holding an 855 per order, addressed back to the sender of `ic`."""
    sender, receiver = reply_parties(ic)
    sets = [("855", build_855(order, rejected)) for order, rejected in acks]
    return build_interchange("PR", sets, sender, receiver, counter, when, ic.usage or "T")
