"""Generate an 856 Advance Ship Notice (ASN) from a simgen shipment summary.

The 856 is a tree built from HL (hierarchical level) segments. Each HL has an
id, the id of its parent, and a level code:

    HL*1**S     Shipment   carrier, bill of lading, ship date, ship-to
      HL*2*1*O  Order      which PO this shipment fills
        HL*3*2*I  Item     what was shipped, how many, batch and serials

The summary JSON is written by the sibling project `simgen` after it has
personalised a batch of SIM cards.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

from .envelope import ControlNumbers, Party, build_interchange

SUMMARY_KEYS = ("order_id", "customer", "po_number", "batch", "product", "quantity",
                "iccid_first", "iccid_last", "generated_at")
CARDS_PER_CARTON = 500  # synthetic packing rule, used for the TD1 carton count


def load_summary(path: str | Path) -> dict:
    """Read the summary and fail with a clear message if a key is missing."""
    summary = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = [k for k in SUMMARY_KEYS if k not in summary]
    if missing:
        raise ValueError(f"shipment summary is missing: {', '.join(missing)}")
    return summary


def build_856(summary: dict, carrier: str = "DEMO", bol: str | None = None,
              ship_to_name: str | None = None, ship_to_id: str | None = None) -> list[list[str]]:
    """Body of one 856 (everything between ST and SE)."""
    shipped = datetime.fromisoformat(summary["generated_at"])  # simgen writes UTC "...Z"
    date, time = shipped.strftime("%Y%m%d"), shipped.strftime("%H%M")
    qty = int(summary["quantity"])
    batch = summary["batch"]
    body = [
        ["BSN", "00", f"SH{batch}", date, time],    # 00 = original notice, then shipment id
        ["HL", "1", "", "S"],                        # level 1: the shipment, no parent
        ["TD1", "CTN25", str(max(1, math.ceil(qty / CARDS_PER_CARTON)))],  # cartons
        ["TD5", "", "2", carrier],                   # TD502 2 = carrier is a SCAC code
        ["REF", "BM", bol or f"BOL{batch}"],         # BM = bill of lading number
        ["DTM", "011", date],                        # 011 = shipped date
        ["N1", "ST", ship_to_name or summary["customer"], "92", ship_to_id or summary["customer"]],
        ["HL", "2", "1", "O"],                       # level 2: the order, child of HL 1
        ["PRF", summary["po_number"]],               # the customer's PO number
        ["HL", "3", "2", "I"],                       # level 3: the item, child of HL 2
        ["LIN", "", "VP", summary["product"]],       # VP = our (vendor) part number
        ["SN1", "", str(qty), "EA"],                 # units shipped
        ["REF", "BT", batch],                        # BT = batch number
        # Learning touch: the ICCID range of the cards in this batch. Real partners
        # say in their guide where serials go (often a MAN or SLN loop instead).
        ["REF", "SE", summary["iccid_first"], "FIRST ICCID"],
        ["REF", "SE", summary["iccid_last"], "LAST ICCID"],
    ]
    hl_count = sum(1 for row in body if row[0] == "HL")
    body.append(["CTT", str(hl_count)])  # CTT01 = number of HL segments, a common convention
    return body


def make_856(summary: dict, sender: Party, receiver: Party, counter: ControlNumbers,
             when: datetime | None = None, **options: str | None) -> str:
    """The complete outbound 856 file. `options` go to build_856 (carrier, bol ...)."""
    body = build_856(summary, **options)
    return build_interchange("SH", [("856", body)], sender, receiver, counter, when)
