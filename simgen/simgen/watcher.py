"""Folder watcher: the data-center loop that picks up input files as they arrive.

Folders under ROOT:
  inbox/       operators drop input files here (over SFTP in the Docker stack)
  processing/  a worker claims a file by MOVING it here
  outbox/      output, perso and summary files for pickup
  archive/     input files that were processed successfully
  error/       input files that failed, each with a .reason file next to it

Why claim by rename: renaming a file is atomic on one filesystem. If three
workers see the same file, exactly one rename succeeds and the other two get
FileNotFoundError and move on. That is how you scale to several workers
(Kubernetes replicas) without processing an order twice.
"""

import os
import socket
import time
from pathlib import Path

from .db import OrderDB
from .events import EventLog
from .generator import process_order
from .inputfile import InputFileError
from .keys import KeyStore

FOLDERS = ("inbox", "processing", "outbox", "archive", "error")


def ensure_folders(root: Path) -> None:
    for name in FOLDERS:
        (root / name).mkdir(parents=True, exist_ok=True)


def requeue_stale(root: Path, stale_after: float, log: EventLog) -> int:
    """Move claims older than `stale_after` seconds back to the inbox.

    A claim goes stale when a worker dies mid-order (pod killed, node lost).
    """
    moved = 0
    for f in (root / "processing").iterdir():
        if time.time() - f.stat().st_mtime > stale_after:
            original = f.name.rsplit("@", 1)[0]
            try:
                os.rename(f, root / "inbox" / original)
            except FileNotFoundError:
                continue
            log.emit("claim_requeued", file=original, stale_seconds=int(stale_after))
            moved += 1
    return moved


def run_once(root: Path, keys: KeyStore, db: OrderDB, log: EventLog, worker: str) -> int:
    """Process every file currently in the inbox. Returns how many were claimed."""
    claimed = 0
    for f in sorted((root / "inbox").iterdir()):
        if f.name.startswith(".") or f.suffix == ".part" or not f.is_file():
            continue  # hidden or still uploading
        claim = root / "processing" / f"{f.name}@{worker}"
        try:
            os.rename(f, claim)
        except FileNotFoundError:
            continue  # another worker got it first
        try:
            os.utime(claim)  # the claim's age starts now, not at upload time
        except PermissionError:
            pass  # file owned by another user; its upload time will do
        claimed += 1
        try:
            process_order(claim, root / "outbox", keys, db, log)
            os.rename(claim, root / "archive" / f.name)
        except InputFileError as e:
            os.rename(claim, root / "error" / f.name)
            (root / "error" / f"{f.name}.reason").write_text(f"{e.code}: {e.message}\n")
        except Exception as e:
            # Unexpected (database down, disk full...): park the file, log loudly, then
            # crash on purpose. Docker or Kubernetes restarts the worker with fresh
            # connections; an operator resubmits the parked file once the cause is fixed.
            os.rename(claim, root / "error" / f.name)
            (root / "error" / f"{f.name}.reason").write_text(f"E_INTERNAL: {e!r}\n")
            log.emit("worker_error", file=f.name, error_code="E_INTERNAL", message=repr(e), worker=worker)
            raise
    return claimed


def watch(root, keys_path, db_url, log_path, interval=2.0, stale_after=600.0, once=False) -> None:
    root = Path(root)
    ensure_folders(root)
    keys = KeyStore.load(keys_path)
    db = OrderDB(db_url)
    log = EventLog(log_path, echo=True)
    worker = os.environ.get("HOSTNAME") or socket.gethostname()
    log.emit("worker_started", worker=worker, root=str(root))
    try:
        while True:
            requeue_stale(root, stale_after, log)
            run_once(root, keys, db, log, worker)
            if once:
                return
            time.sleep(interval)
    finally:
        db.close()
