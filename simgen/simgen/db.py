"""Order tracking database: SQLite by default, PostgreSQL or Oracle when DATABASE_URL says so.

  sqlite:///orders.db  or a plain path            (laptop, tests)
  postgresql://user:password@host:5432/dbname     (Docker stack)
  oracle://user:password@host:1521/SERVICE        (Docker stack with compose.oracle.yaml)

Two tables:
  orders       one row per order, with its status as it moves through the pipeline
  allocations  the ICCID and IMSI ranges each order holds, reserved before any
               card is made, so an overlapping order is refused BEFORE duplicates exist

Status flow: RECEIVED -> GENERATING -> QC_PASSED -> DELIVERED, or FAILED.
"""

import itertools
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS orders (
        order_id      VARCHAR(40) PRIMARY KEY,
        customer      VARCHAR(40) NOT NULL,
        po_ref        VARCHAR(40) NOT NULL,
        batch         VARCHAR(40) NOT NULL,
        card_type     VARCHAR(40) NOT NULL,
        quantity      INTEGER     NOT NULL,
        status        VARCHAR(20) NOT NULL,
        error_code    VARCHAR(40),
        received_at   VARCHAR(32) NOT NULL,
        due_at        VARCHAR(32) NOT NULL,
        completed_at  VARCHAR(32)
    )""",
    """CREATE TABLE IF NOT EXISTS allocations (
        order_id     VARCHAR(40) NOT NULL REFERENCES orders(order_id),
        kind         VARCHAR(5)  NOT NULL,
        first_value  VARCHAR(20) NOT NULL,
        last_value   VARCHAR(20) NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class OrderDB:
    def __init__(self, url: str = "orders.db"):
        if url.startswith("postgresql://") or url.startswith("postgres://"):
            import psycopg  # only needed for the Docker stack

            # autocommit: plain reads end immediately; writes group in transaction()
            self.conn = psycopg.connect(url, autocommit=True)
            self.kind = "postgres"
        elif url.startswith("oracle://"):
            import oracledb  # thin mode: no Oracle client install needed

            u = urlsplit(url)
            self.conn = oracledb.connect(user=unquote(u.username), password=unquote(u.password or ""),
                                         dsn=f"{u.hostname}:{u.port or 1521}{u.path}")
            self.kind = "oracle"
        else:
            self.conn = sqlite3.connect(url.removeprefix("sqlite:///"))
            self.kind = "sqlite"
        with self.transaction():
            cur = self.conn.cursor()
            if self.kind == "postgres":
                # Several workers may start at once; only one creates the tables at a time.
                cur.execute("SELECT pg_advisory_xact_lock(4242)")
            for stmt in SCHEMA:
                try:
                    cur.execute(stmt)
                except Exception as e:
                    # Oracle: two workers raced on IF NOT EXISTS; the object exists now.
                    if self.kind != "oracle" or not re.search(r"ORA-0(0955|1408)", str(e)):
                        raise

    @contextmanager
    def transaction(self):
        """All statements inside commit together, or roll back together on error."""
        if self.kind == "postgres":
            with self.conn.transaction():
                yield
        elif self.kind == "oracle":
            try:
                yield
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise
        else:
            with self.conn:  # sqlite3: commit on success, rollback on exception
                yield

    def _q(self, sql: str) -> str:
        if self.kind == "postgres":
            return sql.replace("?", "%s")
        if self.kind == "oracle":
            n = itertools.count(1)
            return re.sub(r"\?", lambda _: f":{next(n)}", sql)
        return sql

    def execute(self, sql: str, args=()):
        cur = self.conn.cursor()
        cur.execute(self._q(sql), args)
        return cur

    def get(self, order_id: str):
        row = self.execute("SELECT order_id, status FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        return row

    def receive(self, order, due_at: str) -> None:
        with self.transaction():
            self.execute(
                "INSERT INTO orders (order_id, customer, po_ref, batch, card_type, quantity,"
                " status, received_at, due_at) VALUES (?, ?, ?, ?, ?, ?, 'RECEIVED', ?, ?)",
                (order.order_id, order.customer, order.po_ref, order.batch, order.card_type,
                 order.quantity, now_iso(), due_at),
            )

    def set_status(self, order_id: str, status: str, error_code: str | None = None) -> None:
        done = now_iso() if status in ("DELIVERED", "FAILED") else None
        with self.transaction():
            self.execute(
                "UPDATE orders SET status = ?, error_code = ?, completed_at = COALESCE(?, completed_at)"
                " WHERE order_id = ?",
                (status, error_code, done, order_id),
            )

    def overlapping(self, kind: str, first: str, last: str):
        """Orders whose allocated range of `kind` overlaps [first, last].

        Values are fixed-width digit strings, so text comparison orders them correctly.
        """
        return self.execute(
            "SELECT order_id, first_value, last_value FROM allocations"
            " WHERE kind = ? AND first_value <= ? AND last_value >= ?",
            (kind, last, first),
        ).fetchall()

    def reserve(self, order_id: str, ranges):
        """Claim an order's (kind, first, last) ranges, or return the first clash.

        The check and the insert run under one table lock, so two workers can never
        both see a range as free and both use it. Returns None when reserved, else
        (kind, first, last, clashing_order_id).
        """
        with self.transaction():
            if self.kind == "sqlite":
                self.execute("BEGIN IMMEDIATE")  # take the write lock before reading
            else:
                self.execute("LOCK TABLE allocations IN EXCLUSIVE MODE")  # held until commit
            for kind, first, last in ranges:
                clash = self.overlapping(kind, first, last)
                if clash:
                    return kind, first, last, clash[0][0]
            for kind, first, last in ranges:
                self.execute(
                    "INSERT INTO allocations (order_id, kind, first_value, last_value) VALUES (?, ?, ?, ?)",
                    (order_id, kind, first, last),
                )
        return None

    def release(self, order_id: str) -> None:
        """Free a failed order's ranges so a corrected file can use them."""
        with self.transaction():
            self.execute("DELETE FROM allocations WHERE order_id = ?", (order_id,))

    def orders(self):
        return self.execute(
            "SELECT order_id, customer, batch, quantity, status, error_code, received_at, due_at, completed_at"
            " FROM orders ORDER BY received_at DESC"
        ).fetchall()

    def close(self) -> None:
        self.conn.close()
