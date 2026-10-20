"""Print an X12 file one segment per line with every element labelled.

    12      PO1  PO101=1 [Line no]  PO102=500 [Quantity]  PO103=EA (Each) ...

The number on the left is the segment position (validate reports the same
numbers) and the indentation shows the envelope nesting. "(...)" explains a
code value, "[...]" names a free-text element. Works on broken files too,
because it does not need the envelopes to be correct.
"""

from __future__ import annotations

from .x12 import detect_delimiters, split_segments

PURPOSE = {"00": "Original", "01": "Cancellation", "05": "Replace", "07": "Duplicate"}
QUALIFIERS = {"ZZ": "Mutually defined", "01": "DUNS", "12": "Phone number"}
SET_IDS = {"850": "Purchase Order", "855": "PO Acknowledgment", "856": "Ship Notice",
           "810": "Invoice", "860": "PO Change", "997": "Functional Acknowledgment"}
FUNCTIONAL_IDS = {"PO": "Purchase orders", "PR": "PO acknowledgments", "SH": "Ship notices",
                  "IN": "Invoices", "FA": "Functional acknowledgments"}
UNITS = {"EA": "Each", "CA": "Case", "BX": "Box"}
PRODUCT_IDS = {"VP": "Vendor part", "BP": "Buyer part", "UP": "UPC"}
DATES = {"002": "Delivery requested", "011": "Shipped", "067": "Current schedule delivery",
         "068": "Current schedule ship"}

# tag: (segment name, {element number: "name" or ("name", {code: meaning})})
SEGMENTS: dict[str, tuple[str, dict]] = {
    "ISA": ("Interchange header", {
        1: ("Auth qualifier", {"00": "No authorization info"}), 2: "Auth info",
        3: ("Security qualifier", {"00": "No security info"}), 4: "Security info",
        5: ("Sender qualifier", QUALIFIERS), 6: "Sender ID", 7: ("Receiver qualifier", QUALIFIERS),
        8: "Receiver ID", 9: "Date YYMMDD", 10: "Time HHMM",
        11: ("Standards ID", {"U": "US EDI community (X12)"}),
        12: ("Version", {"00401": "X12 4010", "00501": "X12 5010"}), 13: "Interchange control no",
        14: ("TA1 wanted", {"0": "No TA1 requested", "1": "TA1 requested"}),
        15: ("Usage", {"T": "Test", "P": "Production"}), 16: "Component separator"}),
    "GS": ("Group header", {
        1: ("Functional ID", FUNCTIONAL_IDS), 2: "App sender", 3: "App receiver",
        4: "Date", 5: "Time", 6: "Group control no", 7: ("Agency", {"X": "ASC X12"}), 8: "Version"}),
    "ST": ("Transaction set header", {1: ("Set ID", SET_IDS), 2: "Set control no"}),
    "BEG": ("Beginning of PO", {
        1: ("Purpose", PURPOSE),
        2: ("PO type", {"SA": "Stand-alone order", "BK": "Blanket order", "RL": "Release"}),
        3: "PO number", 4: "Release no", 5: "PO date"}),
    "DTM": ("Date/time", {1: ("Date qualifier", DATES), 2: "Date", 3: "Time"}),
    "N1": ("Party name", {
        1: ("Entity", {"ST": "Ship to", "BT": "Bill to", "BY": "Buyer", "SF": "Ship from"}),
        2: "Name", 3: ("ID qualifier", {"92": "Assigned by buyer", "91": "Assigned by seller"}),
        4: "Party ID"}),
    "PO1": ("PO line", {
        1: "Line no", 2: "Quantity", 3: ("Unit", UNITS), 4: "Unit price", 5: "Price basis",
        **{n: ("Product ID qualifier", PRODUCT_IDS) for n in (6, 8, 10)},
        **{n: "Product ID" for n in (7, 9, 11)}}),
    "CTT": ("Totals", {1: "Line/HL count", 2: "Hash total"}),
    "SE": ("Transaction set trailer", {1: "Segment count ST..SE", 2: "Set control no (= ST02)"}),
    "GE": ("Group trailer", {1: "Set count", 2: "Group control no (= GS06)"}),
    "IEA": ("Interchange trailer", {1: "Group count", 2: "Interchange control no (= ISA13)"}),
    "BAK": ("Beginning of PO ack", {
        1: ("Purpose", PURPOSE),
        2: ("Ack type", {"AC": "Accepted with detail", "RJ": "Rejected", "RD": "Rejected with detail"}),
        3: "PO number", 4: "PO date"}),
    "ACK": ("Line acknowledgment", {
        1: ("Line status", {"IA": "Item accepted", "IR": "Item rejected", "IB": "Backordered"}),
        2: "Quantity", 3: ("Unit", UNITS), 4: ("Date qualifier", DATES), 5: "Date"}),
    "BSN": ("Beginning of ship notice", {
        1: ("Purpose", PURPOSE), 2: "Shipment ID", 3: "Date", 4: "Time"}),
    "HL": ("Hierarchical level", {
        1: "HL id", 2: "Parent HL id",
        3: ("Level", {"S": "Shipment", "O": "Order", "P": "Pack", "I": "Item"})}),
    "TD1": ("Packaging", {1: ("Packaging", {"CTN25": "Corrugated carton"}), 2: "Lading quantity"}),
    "TD5": ("Carrier", {2: ("Carrier ID qualifier", {"2": "SCAC code"}), 3: "Carrier code"}),
    "REF": ("Reference", {
        1: ("Reference type", {"BM": "Bill of lading", "BT": "Batch number", "SE": "Serial number"}),
        2: "Reference", 3: "Description"}),
    "PRF": ("PO reference", {1: "PO number"}),
    "LIN": ("Item ID", {1: "Assigned ID", 2: ("Product ID qualifier", PRODUCT_IDS), 3: "Product ID"}),
    "SN1": ("Item shipped", {1: "Assigned ID", 2: "Units shipped", 3: ("Unit", UNITS)}),
    "AK1": ("Group response header", {1: ("Functional ID", FUNCTIONAL_IDS), 2: "Group control no"}),
    "AK2": ("Set response header", {1: ("Set ID", SET_IDS), 2: "Set control no"}),
    "AK3": ("Segment error", {1: "Segment ID", 2: "Position in set", 3: "Loop ID",
                              4: ("Error", {"3": "Mandatory segment missing",
                                            "8": "Segment has element errors"})}),
    "AK4": ("Element error", {1: "Element position", 2: "Element ref no",
                              3: ("Error", {"1": "Mandatory element missing",
                                            "6": "Invalid character", "8": "Invalid date"}),
                              4: "Bad value"}),
    "AK5": ("Set response trailer", {
        1: ("Set status", {"A": "Accepted", "E": "Accepted with errors", "R": "Rejected"}),
        **{n: ("Error", {"2": "SE missing", "3": "ST02/SE02 differ", "4": "SE01 count wrong",
                         "5": "Segments in error"}) for n in range(2, 7)}}),
    "AK9": ("Group response trailer", {
        1: ("Group status", {"A": "Accepted", "P": "Partially accepted", "R": "Rejected"}),
        2: "Sets included", 3: "Sets received", 4: "Sets accepted",
        **{n: ("Error", {"3": "GE missing", "4": "GS06/GE02 differ", "5": "GE01 count wrong"})
           for n in range(5, 10)}}),
}
DEPTH = {"ISA": 0, "IEA": 0, "GS": 1, "GE": 1, "ST": 2, "SE": 2}


def describe(tag: str, elements: list[str]) -> str:
    """One labelled line for one segment, e.g. 'BEG  BEG01=00 (Original) ...'."""
    name, labels = SEGMENTS.get(tag, ("unknown segment", {}))
    parts = []
    for n, value in enumerate(elements, start=1):
        value = value.strip()
        if not value:
            continue  # empty elements keep their number but are not printed
        text, label = f"{tag}{n:02d}={value}", labels.get(n)
        if isinstance(label, tuple):
            meaning = label[1].get(value)
            text += f" ({meaning})" if meaning else f" [{label[0]}]"
        elif label:
            text += f" [{label}]"
        parts.append(text)
    return f"{tag:<4} " + "  ".join(parts) + f"   # {name}"


def pretty(text: str) -> str:
    text = text.lstrip("﻿ \t\r\n")
    d = detect_delimiters(text)  # raises ValueError if this is not X12 at all
    lines = [f"delimiters: element {d.element!r}  component {d.component!r}  segment {d.segment!r}"]
    for pos, (tag, *elements) in enumerate(split_segments(text, d), start=1):
        indent = "  " * DEPTH.get(tag.strip(), 3)
        lines.append(f"{pos:>3}  {indent}{describe(tag.strip(), elements)}")
    return "\n".join(lines)
