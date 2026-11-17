"""Configuration: immutable, versioned, hash-addressed.

Every strategy number that has not been explicitly established stays ``None``
and surfaces as NOT CONFIGURED. Nothing here supplies a silent default that
could later be mistaken for a validated parameter.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping

from .domain import D, content_hash, dec_str, iso, utc_now

SOFTWARE_VERSION = "0.1.0"


def _opt(data: Mapping[str, Any], key: str) -> Decimal | None:
    if key not in data or data[key] is None:
        return None
    return D(data[key])


@dataclass(frozen=True, slots=True)
class PortfolioConfig:
    btc_core: Decimal | None = None
    btc_tactical: Decimal | None = None
    usdt_core: Decimal | None = None
    usdt_tactical: Decimal | None = None
    min_btc_inventory: Decimal | None = None
    min_usdt_inventory: Decimal | None = None
    max_trade_notional_usdt: Decimal | None = None
    max_risk_per_trade_pct: Decimal | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PortfolioConfig":
        return cls(
            btc_core=_opt(data, "btc_core"),
            btc_tactical=_opt(data, "btc_tactical"),
            usdt_core=_opt(data, "usdt_core"),
            usdt_tactical=_opt(data, "usdt_tactical"),
            min_btc_inventory=_opt(data, "min_btc_inventory"),
            min_usdt_inventory=_opt(data, "min_usdt_inventory"),
            max_trade_notional_usdt=_opt(data, "max_trade_notional_usdt"),
            max_risk_per_trade_pct=_opt(data, "max_risk_per_trade_pct"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "btc_core": dec_str(self.btc_core),
            "btc_tactical": dec_str(self.btc_tactical),
            "usdt_core": dec_str(self.usdt_core),
            "usdt_tactical": dec_str(self.usdt_tactical),
            "min_btc_inventory": dec_str(self.min_btc_inventory),
            "min_usdt_inventory": dec_str(self.min_usdt_inventory),
            "max_trade_notional_usdt": dec_str(self.max_trade_notional_usdt),
            "max_risk_per_trade_pct": dec_str(self.max_risk_per_trade_pct),
        }

    def missing(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in (
                "btc_core",
                "btc_tactical",
                "usdt_tactical",
                "min_btc_inventory",
                "min_usdt_inventory",
                "max_trade_notional_usdt",
                "max_risk_per_trade_pct",
            )
            if getattr(self, name) is None
        )


@dataclass(frozen=True, slots=True)
class BandConfig:
    """Illustrative exposure bands. Percentages of total portfolio value."""

    defensive: Decimal | None = None
    cautious: Decimal | None = None
    neutral: Decimal | None = None
    constructive: Decimal | None = None
    strongly_constructive: Decimal | None = None
    current: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BandConfig":
        return cls(
            defensive=_opt(data, "defensive"),
            cautious=_opt(data, "cautious"),
            neutral=_opt(data, "neutral"),
            constructive=_opt(data, "constructive"),
            strongly_constructive=_opt(data, "strongly_constructive"),
            current=data.get("current"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "defensive": dec_str(self.defensive),
            "cautious": dec_str(self.cautious),
            "neutral": dec_str(self.neutral),
            "constructive": dec_str(self.constructive),
            "strongly_constructive": dec_str(self.strongly_constructive),
            "current": self.current,
        }

    def for_regime(self, regime: str) -> Decimal | None:
        mapping = {
            "DEFENSIVE": self.defensive,
            "CAUTIOUS": self.cautious,
            "CONSTRUCTIVE": self.constructive,
            "DISLOCATION": self.defensive,
        }
        if regime in mapping:
            return mapping[regime]
        return self.neutral


@dataclass(frozen=True, slots=True)
class SleeveConfig:
    active_pct: Decimal | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SleeveConfig":
        return cls(active_pct=_opt(data, "active_pct"))

    def to_dict(self) -> dict[str, Any]:
        return {"active_pct": dec_str(self.active_pct)}


@dataclass(frozen=True, slots=True)
class FeeConfig:
    maker_bps: Decimal | None = None
    taker_bps: Decimal | None = None
    slippage_bps: Decimal | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FeeConfig":
        return cls(
            maker_bps=_opt(data, "maker_bps"),
            taker_bps=_opt(data, "taker_bps"),
            slippage_bps=_opt(data, "slippage_bps"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "maker_bps": dec_str(self.maker_bps),
            "taker_bps": dec_str(self.taker_bps),
            "slippage_bps": dec_str(self.slippage_bps),
        }

    @property
    def all_in_bps(self) -> Decimal | None:
        if self.taker_bps is None or self.slippage_bps is None:
            return None
        return self.taker_bps + self.slippage_bps


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
        return {k: getattr(self, k) for k in self.__annotations__}


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    max_total_cost_bps: Decimal | None = None
    order_expiry_minutes: int | None = None
    require_limit_order: bool = True

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExecutionConfig":
        return cls(
            max_total_cost_bps=_opt(data, "max_total_cost_bps"),
            order_expiry_minutes=data.get("order_expiry_minutes"),
            require_limit_order=bool(data.get("require_limit_order", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_total_cost_bps": dec_str(self.max_total_cost_bps),
            "order_expiry_minutes": self.order_expiry_minutes,
            "require_limit_order": self.require_limit_order,
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
        return cls(
            entry_zone_bps=_opt(data, "entry_zone_bps"),
            sweep_min_bps=_opt(data, "sweep_min_bps"),
            invalidation_buffer_bps=_opt(data, "invalidation_buffer_bps"),
            absorption_min=_opt(data, "absorption_min"),
            delta_improvement_min=_opt(data, "delta_improvement_min"),
            require_value_area_reclaim=(
                None if data.get("require_value_area_reclaim") is None
                else bool(data.get("require_value_area_reclaim"))
            ),
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
            )
            if getattr(self, name) is None
        )


@dataclass(frozen=True, slots=True)
class SellRules:
    resistance_zone_bps: Decimal | None = None
    absorption_min: Decimal | None = None
    cvd_deterioration_max: Decimal | None = None
    breakdown_buffer_bps: Decimal | None = None
    profit_target_pct: Decimal | None = None
    require_breakdown_confirmation: bool | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SellRules":
        return cls(
            resistance_zone_bps=_opt(data, "resistance_zone_bps"),
            absorption_min=_opt(data, "absorption_min"),
            cvd_deterioration_max=_opt(data, "cvd_deterioration_max"),
            breakdown_buffer_bps=_opt(data, "breakdown_buffer_bps"),
            profit_target_pct=_opt(data, "profit_target_pct"),
            require_breakdown_confirmation=(
                None if data.get("require_breakdown_confirmation") is None
                else bool(data.get("require_breakdown_confirmation"))
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "resistance_zone_bps": dec_str(self.resistance_zone_bps),
            "absorption_min": dec_str(self.absorption_min),
            "cvd_deterioration_max": dec_str(self.cvd_deterioration_max),
            "breakdown_buffer_bps": dec_str(self.breakdown_buffer_bps),
            "profit_target_pct": dec_str(self.profit_target_pct),
            "require_breakdown_confirmation": self.require_breakdown_confirmation,
        }

    def missing(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in (
                "resistance_zone_bps",
                "absorption_min",
                "cvd_deterioration_max",
                "breakdown_buffer_bps",
                "profit_target_pct",
            )
            if getattr(self, name) is None
        )


@dataclass(frozen=True, slots=True)
class MechanismCaps:
    """Maximum fraction of available tactical BTC per selling mechanism."""

    profit_taking_max_pct: Decimal | None = None
    risk_reduction_max_pct: Decimal | None = None
    defensive_exit_max_pct: Decimal | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MechanismCaps":
        return cls(
            profit_taking_max_pct=_opt(data, "profit_taking_max_pct"),
            risk_reduction_max_pct=_opt(data, "risk_reduction_max_pct"),
            defensive_exit_max_pct=_opt(data, "defensive_exit_max_pct"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "profit_taking_max_pct": dec_str(self.profit_taking_max_pct),
            "risk_reduction_max_pct": dec_str(self.risk_reduction_max_pct),
            "defensive_exit_max_pct": dec_str(self.defensive_exit_max_pct),
        }

    def cap_for(self, mechanism: str) -> Decimal | None:
        return {
            "PROFIT-TAKING": self.profit_taking_max_pct,
            "RISK REDUCTION": self.risk_reduction_max_pct,
            "DEFENSIVE EXIT": self.defensive_exit_max_pct,
        }.get(mechanism)


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    """Measurement conventions, not validated strategy thresholds.

    The value-area horizon decides which auction the reclaim/breakdown test is
    judged against. It is a timeframe choice (like 1h vs 4h), not a proven edge.
    """

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
    portfolio: PortfolioConfig
    bands: BandConfig
    sleeve: SleeveConfig
    fees: FeeConfig
    freshness: FreshnessConfig
    execution: ExecutionConfig
    buy: BuyRules
    sell: SellRules
    mechanisms: MechanismCaps
    regime: RegimeRules
    mandatory_feeds: tuple[str, ...] = ()
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    created_at: datetime = field(default_factory=utc_now)
    operator: str = "system"
    note: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Config":
        portfolio_data = dict(data.get("portfolio") or {})
        return cls(
            config_version=str(data.get("config_version", "unversioned")),
            label=str(data.get("label", "")),
            validation_status=str(data.get("validation_status", "UNVALIDATED")),
            portfolio=PortfolioConfig.from_dict(portfolio_data),
            bands=BandConfig.from_dict(data.get("bands") or {}),
            sleeve=SleeveConfig.from_dict(data.get("sleeve") or {}),
            fees=FeeConfig.from_dict(data.get("fees") or {}),
            freshness=FreshnessConfig.from_dict(data.get("freshness") or {}),
            execution=ExecutionConfig.from_dict(data.get("execution") or {}),
            buy=BuyRules.from_dict(data.get("buy") or {}),
            sell=SellRules.from_dict(data.get("sell") or {}),
            mechanisms=MechanismCaps.from_dict(data.get("mechanisms") or {}),
            regime=RegimeRules.from_dict(data.get("regime") or {}),
            mandatory_feeds=tuple(data.get("mandatory_feeds") or ()),
            analysis=AnalysisConfig.from_dict(data.get("analysis") or {}),
            created_at=utc_now(),
            operator=str(data.get("operator", "system")),
            note=str(data.get("note", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_version": self.config_version,
            "label": self.label,
            "validation_status": self.validation_status,
            "portfolio": self.portfolio.to_dict(),
            "bands": self.bands.to_dict(),
            "sleeve": self.sleeve.to_dict(),
            "fees": self.fees.to_dict(),
            "freshness": self.freshness.to_dict(),
            "execution": self.execution.to_dict(),
            "buy": self.buy.to_dict(),
            "sell": self.sell.to_dict(),
            "mechanisms": self.mechanisms.to_dict(),
            "regime": self.regime.to_dict(),
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
        missing.extend(f"portfolio.{m}" for m in self.portfolio.missing())
        missing.extend(f"buy.{m}" for m in self.buy.missing())
        missing.extend(f"sell.{m}" for m in self.sell.missing())
        if self.fees.taker_bps is None:
            missing.append("fees.taker_bps")
        if self.fees.slippage_bps is None:
            missing.append("fees.slippage_bps")
        if self.execution.max_total_cost_bps is None:
            missing.append("execution.max_total_cost_bps")
        if self.regime.sma_days is None:
            missing.append("regime.sma_days")
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
            "portfolio": {},
            "bands": {},
            "sleeve": {},
            "fees": {},
            "freshness": {"spot_price_s": 30, "klines_s": 300, "agg_trades_s": 180, "depth_s": 30, "derivatives_s": 300},
            "execution": {},
            "buy": {},
            "sell": {},
            "mechanisms": {},
            "regime": {},
            "mandatory_feeds": ["spot_price", "spot_klines", "spot_agg_trades"],
        }
    )


__all__ = [
    "Config",
    "AnalysisConfig",
    "PortfolioConfig",
    "BandConfig",
    "SleeveConfig",
    "FeeConfig",
    "FreshnessConfig",
    "ExecutionConfig",
    "BuyRules",
    "SellRules",
    "MechanismCaps",
    "RegimeRules",
    "empty_config",
    "SOFTWARE_VERSION",
]
