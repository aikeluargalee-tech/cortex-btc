"""Application services: orchestration between data, ledger, authority and store."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from .authority import decide
from .config import Config, empty_config
from .domain import (
    DataStatus,
    Loop,
    MarketSnapshot,
    OrderStatus,
    PortfolioSnapshot,
    PortfolioState,
    TradeSide,
    build_portfolio_snapshot,
    dec_str,
    iso,
    utc_now,
)
from .ledger import Ledger, LedgerError, available_btc, available_usdt, integrity_check
from .market import build_snapshot
from .store import Store

SNAPSHOT_TTL_SECONDS = 45

# Contextual feeds may be absent without degrading a decision; they are
# telemetry, not decision inputs.
CONTEXTUAL_FEATURES = {
    "liquidations",
    "average_cost_basis",
    "open_interest",
    "funding_rate",
}


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

    # -- portfolio ---------------------------------------------------------
    def set_portfolio(self, payload: dict[str, Any], *, operator: str) -> dict[str, Any]:
        values = {}
        for key in ("btc_core", "btc_tactical", "usdt_core", "usdt_tactical"):
            raw = payload.get(key, 0)
            try:
                values[key] = Decimal(str(raw if raw not in (None, "") else "0"))
            except Exception as error:  # noqa: BLE001
                raise AppError(f"{key} is not a number: {raw!r}") from error
            if values[key] < 0:
                raise AppError(f"{key} cannot be negative")
        with self._lock:
            ledger = self.store.ledger()
            if ledger.events:
                raise AppError(
                    "portfolio is already recorded; use a reconciliation adjustment instead of re-seeding",
                    409,
                )
            event = ledger.seed(actor=operator or "operator", **values)
            self.store.append_event(event)
        return {"ok": True, "event": event.to_dict()}

    # -- market ------------------------------------------------------------
    def market_snapshot(self, *, force: bool = False) -> tuple[MarketSnapshot, bool]:
        with self._lock:
            fresh = (
                self._snapshot is not None
                and self._snapshot_at is not None
                and datetime.now(tz=self._snapshot_at.tzinfo) - self._snapshot_at < timedelta(seconds=SNAPSHOT_TTL_SECONDS)
            )
            if fresh and not force:
                return self._snapshot, True  # type: ignore[return-value]
            ledger = self.store.ledger()
            snapshot = build_snapshot(config=self.config(), average_cost=ledger.average_cost())
            self._snapshot = snapshot
            self._snapshot_at = utc_now()
            return snapshot, False

    # -- decision ----------------------------------------------------------
    def build_state(self, *, force: bool = False) -> dict[str, Any]:
        config = self.config()
        ledger = self.store.ledger()
        seeded = self.store.is_seeded()
        market, cached = self.market_snapshot(force=force)
        state = ledger.state()
        portfolio = build_portfolio_snapshot(
            as_of=market.as_of,
            price=market.price,
            ledger=state,
            available_btc=available_btc(state, config) if seeded else None,
            available_usdt=available_usdt(state, config) if seeded else None,
            config_version=config.config_version,
        )
        previous = self._previous_state()
        decision = decide(
            config=config,
            market=market,
            portfolio=portfolio,
            previous_state=previous,
            seeded=seeded,
        )
        self.store.save_decision(decision)
        ok, problems = integrity_check(ledger)
        return {
            "now": iso(utc_now()),
            "seeded": seeded,
            "cached_market": cached,
            "config": config.to_dict(),
            "config_hash": config.config_hash,
            "config_missing": list(config.missing()),
            "market": market.to_dict(),
            "portfolio": portfolio.to_dict(),
            "decision": decision.to_dict(),
            "ledger": state.to_dict(),
            "orders": self.store.orders(),
            "integrations": {
                "integrity_ok": ok,
                "integrity_problems": problems,
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

    def _previous_state(self) -> PortfolioState | None:
        latest = self.store.latest_decision()
        if latest is None:
            return None
        try:
            return PortfolioState(latest["portfolio_state"])
        except Exception:  # noqa: BLE001
            return None

    # -- orders ------------------------------------------------------------
    def approve(self, decision_id: str, *, operator: str) -> dict[str, Any]:
        decision = self.store.get_decision(decision_id)
        if decision is None:
            raise AppError("unknown decision", 404)
        proposal = decision.get("proposed_order")
        if proposal is None:
            raise AppError("this decision carries no proposed order", 409)
        if self.store.order(proposal["order_id"]) is not None:
            raise AppError("order already exists for this proposal", 409)
        config = self.config()
        ledger = self.store.ledger()
        state = ledger.state()
        side = TradeSide(proposal["side"])
        asset = "USDT" if side is TradeSide.BUY else "BTC"
        quantity = Decimal(proposal["quantity"])
        reserve_quantity = (Decimal(proposal["notional"]) + Decimal(proposal["fee_usdt"])) if side is TradeSide.BUY else quantity
        if side is TradeSide.BUY and reserve_quantity > available_usdt(state, config):
            raise AppError("proposal no longer fits available USDT; refresh before approving", 409)
        if side is TradeSide.SELL and quantity > available_btc(state, config):
            raise AppError("proposal no longer fits available BTC; refresh before approving", 409)
        with self._lock:
            try:
                event = ledger.reserve(
                    order_id=proposal["order_id"],
                    asset=asset,
                    quantity=reserve_quantity,
                    note=f"reserved on approval by {operator or 'operator'}",
                )
                self.store.append_event(event)
            except LedgerError as error:
                raise AppError(f"reservation rejected: {error}", 409) from error
        order = dict(proposal)
        order["status"] = OrderStatus.APPROVED.value
        self.store.create_order(order, decision_id)
        return {"ok": True, "order": order, "reserved": dec_str(reserve_quantity), "asset": asset}

    def transition(self, order_id: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        order = self.store.order(order_id)
        if order is None:
            raise AppError("unknown order", 404)
        current = OrderStatus(order["status"])
        allowed = {
            "submit": (OrderStatus.APPROVED, OrderStatus.SUBMITTED),
            "work": (OrderStatus.SUBMITTED, OrderStatus.WORKING),
            "expire": (OrderStatus.WORKING, OrderStatus.EXPIRED),
            "reject": (OrderStatus.WORKING, OrderStatus.REJECTED),
        }
        if action in allowed:
            expected, target = allowed[action]
            if current is not expected:
                raise AppError(f"cannot {action} an order in state {current.value}", 409)
            self.store.update_order(
                order_id,
                status=target.value,
                filled=order["filled_quantity"],
                fee=order["fee_usdt"],
                note=f"{action} recorded by operator",
            )
            return {"ok": True, "status": target.value}

        if action == "fill":
            quantity = Decimal(str(payload.get("quantity") or order["quantity"]))
            price = Decimal(str(payload.get("price") or order["limit_price"]))
            fee = Decimal(str(payload.get("fee") or "0"))
            if quantity <= 0:
                raise AppError("fill quantity must be positive")
            ledger = self.store.ledger()
            with self._lock:
                try:
                    event = ledger.fill(
                        order_id=order_id,
                        side=order["side"],
                        quantity=quantity,
                        price=price,
                        fee_usdt=fee,
                        note="fill recorded by operator",
                    )
                    self.store.append_event(event)
                except LedgerError as error:
                    raise AppError(f"fill rejected: {error}", 409) from error
                remaining = ledger.state().reserved("BTC" if order["side"] == "SELL" else "USDT")
                total_filled = Decimal(order["filled_quantity"]) + quantity
                complete = total_filled >= Decimal(order["quantity"])
                target = OrderStatus.FILLED if complete else OrderStatus.PARTIALLY_FILLED
                self.store.update_order(
                    order_id,
                    status=target.value,
                    filled=dec_str(total_filled) or "0",
                    fee=dec_str(Decimal(order["fee_usdt"]) + fee) or "0",
                    note=f"fill {dec_str(quantity)} @ {dec_str(price)}; reservation left {dec_str(remaining)}",
                )
            return {"ok": True, "status": target.value, "filled": dec_str(total_filled)}

        if action == "cancel":
            if not payload.get("confirmed"):
                raise AppError(
                    "cancellation must be confirmed; a reservation is never released on assumption", 409
                )
            ledger = self.store.ledger()
            with self._lock:
                try:
                    self.store.append_event(ledger.release(order_id=order_id, note="exchange cancellation confirmed"))
                except LedgerError as error:
                    raise AppError(f"release rejected: {error}", 409) from error
                self.store.update_order(
                    order_id,
                    status=OrderStatus.CANCELLED.value,
                    filled=order["filled_quantity"],
                    fee=order["fee_usdt"],
                    note="cancellation confirmed by operator",
                )
            return {"ok": True, "status": OrderStatus.CANCELLED.value}

        raise AppError(f"unknown action {action!r}", 400)

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
