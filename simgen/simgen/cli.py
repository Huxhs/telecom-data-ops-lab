"""Command line for simgen. Run `simgen --help`."""

import argparse
import json
import os
import sys

from .db import OrderDB
from .events import EventLog
from .generator import process_order, qc_output_file
from .inputfile import InputFileError
from .keys import KeyStore, write_lab_keys
from .watcher import watch


def main(argv=None) -> int:
    env = os.environ.get
    p = argparse.ArgumentParser(prog="simgen", description="SIM profile generation lab")
    sub = p.add_subparsers(dest="cmd", required=True)

    k = sub.add_parser("init-keys", help="create a LAB ONLY key file")
    k.add_argument("path", nargs="?", default="lab-keys.json")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--keys", default=env("SIMGEN_KEYS", "lab-keys.json"))
    common.add_argument("--db", default=env("DATABASE_URL", "orders.db"))
    common.add_argument("--log", default=env("SIMGEN_LOG", "events.log"))

    g = sub.add_parser("generate", parents=[common], help="process one input file")
    g.add_argument("input")
    g.add_argument("--out", default="out")

    q = sub.add_parser("qc", help="quality-check an output file")
    q.add_argument("output")
    q.add_argument("--keys", default=None, help="also prove KI and OPC match")

    s = sub.add_parser("status", help="list orders and their status")
    s.add_argument("--db", default=env("DATABASE_URL", "orders.db"))

    w = sub.add_parser("watch", parents=[common], help="run the folder watcher")
    w.add_argument("--root", default=env("SIMGEN_ROOT", "data"))
    w.add_argument("--interval", type=float, default=2.0)
    w.add_argument("--stale-after", type=float, default=600.0)
    w.add_argument("--once", action="store_true", help="process the inbox once and exit")

    a = p.parse_args(argv)

    if a.cmd == "init-keys":
        write_lab_keys(a.path)
        print(f"wrote {a.path} (LAB ONLY keys, file mode 600)")
        return 0

    if a.cmd == "generate":
        db = OrderDB(a.db)
        try:
            summary = process_order(a.input, a.out, KeyStore.load(a.keys), db, EventLog(a.log))
        except InputFileError as e:
            print(f"FAILED {e.code}: {e.message}", file=sys.stderr)
            return 1
        finally:
            db.close()
        print(json.dumps(summary, indent=2))
        return 0

    if a.cmd == "qc":
        problems = qc_output_file(a.output, KeyStore.load(a.keys) if a.keys else None)
        for line in problems:
            print("FAIL", line)
        print("QC PASSED" if not problems else f"QC FAILED ({len(problems)} problems)")
        return 0 if not problems else 1

    if a.cmd == "status":
        db = OrderDB(a.db)
        rows = db.orders()
        db.close()
        print(f"{'ORDER':24} {'CUSTOMER':12} {'BATCH':11} {'QTY':>7} {'STATUS':11} {'ERROR':18} DUE")
        for r in rows:
            print(f"{r[0]:24} {r[1]:12} {r[2]:11} {r[3]:>7} {r[4]:11} {r[5] or '':18} {r[7]}")
        return 0

    if a.cmd == "watch":
        watch(a.root, a.keys, a.db, a.log, a.interval, a.stale_after, a.once)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
