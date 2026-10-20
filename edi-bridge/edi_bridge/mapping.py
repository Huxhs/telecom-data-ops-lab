"""Cross-reference an Order to ERP codes and write an ERP import record.

EDI speaks the customer's language (their ISA ID, their part numbers); the ERP
speaks ours (customer number, internal item). Cross-reference tables bridge the
two. A missing entry is the most common inbound failure in real EDI support, so
it is reported as a MappingError, never raised.

The output is shaped like a much simplified Infor SyteLine customer order: a
header (table co) and lines (table coitem).
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path

from .m850 import Order

HEADER_FIELDS = ["co_num", "cust_num", "cust_po", "order_date", "due_date", "ship_to"]
LINE_FIELDS = ["co_num", "co_line", "item", "qty_ordered", "u_m", "price"]


@dataclass
class MappingError:
    code: str                    # UNKNOWN_PARTNER or UNKNOWN_ITEM
    message: str
    line_no: str | None = None   # PO101 of the failing line; None = whole order


@dataclass
class ErpLine:
    co_line: int
    item: str
    qty_ordered: Decimal | None
    u_m: str
    price: Decimal | None


@dataclass
class ErpOrder:
    co_num: str
    cust_num: str
    cust_po: str
    order_date: str
    due_date: str | None
    ship_to: str
    lines: list[ErpLine] = field(default_factory=list)


def load_partners(path: str | Path) -> dict[str, dict[str, str]]:
    """partners.csv: isa_sender_id -> {customer_code, name}."""
    with open(path, newline="", encoding="utf-8") as f:
        return {row["isa_sender_id"].strip(): row for row in csv.DictReader(f)}


def load_items(path: str | Path) -> dict[tuple[str, str, str], dict[str, str]]:
    """items.csv, keyed by (customer_code, "BP" or "VP", part number)."""
    table = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["buyer_part"]:
                table[(row["customer_code"], "BP", row["buyer_part"])] = row
            if row["vendor_part"]:
                table[(row["customer_code"], "VP", row["vendor_part"])] = row
    return table


def make_co_num(po_number: str) -> str:
    """SyteLine's co_num is a 10-character key. Real sites take the next number
    from a sequence; we derive it from the PO so the lab output is repeatable."""
    return ("EDI" + re.sub(r"[^A-Za-z0-9]", "", po_number))[:10]


def map_order(order: Order, partners: dict,
              items: dict) -> tuple[ErpOrder | None, list[MappingError]]:
    """Return (record, []) when everything maps, or (None, errors) when anything does not."""
    partner = partners.get(order.sender_id)
    if partner is None:
        return None, [MappingError("UNKNOWN_PARTNER",
                                   f"no trading partner set up for ISA sender {order.sender_id!r}")]
    customer = partner["customer_code"]
    record = ErpOrder(co_num=make_co_num(order.po_number), cust_num=customer,
                      cust_po=order.po_number, order_date=order.po_date,
                      due_date=order.requested_delivery, ship_to=order.ship_to_id)
    errors = []
    for n, line in enumerate(order.lines, start=1):
        # Prefer the buyer's part number, fall back to our vendor part number.
        xref = (items.get((customer, "BP", line.buyer_part))
                or items.get((customer, "VP", line.vendor_part)))
        if xref is None:
            errors.append(MappingError(
                "UNKNOWN_ITEM", f"line {line.line_no}: no item cross-reference for {customer} "
                f"BP={line.buyer_part!r} VP={line.vendor_part!r}", line.line_no))
            continue
        # UOM is passed through: X12 "EA" and SyteLine "EA" happen to agree here.
        record.lines.append(ErpLine(n, xref["item"], line.quantity, line.uom, line.unit_price))
    return (None, errors) if errors else (record, [])


def write_json(data: object, path: Path) -> None:
    """Dataclasses become dicts; Decimals are written as strings so no rounding sneaks in."""
    if isinstance(data, list):
        data = [asdict(x) for x in data]
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def write_erp_csv(records: list[ErpOrder], out_dir: Path) -> tuple[Path, Path]:
    """Two flat files, the way an ERP bulk-import usually wants them."""
    header_path, lines_path = out_dir / "co_header.csv", out_dir / "co_lines.csv"
    with open(header_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, HEADER_FIELDS)
        writer.writeheader()
        for rec in records:
            writer.writerow({k: getattr(rec, k) or "" for k in HEADER_FIELDS})
    with open(lines_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, LINE_FIELDS)
        writer.writeheader()
        for rec in records:
            for line in rec.lines:
                writer.writerow({"co_num": rec.co_num, **asdict(line)})
    return header_path, lines_path
