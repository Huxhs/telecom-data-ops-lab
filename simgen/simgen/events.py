"""Structured event log (JSON lines) for Splunk.

One JSON object per line. Splunk reads these with sourcetype `_json` and
extracts every key as a field, so dashboards can do
`stats count by event, customer` without any parsing rules.

Never put keys, PINs or other secrets in an event.
"""

import json
import os
import sys
from datetime import datetime, timezone

FORBIDDEN = {"ki", "opc", "op", "pin1", "puk1", "pin2", "puk2", "adm1", "key"}


class EventLog:
    def __init__(self, path: str | None = None, app: str = "simgen", echo: bool = False):
        self.path = path
        self.app = app
        self.echo = echo  # also print to stdout, for `docker compose logs` and `kubectl logs`
        self.node = os.environ.get("HOSTNAME", "local")

    def emit(self, event: str, **fields) -> dict:
        bad = FORBIDDEN & {k.lower() for k in fields}
        if bad:
            raise ValueError(f"refusing to log secret fields: {sorted(bad)}")
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "app": self.app,  # not "source" either: also reserved
            "node": self.node,  # not "host": Splunk reserves that field
            "event": event,
            **fields,
        }
        line = json.dumps(record, separators=(",", ":"))
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            if self.echo:
                print(line, flush=True)
        else:
            print(line, file=sys.stderr)
        return record
