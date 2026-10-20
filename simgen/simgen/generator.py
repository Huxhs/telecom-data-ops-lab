"""Profile generation: input file in, output file + perso file + summary out.

eSIM orders get no perso file. They get an SM-DP+ file (profile data for the
download server) and an activation file (one activation code per profile).

Pipeline for one order (each step is logged as an event for Splunk):
  1. parse and validate the input file
  2. refuse duplicates (same Order_ID already delivered)
  3. record the order and reserve its ranges (refused if they overlap another order)
  4. generate one profile per card (ICCID, IMSI, keys, PINs)
  5. write the operator output file and the factory perso file (or the eSIM files)
  6. quality check (QC) the output file before anything is released
  7. write a shipment summary, mark the order delivered
A failed order releases its ranges so a corrected file can reuse them.
"""

import csv
import json
import os
import re
import secrets
import string
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .db import OrderDB
from .events import EventLog
from .identifiers import iccid_range, imsi_range, luhn_valid
from .inputfile import InputFileError, Order, parse_input_file
from .keys import KeyStore, compute_opc, decrypt_from_transport, encrypt_for_transport

OUT_VARS = ["ICCID", "IMSI", "PIN1", "PUK1", "PIN2", "PUK2", "KI", "OPC", "ADM1"]
ACTIVATION_CODE = re.compile(r"LPA:1\$([^$]+)\$([A-Z0-9]{5}(?:-[A-Z0-9]{5}){3})")


def _digits(n: int) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(n))


def matching_id() -> str:
    """Random MatchingID, 4 groups of 5 from A-Z0-9 (about 103 bits). QC proves no repeats."""
    chars = string.ascii_uppercase + string.digits
    return "-".join("".join(secrets.choice(chars) for _ in range(5)) for _ in range(4))


def activation_code(smdp_address: str, mid: str) -> str:
    """SGP.22 activation code, the text inside the customer's QR code."""
    return f"LPA:1${smdp_address}${mid}"


def generate_profiles(order: Order, keys: KeyStore):
    """Yield one dict per card. Ki is random per card; OPc is derived from it."""
    tk = keys.transport_key(order.transport_key_id)
    for iccid, imsi in zip(iccid_range(order.iccid_start, order.quantity),
                           imsi_range(order.imsi_start, order.quantity)):
        ki = secrets.token_bytes(16)
        opc = compute_opc(ki, keys.op)
        p = {
            "ICCID": iccid,
            "IMSI": imsi,
            "PIN1": "0000",            # operator default PIN, set by the profile spec
            "PUK1": _digits(8),
            "PIN2": _digits(4),
            "PUK2": _digits(8),
            "KI": encrypt_for_transport(tk, ki),
            "OPC": encrypt_for_transport(tk, opc),
            "ADM1": _digits(8),
            "_ki_perso": encrypt_for_transport(keys.perso_key, ki),
            "_opc_perso": encrypt_for_transport(keys.perso_key, opc),
        }
        if order.is_esim:
            p["MATCHING_ID"] = matching_id()
            p["ACTIVATION_CODE"] = activation_code(order.smdp_address, p["MATCHING_ID"])
        yield p


def write_output_file(order: Order, profiles, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".part")
    with open(tmp, "w", encoding="ascii", newline="\n") as f:
        f.write("*HEADER DESCRIPTION\n")
        for key, value in [
            ("Customer", order.customer), ("Order_ID", order.order_id), ("PO_Ref", order.po_ref),
            ("Quantity", order.quantity), ("Type", order.card_type), ("Profile", order.profile),
            ("Batch", order.batch), ("Transport_Key_ID", order.transport_key_id),
        ]:
            f.write(f"{key}: {value}\n")
        f.write("*INPUT VARIABLES\n")
        f.write(f"ICCID_Start: {order.iccid_start}\nIMSI_Start: {order.imsi_start}\n")
        f.write("*OUTPUT VARIABLES\n")
        f.write("var_Out: " + "/".join(OUT_VARS) + "\n")
        for p in profiles:
            f.write(" ".join(p[v] for v in OUT_VARS) + "\n")
        f.write("*END\n")
    os.replace(tmp, path)  # atomic: readers never see a half-written file


def _write_csv(path: Path, header, rows) -> None:
    tmp = path.with_suffix(path.suffix + ".part")
    with open(tmp, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    os.replace(tmp, path)


def write_perso_file(profiles, path: Path) -> None:
    _write_csv(path, ["ICCID", "IMSI", "KI_PERSO", "OPC_PERSO", "PIN1", "PUK1", "PIN2", "PUK2", "ADM1"],
               ([p["ICCID"], p["IMSI"], p["_ki_perso"], p["_opc_perso"],
                 p["PIN1"], p["PUK1"], p["PIN2"], p["PUK2"], p["ADM1"]] for p in profiles))


def write_smdp_file(profiles, path: Path) -> None:
    """Profile data for the SM-DP+. KI and OPC use the same key as the perso file;
    the PINs match the operator's output file, as they would on a physical card."""
    _write_csv(path, ["ICCID", "IMSI", "KI_PERSO", "OPC_PERSO", "PIN1", "PUK1", "PIN2", "PUK2", "ADM1",
                      "MATCHING_ID"],
               ([p["ICCID"], p["IMSI"], p["_ki_perso"], p["_opc_perso"],
                 p["PIN1"], p["PUK1"], p["PIN2"], p["PUK2"], p["ADM1"], p["MATCHING_ID"]]
                for p in profiles))


def write_activation_file(profiles, path: Path) -> None:
    """Activation codes for the operator to turn into QR codes. No keys."""
    _write_csv(path, ["ICCID", "MATCHING_ID", "ACTIVATION_CODE"],
               ([p["ICCID"], p["MATCHING_ID"], p["ACTIVATION_CODE"]] for p in profiles))


def read_output_file(path):
    """Return (header dict, list of row dicts) from an output file."""
    header, rows, cols, section = {}, [], None, None
    for line in Path(path).read_text(encoding="ascii").splitlines():
        if line.startswith("*"):
            section = line
            continue
        if section == "*HEADER DESCRIPTION" and ":" in line:
            k, v = line.split(":", 1)
            header[k.strip()] = v.strip()
        elif section == "*OUTPUT VARIABLES":
            if line.startswith("var_Out:"):
                cols = line.split(":", 1)[1].strip().split("/")
            elif line.strip():
                rows.append(dict(zip(cols, line.split())))
    return header, rows


def qc_output_file(path, keys: KeyStore | None = None) -> list[str]:
    """Quality checks an output file must pass before release. Returns problems found."""
    header, rows = read_output_file(path)
    problems = []
    expected = int(header.get("Quantity", -1))
    if len(rows) != expected:
        problems.append(f"row count {len(rows)} != Quantity {expected}")
    seen_iccid, seen_imsi = set(), set()
    tk = keys.transport_key(header["Transport_Key_ID"]) if keys else None
    for n, r in enumerate(rows, start=1):
        iccid, imsi = r.get("ICCID", ""), r.get("IMSI", "")
        if len(iccid) != 19 or not luhn_valid(iccid):
            problems.append(f"row {n}: bad ICCID {iccid}")
        if len(imsi) != 15 or not imsi.isdigit():
            problems.append(f"row {n}: bad IMSI {imsi}")
        if iccid in seen_iccid:
            problems.append(f"row {n}: duplicate ICCID {iccid}")
        if imsi in seen_imsi:
            problems.append(f"row {n}: duplicate IMSI {imsi}")
        seen_iccid.add(iccid)
        seen_imsi.add(imsi)
        for col, length in (("PIN1", 4), ("PIN2", 4), ("PUK1", 8), ("PUK2", 8), ("ADM1", 8)):
            v = r.get(col, "")
            if len(v) != length or not v.isdigit():
                problems.append(f"row {n}: bad {col}")
        for col in ("KI", "OPC"):
            v = r.get(col, "")
            if len(v) != 32 or any(c not in "0123456789ABCDEF" for c in v):
                problems.append(f"row {n}: {col} is not 32 hex characters")
        if tk and not problems:
            # Decrypt and re-derive: proves KI and OPC belong together.
            ki = decrypt_from_transport(tk, r["KI"])
            if compute_opc(ki, keys.op) != decrypt_from_transport(tk, r["OPC"]):
                problems.append(f"row {n}: OPC does not match KI")
        if len(problems) > 20:
            problems.append("too many problems, stopping")
            break
    return problems


def qc_activation_file(out_path, activation_path, smdp_address: str) -> list[str]:
    """eSIM QC: unique MatchingIDs, well-formed codes for this SM-DP+, same ICCIDs as the .out.

    Problems name the ICCID, never the MatchingID: they end up in the event log.
    """
    _, out_rows = read_output_file(out_path)
    with open(activation_path, newline="") as f:
        rows = list(csv.DictReader(f))
    problems, seen = [], set()
    for n, r in enumerate(rows, start=1):
        iccid, mid = r.get("ICCID", ""), r.get("MATCHING_ID", "")
        if mid in seen:
            problems.append(f"row {n}: duplicate MatchingID (ICCID {iccid})")
        seen.add(mid)
        m = ACTIVATION_CODE.fullmatch(r.get("ACTIVATION_CODE", ""))
        if not m or m[2] != mid:
            problems.append(f"row {n}: bad activation code (ICCID {iccid})")
        elif m[1] != smdp_address:
            problems.append(f"row {n}: activation code points to {m[1]}, not {smdp_address}")
        if len(problems) > 20:
            problems.append("too many problems, stopping")
            break
    if sorted(r.get("ICCID", "") for r in out_rows) != sorted(r.get("ICCID", "") for r in rows):
        problems.append("ICCIDs in the activation file differ from the output file")
    return problems


def process_order(input_path, out_dir, keys: KeyStore, db: OrderDB, log: EventLog) -> dict:
    """Run the whole pipeline for one input file. Returns the shipment summary."""
    started = time.monotonic()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = Path(input_path).name.rsplit("@", 1)[0]  # drop the worker claim suffix

    try:
        order = parse_input_file(input_path)
    except InputFileError as e:
        log.emit("order_rejected", file=name, error_code=e.code, message=e.message)
        raise

    ids = dict(order_id=order.order_id, customer=order.customer, batch=order.batch)
    existing = db.get(order.order_id)
    if existing and existing[1] != "FAILED":
        log.emit("order_rejected", file=name, error_code="E_DUPLICATE_ORDER",
                 message=f"order already {existing[1]}", **ids)
        raise InputFileError("E_DUPLICATE_ORDER", f"{order.order_id} already {existing[1]}")
    if existing:  # a failed order may be resubmitted after the fix
        with db.transaction():
            db.execute("DELETE FROM allocations WHERE order_id = ?", (order.order_id,))
            db.execute("DELETE FROM orders WHERE order_id = ?", (order.order_id,))

    due = datetime.now(timezone.utc) + timedelta(hours=order.sla_hours)
    due_at = due.strftime("%Y-%m-%dT%H:%M:%SZ")
    db.receive(order, due_at)
    log.emit("order_received", file=name, quantity=order.quantity, card_type=order.card_type,
             profile_kind=order.profile_kind, po_ref=order.po_ref, due_at=due_at, **ids)

    def fail(code: str, message: str):
        db.release(order.order_id)  # a failed order holds no ranges
        db.set_status(order.order_id, "FAILED", code)
        log.emit("order_failed", error_code=code, message=message, **ids)
        raise InputFileError(code, message)

    # Claim the ranges before generating, so a second worker sees them as taken.
    iccids = list(iccid_range(order.iccid_start, order.quantity))
    imsis = list(imsi_range(order.imsi_start, order.quantity))
    clash = db.reserve(order.order_id, [("ICCID", iccids[0], iccids[-1]), ("IMSI", imsis[0], imsis[-1])])
    if clash:
        kind, first, last, other = clash
        fail("E_RANGE_OVERLAP", f"{kind} {first}-{last} overlaps order {other}")

    try:
        keys.transport_key(order.transport_key_id)
    except KeyError as e:
        fail("E_UNKNOWN_KEY", e.args[0])

    db.set_status(order.order_id, "GENERATING")
    log.emit("generation_started", quantity=order.quantity, **ids)

    profiles = list(generate_profiles(order, keys))
    base = f"{order.customer}_{order.batch}"
    out_file = out_dir / f"{base}.out"
    perso_file = out_dir / f"{base}.perso.csv"
    smdp_file = out_dir / f"{base}.smdp.csv"
    activation_file = out_dir / f"{base}.activation.csv"
    try:
        write_output_file(order, profiles, out_file)
        if order.is_esim:
            write_smdp_file(profiles, smdp_file)
            write_activation_file(profiles, activation_file)
        else:
            write_perso_file(profiles, perso_file)
    except OSError as e:
        fail("E_WRITE", f"could not write output: {e}")

    problems = qc_output_file(out_file, keys)
    if order.is_esim:
        problems += qc_activation_file(out_file, activation_file, order.smdp_address)
    if problems:
        out_file.rename(out_file.with_suffix(".out.rejected"))
        if order.is_esim:  # no QR codes or downloadable profiles from a rejected order
            for f in (smdp_file, activation_file):
                f.rename(f.with_name(f.name + ".rejected"))
        fail("E_QC", "; ".join(problems[:5]))
    db.set_status(order.order_id, "QC_PASSED")

    summary = {
        "order_id": order.order_id,
        "customer": order.customer,
        "po_number": order.po_ref,
        "batch": order.batch,
        "product": order.card_type,
        "profile_kind": order.profile_kind,
        "quantity": order.quantity,
        "iccid_first": profiles[0]["ICCID"],
        "iccid_last": profiles[-1]["ICCID"],
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "output_file": out_file.name,
    }
    (out_dir / f"{base}.summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    db.set_status(order.order_id, "DELIVERED")
    duration_ms = int((time.monotonic() - started) * 1000)
    log.emit("order_completed", quantity=order.quantity, duration_ms=duration_ms,
             sla_met=datetime.now(timezone.utc) <= due, output_file=out_file.name, **ids)
    return summary
