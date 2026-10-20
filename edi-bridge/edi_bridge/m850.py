"""Map an 850 Purchase Order transaction set onto a plain Python object.

This is the "map" step a translator such as Sterling Gentran performs: take
values from fixed positions (BEG03, PO102 ...) and give them business names.
After this step no other code needs to know X12 element numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .x12 import Interchange, Segment, TransactionSet

# PO106/PO107, PO108/PO109 ... are (qualifier, value) pairs naming the product.
PRODUCT_QUALIFIERS = {"VP": "vendor_part", "BP": "buyer_part", "UP": "upc"}


@dataclass
class OrderLine:
    line_no: str                 # PO101: the buyer's line number
    quantity: Decimal | None     # PO102
    uom: str                     # PO103: unit of measure, EA = each
    unit_price: Decimal | None   # PO104
    vendor_part: str = ""        # VP: our part number, as the buyer knows it
    buyer_part: str = ""         # BP: the buyer's own part number
    upc: str = ""                # UP: UPC barcode number


@dataclass
class Order:
    sender_id: str                   # ISA06: who sent it (used to find the customer)
    control_number: str              # ST02: needed to acknowledge this exact set
    purpose: str                     # BEG01: 00 = original
    order_type: str                  # BEG02: SA = stand-alone order
    po_number: str                   # BEG03
    po_date: str                     # BEG05, as YYYY-MM-DD
    requested_delivery: str | None = None  # DTM*002 date, as YYYY-MM-DD
    ship_to_name: str = ""           # N1*ST N102
    ship_to_id: str = ""             # N1*ST N104 (N103 says who assigned it)
    lines: list[OrderLine] = field(default_factory=list)


def x12_date(value: str) -> str:
    """CCYYMMDD -> YYYY-MM-DD. Anything else is returned unchanged."""
    value = value.strip()
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def to_decimal(value: str) -> Decimal | None:
    """Money and quantities stay exact Decimals; floats would round 0.1 + 0.2."""
    try:
        number = Decimal(value.strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def map_850(ts: TransactionSet, ic: Interchange) -> Order:
    """Read one 850. Missing segments give empty fields, never an exception."""
    beg = ts.find("BEG") or Segment("BEG", [], 0)
    order = Order(sender_id=ic.sender_id, control_number=ts.control_number,
                  purpose=beg.get(1), order_type=beg.get(2), po_number=beg.get(3),
                  po_date=x12_date(beg.get(5)))
    for seg in ts.segments:
        if seg.tag == "DTM" and seg.get(1) == "002" and order.requested_delivery is None:
            # Only the first DTM*002 is kept (the header one). A DTM after a PO1
            # would be a line-level date; a fuller map would store it on the line.
            order.requested_delivery = x12_date(seg.get(2))
        elif seg.tag == "N1" and seg.get(1) == "ST":
            order.ship_to_name, order.ship_to_id = seg.get(2), seg.get(4)
        elif seg.tag == "PO1":
            order.lines.append(_map_po1(seg))
    return order


def _map_po1(seg: Segment) -> OrderLine:
    line = OrderLine(line_no=seg.get(1), quantity=to_decimal(seg.get(2)),
                     uom=seg.get(3), unit_price=to_decimal(seg.get(4)))
    for n in range(6, len(seg.elements) + 1, 2):   # PO106, PO108, PO110 ...
        attr = PRODUCT_QUALIFIERS.get(seg.get(n))
        if attr and seg.get(n + 1):
            setattr(line, attr, seg.get(n + 1))
    return line
