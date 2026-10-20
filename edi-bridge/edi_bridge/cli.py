"""Command line: edi-bridge pretty | validate | inbound | asn | sap.

Exit codes: 0 = everything fine, 1 = something needs a human (bad file,
mapping error, unreadable input). A scheduler can alert on exit code 1.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

from . import ack855, ack997, asn856, idoc, m850, mapping, x12
from .envelope import ControlNumbers, Party
from .pretty import pretty

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
COUNTER_FILE = ".edi_control.json"
INBOUND_OUTPUTS = ["997.x12", "855.x12", "orders.json", "erp_orders.json",
                   "co_header.csv", "co_lines.csv"]


def read_edi(path: str) -> str:
    # X12 is plain ASCII. latin-1 decodes any byte, so a stray non-ASCII byte
    # becomes an odd character instead of crashing the reader.
    return Path(path).read_text(encoding="latin-1")


def cmd_pretty(args: argparse.Namespace) -> int:
    print(pretty(read_edi(args.file)))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    ic, errors = x12.read_x12(read_edi(args.file))
    for e in errors:
        print(f"ERROR {e}")
    if errors:
        print(f"{args.file}: {len(errors)} error(s)")
        return 1
    sets = sum(len(g.transactions) for g in ic.groups)
    print(f"{args.file}: OK (interchange {ic.control_number}, "
          f"{len(ic.groups)} group(s), {sets} transaction set(s))")
    return 0


def cmd_inbound(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name in INBOUND_OUTPUTS:  # never leave a previous run's files lying around
        (out / name).unlink(missing_ok=True)
    partners = mapping.load_partners(args.partners)
    items = mapping.load_items(args.items)

    ic, errors = x12.read_x12(read_edi(args.file))
    for e in errors:
        print(f"ERROR {e}")
    if ic is None:
        print("no readable ISA header: the sender is unknown, so no 997 can be sent")
        return 1
    counter = ControlNumbers(out / COUNTER_FILE)
    now = datetime.now()

    # Step 1: a 997 always goes back, good file or bad.
    (out / "997.x12").write_text(ack997.make_997(ic, errors, counter, now), encoding="latin-1")
    print(f"interchange {ic.control_number} from {ic.sender_id}: "
          f"{len(errors)} envelope/syntax error(s), 997 written")

    # Step 2: translate every 850 the 997 accepted, then cross-reference it.
    interchange_ok = not any(e.group is None for e in errors)
    orders, records, acks, mapping_problems = [], [], [], 0
    for group in ic.groups:
        for ts in group.transactions:
            label = f"  {ts.set_id} {ts.control_number}"
            if ts.set_id != "850":
                print(f"{label}: skipped, only 850 is mapped")
                continue
            if not interchange_ok or errors_in_scope(errors, group, ts):
                print(f"{label}: rejected in the 997, not imported")
                continue
            order = m850.map_850(ts, ic)
            orders.append(order)
            record, problems = mapping.map_order(order, partners, items)
            mapping_problems += len(problems)
            for p in problems:
                print(f"{label}: MAPPING {p.code} {p.message}")
            if record:
                records.append(record)
                print(f"{label}: PO {order.po_number} -> ERP order {record.co_num} "
                      f"({len(record.lines)} line(s))")
            rejected = {p.line_no for p in problems}
            if None in rejected:  # a whole-order problem rejects every line
                rejected = {line.line_no for line in order.lines}
            acks.append((order, rejected))

    # Step 3: write what we have.
    if orders:
        mapping.write_json(orders, out / "orders.json")
    if records:
        mapping.write_json(records, out / "erp_orders.json")
        mapping.write_erp_csv(records, out)
    if acks:
        (out / "855.x12").write_text(ack855.make_855(acks, ic, counter, now), encoding="latin-1")
    written = [name for name in INBOUND_OUTPUTS if (out / name).exists()]
    print(f"written to {out}: {', '.join(written)}")
    return 1 if errors or mapping_problems else 0


def errors_in_scope(errors: list[x12.EdiError], group: x12.FunctionalGroup,
                    ts: x12.TransactionSet) -> bool:
    """True if the set itself or its group has a problem."""
    return bool(x12.errors_for(errors, group) or x12.errors_for(errors, group, ts))


def cmd_asn(args: argparse.Namespace) -> int:
    summary = asn856.load_summary(args.summary)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    # Real systems look the partner's ISA ID up in a trading-partner profile;
    # here "MNO-NORTH" simply becomes "MNONORTH".
    receiver = args.receiver or re.sub(r"[^A-Z0-9]", "", summary["customer"].upper())
    text = asn856.make_856(summary, Party(args.sender), Party(receiver),
                           ControlNumbers(out / COUNTER_FILE), carrier=args.carrier,
                           bol=args.bol, ship_to_name=args.ship_to_name,
                           ship_to_id=args.ship_to_id)
    path = out / f"856_{re.sub(r'[^A-Za-z0-9_-]', '_', summary['batch'])}.x12"
    path.write_text(text, encoding="latin-1")
    print(f"856 for PO {summary['po_number']}, batch {summary['batch']}, "
          f"{summary['quantity']} unit(s) written to {path}")
    return 0


def cmd_sap(args: argparse.Namespace) -> int:
    orders = idoc.load_erp_orders(args.erp_orders)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    text = idoc.make_idoc_xml(orders, ControlNumbers(out / COUNTER_FILE), sender=args.sender,
                              receiver=args.receiver, client=args.client,
                              partners=mapping.load_partners(args.partners),
                              items=mapping.load_items(args.items))
    path = out / "idoc_orders.xml"
    path.write_text(text, encoding="utf-8")
    print(f"{len(orders)} {idoc.IDOC_TYPE} IDoc(s) for PO {', '.join(o['cust_po'] for o in orders)} "
          f"to {args.receiver} written to {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="edi-bridge",
                                     description="Learning tool for ANSI X12 850/855/856/997 "
                                                 "and SAP IDocs.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("pretty", help="print a file one labelled segment per line")
    p.add_argument("file")
    p.set_defaults(func=cmd_pretty)

    p = sub.add_parser("validate", help="check envelopes, counts and required segments")
    p.add_argument("file")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("inbound", help="process an inbound 850 file into 997, 855 and ERP files")
    p.add_argument("file")
    p.add_argument("--out", required=True, help="output directory")
    p.add_argument("--partners", default=str(SAMPLES / "partners.csv"))
    p.add_argument("--items", default=str(SAMPLES / "items.csv"))
    p.set_defaults(func=cmd_inbound)

    p = sub.add_parser("asn", help="build an 856 ship notice from a simgen shipment summary")
    p.add_argument("summary")
    p.add_argument("--out", required=True, help="output directory")
    p.add_argument("--carrier", default="DEMO", help="carrier SCAC code (default DEMO)")
    p.add_argument("--bol", help="bill of lading number (default BOL<batch>)")
    p.add_argument("--ship-to-name", help="default: the summary's customer")
    p.add_argument("--ship-to-id", help="default: the summary's customer")
    p.add_argument("--sender", default="GDMOBILE", help="our ISA ID (default GDMOBILE)")
    p.add_argument("--receiver", help="partner ISA ID (default: customer without dashes)")
    p.set_defaults(func=cmd_asn)

    p = sub.add_parser("sap", help="pass ERP orders on to SAP as ORDERS05 IDocs (XML)")
    p.add_argument("erp_orders", help="erp_orders.json written by inbound")
    p.add_argument("--out", required=True, help="output directory")
    p.add_argument("--sender", default="SYTELINE", help="our logical system (default SYTELINE)")
    p.add_argument("--receiver", default="SAPCLNT100",
                   help="SAP logical system (default SAPCLNT100)")
    p.add_argument("--client", default="100", help="SAP client, MANDT (default 100)")
    p.add_argument("--partners", default=str(SAMPLES / "partners.csv"))
    p.add_argument("--items", default=str(SAMPLES / "items.csv"))
    p.set_defaults(func=cmd_sap)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyError as exc:
        print(f"error: missing column or key {exc}", file=sys.stderr)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
