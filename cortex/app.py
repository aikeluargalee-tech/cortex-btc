"""Application services: orchestration between market data, authority and store."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .authority import decide
from .config import Config, empty_config
from .domain import DataStatus, MarketSnapshot, iso, utc_now
from .market import build_snapshot
from .store import Store

SNAPSHOT_TTL_SECONDS = 45

# Contextual feeds may be absent without degrading a stance; they are telemetry,
# not decision inputs.
CONTEXTUAL_FEATURES = {"liquidations", "open_interest", "funding_rate"}


class AppError(RuntimeError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class CortexApp:
    def __init__(self, db_path: str | Path, seed_config: str | Path | None = None) -> None:
        self.store = Store(db_path)
        self._lock = threading.RLock()
        self._snapshot: MarketSnapshot | None = None
        self._snapshot_at: datetime | None = None
        self._ensure_config(seed_config)

    # -- configuration -----------------------------------------------------
    def _ensure_config(self, seed_config: str | Path | None) -> None:
        if self.store.latest_config() is not None:
            return
        config = empty_config()
        if seed_config is not None and Path(seed_config).exists():
            payload = json.loads(Path(seed_config).read_text(encoding="utf-8-sig"))
            config = Config.from_dict(payload)
        self.store.save_config(config)

    def config(self) -> Config:
        config = self.store.latest_config()
        if config is None:
            raise AppError("no configuration available", 500)
        return config

    def change_config(self, changes: dict[str, Any], *, operator: str, note: str) -> Config:
        if not operator.strip():
            raise AppError("operator is required for a configuration change")
        if not note.strip():
            raise AppError("a reason is required for a configuration change")
        current = self.config()
        previous = current.to_dict()
        version = self._next_config_version()
        updated = current.with_changes(changes, version=version, operator=operator, note=note)
        self.store.save_config(updated)
        record = {
            "version": version,
            "operator": operator,
            "note": note,
            "changes": {path: {"from": _dig(previous, path), "to": changes[path]} for path in changes},
        }
        history = json.loads(self.store.get_meta("config_history", "[]"))
        history.append(record)
        self.store.set_meta("config_history", json.dumps(history[-200:]))
        return updated

    def _next_config_version(self) -> str:
        existing = {row["version"] for row in self.store.config_versions()}
        index = 1
        while f"cfg-{index:04d}" in existing:
            index += 1
        return f"cfg-{index:04d}"

    def config_history(self) -> list[dict[str, Any]]:
        return json.loads(self.store.get_meta("config_history", "[]"))

    # -- market ------------------------------------------------------------
    def market_snapshot(self, *, force: bool = False) -> tuple[MarketSnapshot, bool]:
        with self._lock:
            fresh = (
                self._snapshot is not None
                and self._snapshot_at is not None
                and datetime.now(tz=self._snapshot_at.tzinfo) - self._snapshot_at
                < timedelta(seconds=SNAPSHOT_TTL_SECONDS)
            )
            if fresh and not force:
                return self._snapshot, True  # type: ignore[return-value]
            snapshot = build_snapshot(config=self.config())
            self._snapshot = snapshot
            self._snapshot_at = utc_now()
            return snapshot, False

    # -- stance ------------------------------------------------------------
    def build_state(self, *, force: bool = False) -> dict[str, Any]:
        config = self.config()
        market, cached = self.market_snapshot(force=force)
        decision = decide(config=config, market=market)
        self.store.save_decision(decision)
        return {
            "now": iso(utc_now()),
            "cached_market": cached,
            "config": config.to_dict(),
            "config_hash": config.config_hash,
            "config_missing": list(config.missing()),
            "market": market.to_dict(),
            "decision": decision.to_dict(),
            "integrations": {
                "degraded_features": [
                    {"key": key, "status": feature.status.value, "detail": feature.detail}
                    for key, feature in market.features.items()
                    if feature.status is not DataStatus.VERIFIED
                ],
                "degraded_critical": [
                    {"key": key, "status": feature.status.value, "detail": feature.detail}
                    for key, feature in market.features.items()
                    if feature.status in (DataStatus.STALE, DataStatus.UNAVAILABLE, DataStatus.CONFLICTING)
                    and key not in CONTEXTUAL_FEATURES
                ],
            },
            "config_history": self.config_history(),
        }

    # -- exports -----------------------------------------------------------
    def export_decisions(self) -> list[dict[str, Any]]:
        return self.store.decisions(limit=1000)


def _dig(payload: dict[str, Any], path: str) -> Any:
    cursor: Any = payload
    for part in path.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor


__all__ = ["CortexApp", "AppError"]
