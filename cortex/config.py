"""Configuration: immutable, versioned, hash-addressed.

Only the parameters the signal engine actually uses live here. Every number
that has not been explicitly established stays ``None`` and surfaces as
NOT CONFIGURED; nothing supplies a silent default that could be mistaken for a
validated threshold.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping

from .domain import D, content_hash, dec_str, iso, utc_now

SOFTWARE_VERSION = "0.2.0"


def _opt(data: Mapping[str, Any], key: str) -> Decimal | None:
    if key not in data or data[key] is None:
        return None
    return D(data[key])


@dataclass(frozen=True, slots=True)
class FreshnessConfig:
    spot_price_s: int | None = 30
    klines_s: int | None = 300
    agg_trades_s: int | None = 180
    depth_s: int | None = 30
    derivatives_s: int | None = 300

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FreshnessConfig":
        return cls(**{k: data.get(k) for k in cls.__annotations__})

    def to_dict(self) -> dict[str, Any]:
        return {
            "spot_price_s": self.spot_price_s,
            "klines_s": self.klines_s,
            "agg_trades_s": self.agg_trades_s,
            "depth_s": self.depth_s,
            "derivatives_s": self.derivatives_s,
        }


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    """Measurement conventions, not validated strategy thresholds."""

    value_area_hours: int = 24
    context_profile_hours: int = 168

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AnalysisConfig":
        return cls(
            value_area_hours=int(data.get("value_area_hours", 24)),
            context_profile_hours=int(data.get("context_profile_hours", 168)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "value_area_hours": self.value_area_hours,
            "context_profile_hours": self.context_profile_hours,
        }


@dataclass(frozen=True, slots=True)
class BuyRules:
    entry_zone_bps: Decimal | None = None
    sweep_min_bps: Decimal | None = None
    invalidation_buffer_bps: Decimal | None = None
    absorption_min: Decimal | None = None
    delta_improvement_min: Decimal | None = None
    require_value_area_reclaim: bool | None = None
    max_entry_premium_bps: Decimal | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BuyRules":
        reclaim = data.get("require_value_area_reclaim")
        return cls(
            entry_zone_bps=_opt(data, "entry_zone_bps"),
            sweep_min_bps=_opt(data, "sweep_min_bps"),
            invalidation_buffer_bps=_opt(data, "invalidation_buffer_bps"),
            absorption_min=_opt(data, "absorption_min"),
            delta_improvement_min=_opt(data, "delta_improvement_min"),
            require_value_area_reclaim=None if reclaim is None else bool(reclaim),
            max_entry_premium_bps=_opt(data, "max_entry_premium_bps"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_zone_bps": dec_str(self.entry_zone_bps),
            "sweep_min_bps": dec_str(self.sweep_min_bps),
            "invalidation_buffer_bps": dec_str(self.invalidation_buffer_bps),
            "absorption_min": dec_str(self.absorption_min),
            "delta_improvement_min": dec_str(self.delta_improvement_min),
            "require_value_area_reclaim": self.require_value_area_reclaim,
            "max_entry_premium_bps": dec_str(self.max_entry_premium_bps),
        }

    def missing(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in (
                "entry_zone_bps",
                "sweep_min_bps",
                "invalidation_buffer_bps",
                "absorption_min",
                "delta_improvement_min",
                "require_value_area_reclaim",
                "max_entry_premium_bps",
            )
            if getattr(self, name) is None
        )


@dataclass(frozen=True, slots=True)
class SellRules:
    resistance_zone_bps: Decimal | None = None
    absorption_min: Decimal | None = None
    cvd_deterioration_max: Decimal | None = None
    breakdown_buffer_bps: Decimal | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SellRules":
        return cls(
            resistance_zone_bps=_opt(data, "resistance_zone_bps"),
            absorption_min=_opt(data, "absorption_min"),
            cvd_deterioration_max=_opt(data, "cvd_deterioration_max"),
            breakdown_buffer_bps=_opt(data, "breakdown_buffer_bps"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "resistance_zone_bps": dec_str(self.resistance_zone_bps),
            "absorption_min": dec_str(self.absorption_min),
            "cvd_deterioration_max": dec_str(self.cvd_deterioration_max),
            "breakdown_buffer_bps": dec_str(self.breakdown_buffer_bps),
        }

    def missing(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in (
                "resistance_zone_bps",
                "absorption_min",
                "cvd_deterioration_max",
                "breakdown_buffer_bps",
            )
            if getattr(self, name) is None
        )


@dataclass(frozen=True, slots=True)
class RegimeRules:
    sma_days: int | None = None
    constructive_above_pct: Decimal | None = None
    defensive_below_pct: Decimal | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RegimeRules":
        return cls(
            sma_days=data.get("sma_days"),
            constructive_above_pct=_opt(data, "constructive_above_pct"),
            defensive_below_pct=_opt(data, "defensive_below_pct"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sma_days": self.sma_days,
            "constructive_above_pct": dec_str(self.constructive_above_pct),
            "defensive_below_pct": dec_str(self.defensive_below_pct),
        }


@dataclass(frozen=True, slots=True)
class Config:
    config_version: str
    label: str
    validation_status: str
    buy: BuyRules
    sell: SellRules
    regime: RegimeRules
    freshness: FreshnessConfig = field(default_factory=FreshnessConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    mandatory_feeds: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    operator: str = "system"
    note: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Config":
        return cls(
            config_version=str(data.get("config_version", "unversioned")),
            label=str(data.get("label", "")),
            validation_status=str(data.get("validation_status", "UNVALIDATED")),
            buy=BuyRules.from_dict(data.get("buy") or {}),
            sell=SellRules.from_dict(data.get("sell") or {}),
            regime=RegimeRules.from_dict(data.get("regime") or {}),
            freshness=FreshnessConfig.from_dict(data.get("freshness") or {}),
            analysis=AnalysisConfig.from_dict(data.get("analysis") or {}),
            mandatory_feeds=tuple(data.get("mandatory_feeds") or ()),
            operator=str(data.get("operator", "system")),
            note=str(data.get("note", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_version": self.config_version,
            "label": self.label,
            "validation_status": self.validation_status,
            "buy": self.buy.to_dict(),
            "sell": self.sell.to_dict(),
            "regime": self.regime.to_dict(),
            "freshness": self.freshness.to_dict(),
            "analysis": self.analysis.to_dict(),
            "mandatory_feeds": list(self.mandatory_feeds),
            "created_at": iso(self.created_at),
            "operator": self.operator,
            "note": self.note,
        }

    @property
    def config_hash(self) -> str:
        payload = self.to_dict()
        payload.pop("created_at", None)
        payload.pop("operator", None)
        return content_hash(payload)

    def missing(self) -> tuple[str, ...]:
        missing: list[str] = []
        missing.extend(f"buy.{m}" for m in self.buy.missing())
        missing.extend(f"sell.{m}" for m in self.sell.missing())
        if self.regime.sma_days is None:
            missing.append("regime.sma_days")
        if self.regime.constructive_above_pct is None:
            missing.append("regime.constructive_above_pct")
        if self.regime.defensive_below_pct is None:
            missing.append("regime.defensive_below_pct")
        if not self.mandatory_feeds:
            missing.append("mandatory_feeds")
        return tuple(missing)

    def with_changes(self, changes: Mapping[str, Any], *, version: str, operator: str, note: str) -> "Config":
        data = self.to_dict()
        for dotted, value in changes.items():
            cursor: Any = data
            parts = dotted.split(".")
            for part in parts[:-1]:
                if not isinstance(cursor, dict):
                    raise KeyError(f"cannot descend into {part!r} for {dotted!r}")
                cursor = cursor.setdefault(part, {})
            cursor[parts[-1]] = value
        data["config_version"] = version
        data["operator"] = operator
        data["note"] = note
        return Config.from_dict(data)


def empty_config(label: str = "Default profile — nothing configured") -> Config:
    """A profile that intentionally leaves every strategy number unset."""
    return Config.from_dict(
        {
            "config_version": "unconfigured-0",
            "label": label,
            "validation_status": "UNVALIDATED",
            "buy": {},
            "sell": {},
            "regime": {},
            "freshness": {"spot_price_s": 30, "klines_s": 300, "agg_trades_s": 180, "depth_s": 30, "derivatives_s": 300},
            "analysis": {"value_area_hours": 24, "context_profile_hours": 168},
            "mandatory_feeds": ["spot_price", "spot_klines", "spot_agg_trades"],
        }
    )


__all__ = [
    "Config",
    "AnalysisConfig",
    "FreshnessConfig",
    "BuyRules",
    "SellRules",
    "RegimeRules",
    "empty_config",
    "SOFTWARE_VERSION",
]
