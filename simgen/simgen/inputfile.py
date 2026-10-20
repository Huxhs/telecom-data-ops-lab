"""Read and validate the operator's input file (the order for profile data).

Real input files differ per operator and per vendor, but most look like this:
a header of `Key: value` lines, then the number ranges to use. This lab's
format:

    *HEADER
    Customer: MNO-NORTH
    Order_ID: ORD-20261019-0001
    PO_Ref: PO45001
    Quantity: 500
    Type: UICC-TRIO-128K
    Profile: NORTH_4G5G_V3
    Batch: B26101901
    Transport_Key_ID: TK01
    SLA_Hours: 24
    *RANGES
    ICCID_Start: 890010100000000001
    IMSI_Start: 001010000000001
    *END

ICCID_Start is the 18-digit body; the Luhn check digit is added per card.

eSIM orders (Type starting with ESIM) also need `SMDP_Address: smdp.lab.example`,
the hostname of the SM-DP+ server that customers download their profiles from.
"""

import re
from dataclasses import dataclass
from pathlib import Path

REQUIRED = [
    "Customer", "Order_ID", "PO_Ref", "Quantity", "Type", "Profile",
    "Batch", "Transport_Key_ID", "ICCID_Start", "IMSI_Start",
]
MAX_QUANTITY = 1_000_000
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
HOSTNAME = re.compile(rf"{_LABEL}(?:\.{_LABEL})+")  # at least two labels, e.g. smdp.lab.example


class InputFileError(ValueError):
    """The input file is unusable. `code` is a short id for dashboards and tickets."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass
class Order:
    customer: str
    order_id: str
    po_ref: str
    quantity: int
    card_type: str
    profile: str
    batch: str
    transport_key_id: str
    iccid_start: str
    imsi_start: str
    sla_hours: int = 24
    smdp_address: str = ""

    @property
    def is_esim(self) -> bool:
        return self.card_type.startswith("ESIM")

    @property
    def profile_kind(self) -> str:
        return "esim" if self.is_esim else "uicc"


def parse_input_file(path) -> Order:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    lines = [ln.strip() for ln in text.splitlines()]
    if not lines or lines[0] != "*HEADER":
        raise InputFileError("E_FORMAT", "file must start with *HEADER")
    if "*END" not in lines:
        raise InputFileError("E_TRUNCATED", "no *END marker; file may be incomplete")

    fields = {}
    for n, line in enumerate(lines, start=1):
        if not line or line.startswith("*") or line.startswith("#"):
            continue
        if ":" not in line:
            raise InputFileError("E_FORMAT", f"line {n} is not 'Key: value': {line!r}")
        key, value = (part.strip() for part in line.split(":", 1))
        if key in fields:
            raise InputFileError("E_DUPLICATE_FIELD", f"line {n}: {key} appears twice")
        fields[key] = value

    missing = [k for k in REQUIRED if not fields.get(k)]
    if fields.get("Type", "").startswith("ESIM") and not fields.get("SMDP_Address"):
        missing.append("SMDP_Address")
    if missing:
        raise InputFileError("E_MISSING_FIELD", f"missing: {', '.join(missing)}")

    order = Order(
        customer=fields["Customer"],
        order_id=fields["Order_ID"],
        po_ref=fields["PO_Ref"],
        quantity=_int(fields["Quantity"], "Quantity"),
        card_type=fields["Type"],
        profile=fields["Profile"],
        batch=fields["Batch"],
        transport_key_id=fields["Transport_Key_ID"],
        iccid_start=fields["ICCID_Start"],
        imsi_start=fields["IMSI_Start"],
        sla_hours=_int(fields.get("SLA_Hours", "24"), "SLA_Hours"),
        smdp_address=fields.get("SMDP_Address", ""),
    )
    validate_order(order)
    return order


def _int(value: str, name: str) -> int:
    if not value.isdigit():
        raise InputFileError("E_BAD_VALUE", f"{name} must be a whole number, got {value!r}")
    return int(value)


def validate_order(order: Order) -> None:
    if not 0 < order.quantity <= MAX_QUANTITY:
        raise InputFileError("E_BAD_QUANTITY", f"Quantity must be 1..{MAX_QUANTITY}")
    if not (order.iccid_start.isdigit() and len(order.iccid_start) == 18):
        raise InputFileError("E_BAD_ICCID", "ICCID_Start must be the 18-digit body (no check digit)")
    if not order.iccid_start.startswith("89"):
        raise InputFileError("E_BAD_ICCID", "ICCID must start with 89 (telecom industry id)")
    if not (order.imsi_start.isdigit() and len(order.imsi_start) == 15):
        raise InputFileError("E_BAD_IMSI", "IMSI_Start must be 15 digits")
    # Will the ranges fit without changing width or prefix?
    if len(str(int(order.iccid_start) + order.quantity - 1)) > 18:
        raise InputFileError("E_RANGE_OVERFLOW", "ICCID range runs past 18 digits")
    last_imsi = str(int(order.imsi_start) + order.quantity - 1).zfill(15)
    if len(last_imsi) > 15 or last_imsi[:5] != order.imsi_start[:5]:
        raise InputFileError("E_RANGE_OVERFLOW", "IMSI range runs past its MCC/MNC prefix")
    addr = order.smdp_address
    if order.is_esim and (len(addr) > 253 or not HOSTNAME.fullmatch(addr)):
        raise InputFileError("E_BAD_VALUE", f"SMDP_Address must be a hostname, got {addr!r}")
