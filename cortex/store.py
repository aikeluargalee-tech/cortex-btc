"""SQLite persistence: event log, decisions, orders and configuration versions.

The database is a record of what happened, not a source of truth that can be
edited. Snapshots and decisions are append-only; orders move only through the
documented lifecycle.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import Config
from .domain import Decision, dec_str, iso, utc_now
from .ledger import Ledger, LedgerEvent

SCHEMA = """
CREATE TABLE IF NOT EXISTS config_versions (
    version     TEXT PRIMARY KEY,
    hash        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    operator    TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS events (
    seq         INTEGER PRIMARY KEY,
    event_type  TEXT NOT NULL,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    replay_key  TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL,
    verdict     TEXT NOT NULL,
    payload     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id       TEXT PRIMARY KEY,
    decision_id    TEXT NOT NULL,
    side           TEXT NOT NULL,
    status         TEXT NOT NULL,
    limit_price    TEXT NOT NULL,
    quantity       TEXT NOT NULL,
    filled_quantity TEXT NOT NULL DEFAULT '0',
    fee_usdt       TEXT NOT NULL DEFAULT '0',
    payload        TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    TEXT NOT NULL,
    status      TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- configuration -----------------------------------------------------
    def save_config(self, config: Config) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO config_versions (version, hash, payload, created_at, operator, note)"
                " VALUES (?,?,?,?,?,?)",
                (
                    config.config_version,
                    config.config_hash,
                    json.dumps(config.to_dict(), sort_keys=True),
                    iso(config.created_at),
                    config.operator,
                    config.note,
                ),
            )

    def latest_config(self) -> Config | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM config_versions ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return Config.from_dict(json.loads(row["payload"]))

    def config_versions(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT version, hash, created_at, operator, note FROM config_versions ORDER BY created_at DESC, rowid DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    # -- ledger ------------------------------------------------------------
    def append_event(self, event: LedgerEvent) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO events (seq, event_type, payload, created_at) VALUES (?,?,?,?)",
                (event.seq, event.event_type.value, json.dumps(event.to_dict(), sort_keys=True), iso(event.created_at)),
            )

    def events(self) -> list[LedgerEvent]:
        with self._connect() as conn:
            rows = conn.execute("SELECT payload FROM events ORDER BY seq ASC").fetchall()
        return [LedgerEvent.from_dict(json.loads(row["payload"])) for row in rows]

    def ledger(self) -> Ledger:
        return Ledger(self.events())

    def is_seeded(self) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(1) AS n FROM events WHERE event_type = 'SEED'").fetchone()
        return bool(row and row["n"])

    # -- decisions ---------------------------------------------------------
    def save_decision(self, decision: Decision) -> bool:
        """Idempotent: the same replay key can never create a second record."""
        payload = json.dumps(decision.to_dict(), sort_keys=True, default=str)
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT decision_id FROM decisions WHERE replay_key = ?", (decision.replay_key,)
            ).fetchone()
            if existing is not None:
                return False
            conn.execute(
                "INSERT INTO decisions (decision_id, replay_key, created_at, verdict, payload) VALUES (?,?,?,?,?)",
                (
                    decision.decision_id,
                    decision.replay_key,
                    iso(decision.created_at),
                    decision.verdict.value,
                    payload,
                ),
            )
        return True

    def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM decisions WHERE decision_id = ?", (decision_id,)).fetchone()
        return None if row is None else json.loads(row["payload"])

    def latest_decision(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM decisions ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        return None if row is None else json.loads(row["payload"])

    def decisions(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM decisions ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    # -- orders ------------------------------------------------------------
    def create_order(self, order: dict[str, Any], decision_id: str) -> None:
        now = iso(utc_now())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO orders (order_id, decision_id, side, status, limit_price, quantity, filled_quantity, fee_usdt, payload, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    order["order_id"],
                    decision_id,
                    order["side"],
                    order["status"],
                    order["limit_price"],
                    order["quantity"],
                    "0",
                    "0",
                    json.dumps(order, sort_keys=True),
                    now,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO order_events (order_id, status, note, created_at) VALUES (?,?,?,?)",
                (order["order_id"], order["status"], "created by operator approval", now),
            )

    def order(self, order_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        return None if row is None else dict(row)

    def orders(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def update_order(self, order_id: str, *, status: str, filled: str, fee: str, note: str) -> None:
        now = iso(utc_now())
        with self._connect() as conn:
            conn.execute(
                "UPDATE orders SET status = ?, filled_quantity = ?, fee_usdt = ?, updated_at = ? WHERE order_id = ?",
                (status, filled, fee, now, order_id),
            )
            conn.execute(
                "INSERT INTO order_events (order_id, status, note, created_at) VALUES (?,?,?,?)",
                (order_id, status, note, now),
            )

    def order_events(self, order_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, note, created_at FROM order_events WHERE order_id = ? ORDER BY id ASC", (order_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    # -- meta --------------------------------------------------------------
    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, value))

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return default if row is None else row["value"]


__all__ = ["Store", "SCHEMA"]
