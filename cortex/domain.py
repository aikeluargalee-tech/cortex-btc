"""Canonical domain types for the CORTEX-BTC signal dashboard.

The product answers one question — what market condition is BTC in right now —
with evidence that can be audited and replayed. It holds no balances and issues
no instructions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any, Iterable, Mapping


class Verdict(StrEnum):
    QUALIFIED_BUY_ZONE = "QUALIFIED BUY ZONE"
    QUALIFIED_SELL_ZONE = "QUALIFIED SELL ZONE"
    NO_SETUP = "NO SETUP"
    CONFLICT = "CONFLICT"
    DATA_HALT = "DATA HALT"


class ModuleState(StrEnum):
    WATCH = "WATCH"
    SETUP = "SETUP"
    CONFIRMED = "CONFIRMED"
    ELIGIBLE = "ELIGIBLE"
    INVALIDATED = "INVALIDATED"
    DATA_HALT = "DATA_HALT"
    REVIEW = "REVIEW"


class Regime(StrEnum):
    CONSTRUCTIVE = "CONSTRUCTIVE"
    CAUTIOUS = "CAUTIOUS"
    DEFENSIVE = "DEFENSIVE"
    DISLOCATION = "DISLOCATION"


class SellClass(StrEnum):
    DISTRIBUTION = "DISTRIBUTION"
    BREAKDOWN = "BREAKDOWN"


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


# --------------------------------------------------------------------------
# money helpers
# --------------------------------------------------------------------------

DEFAULT_QUANT = Decimal("0.00000001")


def D(value: Any) -> Decimal | None:
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
    return format(value.normalize(), "f")


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

    def feature(self, key: str) -> Feature | None:
        return self.features.get(key)

    def value(self, key: str) -> Decimal | None:
        feat = self.features.get(key)
        return None if feat is None else feat.value

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
    reference_level: Decimal | None = None
    invalidation_price: Decimal | None = None
    sell_class: SellClass | None = None
    data_halt_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "state": self.state.value,
            "conditions": [c.to_dict() for c in self.conditions],
            "summary": self.summary,
            "reference_level": dec_str(self.reference_level),
            "invalidation_price": dec_str(self.invalidation_price),
            "sell_class": None if self.sell_class is None else self.sell_class.value,
            "data_halt_reasons": list(self.data_halt_reasons),
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
class Decision:
    decision_id: str
    created_at: datetime
    verdict: Verdict
    explanation: str
    plain_english: str
    gates: tuple[GateResult, ...]
    bullish: ModuleAssessment
    bearish: ModuleAssessment
    market_snapshot: MarketSnapshot
    config_version: str
    config_hash: str
    replay_key: str
    software_version: str = "0.2.0"
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "created_at": iso(self.created_at),
            "verdict": self.verdict.value,
            "explanation": self.explanation,
            "plain_english": self.plain_english,
            "gates": [g.to_dict() for g in self.gates],
            "bullish": self.bullish.to_dict(),
            "bearish": self.bearish.to_dict(),
            "market_snapshot_id": self.market_snapshot.snapshot_id,
            "config_version": self.config_version,
            "config_hash": self.config_hash,
            "software_version": self.software_version,
            "replay_key": self.replay_key,
            "notes": list(self.notes),
        }


def make_decision_id(replay_key: str) -> str:
    return "dec_" + replay_key[:24]


def gate(name: str, number: int, status: GateStatus, detail: str, reasons: Iterable[str] = ()) -> GateResult:
    return GateResult(gate=number, name=name, status=status, detail=detail, reasons=tuple(reasons))


__all__ = [name for name in dir() if not name.startswith("_")]
