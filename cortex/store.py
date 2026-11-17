"""SQLite persistence: decisions and configuration versions.

The database is an append-only record of what was observed and published.
Decisions are keyed by replay hash, so reprocessing a snapshot cannot create a
second record.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import Config
from .domain import Decision, iso, utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS config_versions (
    version     TEXT PRIMARY KEY,
    hash        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    operator    TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    replay_key  TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL,
    verdict     TEXT NOT NULL,
    payload     TEXT NOT NULL
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
                "SELECT version, hash, created_at, operator, note FROM config_versions"
                " ORDER BY created_at DESC, rowid DESC"
            ).fetchall()
        return [dict(row) for row in rows]

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

    # -- meta --------------------------------------------------------------
    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, value))

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return default if row is None else row["value"]


__all__ = ["Store", "SCHEMA"]
