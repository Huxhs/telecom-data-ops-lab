"""Pass ERP customer orders on to SAP as ORDERS05 IDocs in XML.

An IDoc (intermediate document) is SAP's own interchange format: a control
record (EDI_DC40: who sends which message to whom) followed by data segments,
here E1EDK* for the order header and one E1EDP01 per line. SAP adds the status
records itself while it processes the IDoc.

The input is erp_orders.json as `inbound` writes it. Only what the ERP record
holds goes in; the customer's part number and name come from the same
cross-reference files `inbound` uses. SAP checks segments against the order the
IDoc type defines (transaction WE30), so they are added in that order.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from .envelope import ControlNumbers
from .mapping import HEADER_FIELDS

IDOC_TYPE = "ORDERS05"     # basic type: the segment layout
MESSAGE_TYPE = "ORDERS"    # business meaning: a purchase order / sales order
SENDER_PORT = "EDIBRIDGE"  # the XML file port SAP sets up for our files (WE21)
RECEIVER_PORT = "SAPSID"   # by convention "SAP" + the system id, e.g. SAPPRD
UNITS = {"EA": "PCE"}      # ERP unit -> ISO unit code, which IDocs carry


def load_erp_orders(path: str | Path) -> list[dict]:
    """Read erp_orders.json and fail with a clear message if it is empty or incomplete."""
    orders = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(orders, list) or not orders:
        raise ValueError(f"{path}: no ERP orders to send")
    for order in orders:
        missing = [k for k in [*HEADER_FIELDS, "lines"] if k not in order]
        if missing:
            raise ValueError(f"ERP order {order.get('co_num', '?')} is missing: {', '.join(missing)}")
    return orders


def sap_date(value: str | None) -> str | None:
    """YYYY-MM-DD (the ERP format) -> YYYYMMDD (SAP's date format)."""
    return value.replace("-", "") if value else None


def add_segment(parent: ET.Element, name: str, **fields: object) -> ET.Element:
    """Append a segment. Empty fields are left out, not written as empty tags."""
    seg = ET.SubElement(parent, name, SEGMENT="1")
    for field_name, value in fields.items():
        if value not in (None, ""):
            ET.SubElement(seg, field_name).text = str(value)
    return seg


def build_idoc(order: dict, docnum: int, when: datetime, sender: str, receiver: str,
               client: str, customer_names: dict, customer_parts: dict) -> ET.Element:
    """One <IDOC>: control record, header segments, then the lines."""
    idoc = ET.Element("IDOC", BEGIN="1")
    add_segment(idoc, "EDI_DC40", TABNAM="EDI_DC40", MANDT=client, DOCNUM=f"{docnum:016d}",
                DIRECT="2",  # 2 = inbound, seen from SAP
                IDOCTYP=IDOC_TYPE, MESTYP=MESSAGE_TYPE,
                SNDPOR=SENDER_PORT, SNDPRT="LS", SNDPRN=sender,  # LS = logical system
                RCVPOR=RECEIVER_PORT, RCVPRT="LS", RCVPRN=receiver,
                CREDAT=when.strftime("%Y%m%d"), CRETIM=when.strftime("%H%M%S"))
    add_segment(idoc, "E1EDK01", BELNR=order["co_num"])  # no CURCY: the ERP record has no currency
    for qualifier, date in (("012", order["order_date"]), ("002", order["due_date"])):
        if date:  # 012 = document date, 002 = requested delivery date
            add_segment(idoc, "E1EDK03", IDDAT=qualifier, DATUM=sap_date(date))
    customer = order["cust_num"]
    add_segment(idoc, "E1EDKA1", PARVW="AG", PARTN=customer,  # AG = sold-to
                NAME1=customer_names.get(customer))
    if order["ship_to"]:
        add_segment(idoc, "E1EDKA1", PARVW="WE", PARTN=order["ship_to"])  # WE = ship-to
    add_segment(idoc, "E1EDK02", QUALF="001", BELNR=order["cust_po"])  # 001 = customer PO
    for line in order["lines"]:
        item = add_segment(idoc, "E1EDP01", POSEX=line["co_line"], MENGE=line["qty_ordered"],
                           MENEE=UNITS.get(line["u_m"], line["u_m"]), VPREI=line["price"])
        add_segment(item, "E1EDP20", WMENG=line["qty_ordered"], EDATU=sap_date(order["due_date"]))
        add_segment(item, "E1EDP19", QUALF="002", IDTNR=line["item"])  # 002 = our material
        customer_part = customer_parts.get((customer, line["item"]))
        if customer_part:
            add_segment(item, "E1EDP19", QUALF="001", IDTNR=customer_part)  # 001 = customer's
    return idoc


def make_idoc_xml(orders: list[dict], counter: ControlNumbers, sender: str = "SYTELINE",
                  receiver: str = "SAPCLNT100", client: str = "100",
                  when: datetime | None = None, partners: dict | None = None,
                  items: dict | None = None) -> str:
    """The complete XML file: <ORDERS05> holding one <IDOC> per order.

    `partners` and `items` are the tables mapping.load_partners/load_items return.
    """
    if not (len(client) == 3 and client.isdigit()):
        raise ValueError(f"an SAP client is 3 digits: {client!r}")
    for name in (sender, receiver):
        if not name or len(name) > 10:
            raise ValueError(f"logical system names are 1 to 10 characters: {name!r}")
    when = when or datetime.now()
    customer_names = {row["customer_code"]: row["name"] for row in (partners or {}).values()}
    customer_parts = {}
    for (customer, qualifier, part), row in (items or {}).items():
        if qualifier == "BP":
            customer_parts.setdefault((customer, row["item"]), part)
    root = ET.Element(IDOC_TYPE)
    for order in orders:
        root.append(build_idoc(order, counter.next("idoc"), when, sender, receiver, client,
                               customer_names, customer_parts))
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
