"""Canonical domain types for CORTEX-BTC.

Design rules enforced here:
  * money and quantities are ``Decimal`` — never float;
  * every historical object is immutable and serialises to a canonical form;
  * snapshot hashes are content addressed so a decision can be replayed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any, Iterable, Mapping


class Verdict(StrEnum):
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    ABSTAIN = "ABSTAIN"


class TradeSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class ModuleState(StrEnum):
    WATCH = "WATCH"
    SETUP = "SETUP"
    CONFIRMED = "CONFIRMED"
    ELIGIBLE = "ELIGIBLE"
    INVALIDATED = "INVALIDATED"
    DATA_HALT = "DATA_HALT"
    REVIEW = "REVIEW"


class PortfolioState(StrEnum):
    ALL_USDT = "100% USDT"
    ACCUMULATION = "BTC ACCUMULATION"
    HOLD_BTC = "BTC HOLD"
    REDUCTION = "BTC REDUCTION"
    DEFENSIVE = "BTC DEFENSIVE"
    ROTATION_TO_USDT = "ROTATION TO USDT"
    REACCUMULATION = "RE-ACCUMULATION"
    DATA_HALT = "DATA HALT"
    HUMAN_REVIEW = "HUMAN REVIEW"


class Regime(StrEnum):
    CONSTRUCTIVE = "CONSTRUCTIVE"
    CAUTIOUS = "CAUTIOUS"
    DEFENSIVE = "DEFENSIVE"
    DISLOCATION = "DISLOCATION"


class DataStatus(StrEnum):
    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY VERIFIED"
    PROXY = "PROXY"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    CONFLICTING = "CONFLICTING"


class ConditionStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_CONFIGURED = "NOT CONFIGURED"


class GateStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNAVAILABLE = "UNAVAILABLE"


class SellMechanism(StrEnum):
    PROFIT_TAKING = "PROFIT-TAKING"
    RISK_REDUCTION = "RISK REDUCTION"
    DEFENSIVE_EXIT = "DEFENSIVE EXIT"


class OrderStatus(StrEnum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    SUBMITTED = "SUBMITTED"
    WORKING = "WORKING"
    PARTIALLY_FILLED = "PARTIALLY FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class Loop(StrEnum):
    A = "LOOP A — USDT to BTC to USDT"
    B = "LOOP B — BTC to USDT to BTC"


class CycleStatus(StrEnum):
    OPEN = "OPEN"
    LEG1_FILLED = "LEG 1 FILLED"
    COMPLETE = "COMPLETE"
    ABANDONED = "ABANDONED"


# --------------------------------------------------------------------------
# money helpers
# --------------------------------------------------------------------------

DEFAULT_QUANT = Decimal("0.00000001")
DEFAULT_PRICE_QUANT = Decimal("0.01")


def D(value: Any) -> Decimal | None:
    """Convert to Decimal without ever routing through binary float noise."""
    if value is None or isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    return Decimal(str(value))


def q(value: Decimal, quant: Decimal = DEFAULT_QUANT) -> Decimal:
    return value.quantize(quant)


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def dec_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalised = value.normalize()
    text = format(normalised, "f")
    return text


# --------------------------------------------------------------------------
# market snapshot
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Feature:
    """One observed or derived market fact, always carrying provenance."""

    value: Decimal | None
    status: DataStatus
    source: str
    observed_at: datetime | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": dec_str(self.value),
            "status": self.status.value,
            "source": self.source,
            "observed_at": iso(self.observed_at),
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    snapshot_id: str
    as_of: datetime
    instrument: str
    price: Decimal | None
    features: Mapping[str, Feature]
    config_version: str
    built_at: datetime
    source: str = "binance-public"

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "as_of": iso(self.as_of),
            "instrument": self.instrument,
            "price": dec_str(self.price),
            "features": {k: v.to_dict() for k, v in self.features.items()},
            "config_version": self.config_version,
            "built_at": iso(self.built_at),
            "source": self.source,
        }

    def feature(self, key: str) -> Feature | None:
        return self.features.get(key)

    def value(self, key: str) -> Decimal | None:
        feat = self.features.get(key)
        return None if feat is None else feat.value


def build_market_snapshot(
    *,
    as_of: datetime,
    instrument: str,
    price: Decimal | None,
    features: Mapping[str, Feature],
    config_version: str,
    source: str,
) -> MarketSnapshot:
    payload = {
        "as_of": iso(as_of),
        "instrument": instrument,
        "price": dec_str(price),
        "features": {k: v.to_dict() for k, v in features.items()},
        "config_version": config_version,
        "source": source,
    }
    return MarketSnapshot(
        snapshot_id=content_hash(payload),
        as_of=as_of,
        instrument=instrument,
        price=price,
        features=dict(features),
        config_version=config_version,
        built_at=utc_now(),
        source=source,
    )


# --------------------------------------------------------------------------
# portfolio / ledger views
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Reservation:
    order_id: str
    asset: str
    quantity: Decimal
    created_at: datetime
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "asset": self.asset,
            "quantity": dec_str(self.quantity),
            "created_at": iso(self.created_at),
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class LedgerState:
    btc_core: Decimal
    btc_tactical: Decimal
    usdt_core: Decimal
    usdt_tactical: Decimal
    reservations: tuple[Reservation, ...] = ()
    realised_usdt: Decimal = Decimal("0")
    fees_paid_usdt: Decimal = Decimal("0")
    buy_cost_basis_usdt: Decimal = Decimal("0")
    buy_cost_quantity: Decimal = Decimal("0")

    @property
    def btc_total(self) -> Decimal:
        return self.btc_core + self.btc_tactical

    @property
    def usdt_total(self) -> Decimal:
        return self.usdt_core + self.usdt_tactical

    def reserved(self, asset: str) -> Decimal:
        return sum((r.quantity for r in self.reservations if r.asset == asset), Decimal("0"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "btc_core": dec_str(self.btc_core),
            "btc_tactical": dec_str(self.btc_tactical),
            "usdt_core": dec_str(self.usdt_core),
            "usdt_tactical": dec_str(self.usdt_tactical),
            "btc_total": dec_str(self.btc_total),
            "usdt_total": dec_str(self.usdt_total),
            "reserved_btc": dec_str(self.reserved("BTC")),
            "reserved_usdt": dec_str(self.reserved("USDT")),
            "realised_usdt": dec_str(self.realised_usdt),
            "fees_paid_usdt": dec_str(self.fees_paid_usdt),
            "reservations": [r.to_dict() for r in self.reservations],
        }


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    snapshot_id: str
    as_of: datetime
    price: Decimal | None
    ledger: LedgerState
    btc_value_usdt: Decimal | None
    total_value_usdt: Decimal | None
    btc_pct: Decimal | None
    usdt_pct: Decimal | None
    available_btc: Decimal | None
    available_usdt: Decimal | None
    config_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "as_of": iso(self.as_of),
            "price": dec_str(self.price),
            "ledger": self.ledger.to_dict(),
            "btc_value_usdt": dec_str(self.btc_value_usdt),
            "total_value_usdt": dec_str(self.total_value_usdt),
            "btc_pct": dec_str(self.btc_pct),
            "usdt_pct": dec_str(self.usdt_pct),
            "available_btc": dec_str(self.available_btc),
            "available_usdt": dec_str(self.available_usdt),
            "config_version": self.config_version,
        }


def build_portfolio_snapshot(
    *,
    as_of: datetime,
    price: Decimal | None,
    ledger: LedgerState,
    available_btc: Decimal | None,
    available_usdt: Decimal | None,
    config_version: str,
) -> PortfolioSnapshot:
    btc_value = None if price is None else ledger.btc_total * price
    usdt_value = ledger.usdt_total
    total = None if btc_value is None else btc_value + usdt_value
    btc_pct = None if not total else (btc_value / total) * Decimal("100")
    usdt_pct = None if not total else (usdt_value / total) * Decimal("100")
    payload = {
        # Identity is content, not wall-clock: the same balances and price must
        # always produce the same portfolio snapshot id so a decision can be replayed.
        "price": dec_str(price),
        "ledger": ledger.to_dict(),
        "total": dec_str(total),
        "config_version": config_version,
    }
    return PortfolioSnapshot(
        snapshot_id=content_hash(payload),
        as_of=as_of,
        price=price,
        ledger=ledger,
        btc_value_usdt=btc_value,
        total_value_usdt=total,
        btc_pct=btc_pct,
        usdt_pct=usdt_pct,
        available_btc=available_btc,
        available_usdt=available_usdt,
        config_version=config_version,
    )


# --------------------------------------------------------------------------
# assessments
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Condition:
    id: str
    label: str
    category: str
    status: ConditionStatus
    detail: str
    required: bool = True
    value: Decimal | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "category": self.category,
            "status": self.status.value,
            "detail": self.detail,
            "required": self.required,
            "value": dec_str(self.value),
        }


@dataclass(frozen=True, slots=True)
class ModuleAssessment:
    module: str
    state: ModuleState
    conditions: tuple[Condition, ...]
    summary: str
    proposed_limit_price: Decimal | None = None
    invalidation_price: Decimal | None = None
    reference_level: Decimal | None = None
    mechanism: SellMechanism | None = None
    data_halt_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "state": self.state.value,
            "conditions": [c.to_dict() for c in self.conditions],
            "summary": self.summary,
            "proposed_limit_price": dec_str(self.proposed_limit_price),
            "invalidation_price": dec_str(self.invalidation_price),
            "reference_level": dec_str(self.reference_level),
            "mechanism": None if self.mechanism is None else self.mechanism.value,
            "data_halt_reasons": list(self.data_halt_reasons),
        }


@dataclass(frozen=True, slots=True)
class SizingConstraint:
    name: str
    quantity: Decimal | None
    detail: str
    configured: bool = True
    binding: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "quantity": dec_str(self.quantity),
            "detail": self.detail,
            "configured": self.configured,
            "binding": self.binding,
        }


@dataclass(frozen=True, slots=True)
class AllocationAssessment:
    side: TradeSide | None
    status: str
    quantity: Decimal | None
    limit_price: Decimal | None
    notional: Decimal | None
    fee_usdt: Decimal | None
    slippage_usdt: Decimal | None
    total_cost_usdt: Decimal | None
    binding_constraint: str | None
    constraints: tuple[SizingConstraint, ...]
    resulting_btc_pct: Decimal | None = None
    resulting_usdt_pct: Decimal | None = None
    floor_compliant: bool | None = None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "side": None if self.side is None else self.side.value,
            "status": self.status,
            "quantity": dec_str(self.quantity),
            "limit_price": dec_str(self.limit_price),
            "notional": dec_str(self.notional),
            "fee_usdt": dec_str(self.fee_usdt),
            "slippage_usdt": dec_str(self.slippage_usdt),
            "total_cost_usdt": dec_str(self.total_cost_usdt),
            "binding_constraint": self.binding_constraint,
            "constraints": [c.to_dict() for c in self.constraints],
            "resulting_btc_pct": dec_str(self.resulting_btc_pct),
            "resulting_usdt_pct": dec_str(self.resulting_usdt_pct),
            "floor_compliant": self.floor_compliant,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class GateResult:
    gate: int
    name: str
    status: GateStatus
    detail: str
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "name": self.name,
            "status": self.status.value,
            "detail": self.detail,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class ProposedOrder:
    order_id: str
    side: TradeSide
    limit_price: Decimal
    quantity: Decimal
    notional: Decimal
    fee_usdt: Decimal
    expiry_minutes: int
    invalidation: str
    loop: Loop
    status: OrderStatus = OrderStatus.PROPOSED

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "side": self.side.value,
            "limit_price": dec_str(self.limit_price),
            "quantity": dec_str(self.quantity),
            "notional": dec_str(self.notional),
            "fee_usdt": dec_str(self.fee_usdt),
            "expiry_minutes": self.expiry_minutes,
            "invalidation": self.invalidation,
            "loop": self.loop.value,
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class Decision:
    decision_id: str
    created_at: datetime
    verdict: Verdict
    portfolio_state: PortfolioState
    previous_state: PortfolioState | None
    explanation: str
    gates: tuple[GateResult, ...]
    bullish: ModuleAssessment
    bearish: ModuleAssessment
    allocation: AllocationAssessment
    proposed_order: ProposedOrder | None
    market_snapshot: MarketSnapshot
    portfolio_snapshot: PortfolioSnapshot
    config_version: str
    config_hash: str
    software_version: str = "0.1.0"
    replay_key: str = ""
    notes: tuple[str, ...] = ()
    plain_english: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "created_at": iso(self.created_at),
            "verdict": self.verdict.value,
            "portfolio_state": self.portfolio_state.value,
            "previous_state": None if self.previous_state is None else self.previous_state.value,
            "explanation": self.explanation,
            "plain_english": self.plain_english,
            "gates": [g.to_dict() for g in self.gates],
            "bullish": self.bullish.to_dict(),
            "bearish": self.bearish.to_dict(),
            "allocation": self.allocation.to_dict(),
            "proposed_order": None if self.proposed_order is None else self.proposed_order.to_dict(),
            "market_snapshot_id": self.market_snapshot.snapshot_id,
            "portfolio_snapshot_id": self.portfolio_snapshot.snapshot_id,
            "config_version": self.config_version,
            "config_hash": self.config_hash,
            "software_version": self.software_version,
            "replay_key": self.replay_key,
            "notes": list(self.notes),
        }


def make_decision_id(replay_key: str) -> str:
    return "dec_" + replay_key[:24]


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def summarise_conditions(conditions: Iterable[Condition]) -> dict[str, int]:
    counts = {s.value: 0 for s in ConditionStatus}
    for condition in conditions:
        counts[condition.status.value] += 1
    return counts


def gate(name: str, number: int, status: GateStatus, detail: str, reasons: Iterable[str] = ()) -> GateResult:
    return GateResult(gate=number, name=name, status=status, detail=detail, reasons=tuple(reasons))


__all__ = [name for name in dir() if not name.startswith("_")]
