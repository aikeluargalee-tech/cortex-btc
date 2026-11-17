"""Module A (accumulation) and Module B (distribution).

Both modules are evidence readers, never authorities: they return an
assessment with every condition explicitly resolved, and the state is a
deterministic function of those conditions. Neither can emit an order.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Mapping

from .config import Config
from .domain import (
    Condition,
    ConditionStatus,
    DataStatus,
    Feature,
    MarketSnapshot,
    ModuleAssessment,
    ModuleState,
    SellMechanism,
    dec_str,
)

ZERO = Decimal("0")
BP = Decimal("10000")


def _f(value: Decimal | None, places: str = "0.01") -> str:
    """Format a Decimal for operator-facing text without exposing raw precision."""
    if value is None:
        return "—"
    return dec_str(value.quantize(Decimal(places)))

FEED_FEATURES: dict[str, tuple[str, ...]] = {
    "spot_price": ("price",),
    "spot_klines": ("support", "resistance", "vah", "val", "poc"),
    "spot_agg_trades": ("spot_delta_15m", "spot_cvd_slope_15m"),
    "depth": ("book_imbalance",),
    "derivatives": ("open_interest_change_pct", "funding_rate"),
}


def _status(feature: Feature | None) -> ConditionStatus:
    if feature is None or feature.value is None:
        return ConditionStatus.UNAVAILABLE
    if feature.status in (DataStatus.STALE, DataStatus.UNAVAILABLE, DataStatus.CONFLICTING):
        return ConditionStatus.UNAVAILABLE
    return ConditionStatus.PASS


def _cmp(condition_id: str, label: str, category: str, ok: bool | None, detail: str, value: Decimal | None = None, required: bool = True) -> Condition:
    if ok is None:
        return Condition(id=condition_id, label=label, category=category, status=ConditionStatus.NOT_CONFIGURED, detail=detail, required=required, value=value)
    return Condition(
        id=condition_id,
        label=label,
        category=category,
        status=ConditionStatus.PASS if ok else ConditionStatus.FAIL,
        detail=detail,
        required=required,
        value=value,
    )


def data_halt_reasons(snapshot: MarketSnapshot, config: Config) -> tuple[str, ...]:
    """Mandatory feeds that are not VERIFIED. Drives Gate 1 and both modules."""
    reasons: list[str] = []
    for feed in config.mandatory_feeds:
        keys = FEED_FEATURES.get(feed, (feed,))
        for key in keys:
            feature = snapshot.feature(key)
            if feature is None:
                reasons.append(f"{feed}: {key} missing")
                continue
            if feature.status is DataStatus.VERIFIED:
                continue
            if feature.status is DataStatus.PARTIALLY_VERIFIED:
                reasons.append(f"{feed}: {key} only partially verified")
            elif feature.status is DataStatus.PROXY:
                reasons.append(f"{feed}: {key} is a proxy, not verified")
            elif feature.status is DataStatus.STALE:
                reasons.append(f"{feed}: {key} stale")
            elif feature.status is DataStatus.CONFLICTING:
                reasons.append(f"{feed}: {key} conflicting")
            else:
                reasons.append(f"{feed}: {key} unavailable")
    return tuple(dict.fromkeys(reasons))


def _pct(price: Decimal, reference: Decimal) -> Decimal:
    if reference == ZERO:
        return ZERO
    return ((price - reference) / reference) * BP


# --------------------------------------------------------------------------
# Module A — accumulation
# --------------------------------------------------------------------------


def assess_accumulation(snapshot: MarketSnapshot, config: Config) -> ModuleAssessment:
    rules = config.buy
    price = snapshot.price
    support = snapshot.value("support")
    resistance = snapshot.value("resistance")
    vah = snapshot.value("vah")
    sweep_low = snapshot.value("sweep_low")
    delta = snapshot.value("spot_delta_15m")
    delta_prev = snapshot.value("spot_delta_prev_15m")
    cvd_slope = snapshot.value("spot_cvd_slope_15m")
    absorption = snapshot.value("demand_absorption")
    halts = data_halt_reasons(snapshot, config)

    if price is None or support is None:
        return ModuleAssessment(
            module="A",
            state=ModuleState.DATA_HALT,
            conditions=(),
            summary="No verified price or structural reference; accumulation cannot be assessed.",
            data_halt_reasons=halts or ("price or support reference missing",),
        )

    conditions: list[Condition] = []

    # A1 — a verified structural reference, with price in the approach zone.
    if rules.entry_zone_bps is None:
        conditions.append(_cmp("A1", "Structural reference in approach zone", "structure", None, "entry_zone_bps is NOT CONFIGURED", support))
        a1 = None
    else:
        distance_bps = _pct(price, support)
        a1 = ZERO <= distance_bps <= rules.entry_zone_bps
        conditions.append(
            _cmp(
                "A1",
                "Structural reference in approach zone",
                "structure",
                a1,
                f"price {_f(price)} is {_f(distance_bps, '0.1')} bps above support {_f(support)}",
                support,
            )
        )

    # A2 — a downside sweep deeper than the configured minimum.
    if rules.sweep_min_bps is None:
        conditions.append(_cmp("A2", "Downside sweep", "structure", None, "sweep_min_bps is NOT CONFIGURED"))
        a2 = None
    else:
        depth = None if sweep_low is None else ((support - sweep_low) / support) * BP
        a2 = depth is not None and depth >= rules.sweep_min_bps
        conditions.append(
            _cmp(
                "A2",
                "Downside liquidity sweep",
                "structure",
                a2,
                "no sweep_low observed" if depth is None else f"sweep depth {_f(depth, '0.1')} bps vs minimum {_f(rules.sweep_min_bps, '0.1')}",
                depth,
            )
        )

    # A3 — aggressive selling losing force.
    if rules.delta_improvement_min is None:
        conditions.append(_cmp("A3", "Selling pressure weakens", "flow", None, "delta_improvement_min is NOT CONFIGURED"))
        a3 = None
    elif delta is None or delta_prev is None:
        conditions.append(_cmp("A3", "Selling pressure weakens", "flow", False, "spot delta history unavailable"))
        a3 = False
    else:
        improvement = delta - delta_prev
        a3 = improvement >= rules.delta_improvement_min
        conditions.append(
            _cmp("A3", "Selling pressure weakens", "flow", a3, f"delta change {_f(improvement)} vs minimum {_f(rules.delta_improvement_min)}", improvement)
        )

    # A4 — spot selling not confirming a sustained breakdown.
    if cvd_slope is None:
        conditions.append(_cmp("A4", "Spot selling fails to confirm breakdown", "flow", False, "spot CVD slope unavailable"))
    else:
        conditions.append(
            _cmp("A4", "Spot selling fails to confirm breakdown", "flow", cvd_slope >= ZERO, f"spot CVD slope {_f(cvd_slope, '0.001')}", cvd_slope)
        )

    # A5 — absorption / bid replenishment.
    if rules.absorption_min is None:
        conditions.append(_cmp("A5", "Demand absorption", "flow", None, "absorption_min is NOT CONFIGURED"))
        a5 = None
    elif absorption is None:
        conditions.append(_cmp("A5", "Demand absorption", "flow", False, "absorption estimate unavailable"))
        a5 = False
    else:
        a5 = absorption >= rules.absorption_min
        conditions.append(_cmp("A5", "Demand absorption", "flow", a5, f"absorption {_f(absorption, '0.001')} vs minimum {_f(rules.absorption_min, '0.001')}", absorption))

    # A6 — reclaim of the auction boundary.
    if rules.require_value_area_reclaim is None:
        conditions.append(_cmp("A6", "Value-area reclaim", "auction", None, "require_value_area_reclaim is NOT CONFIGURED"))
        a6 = None
    elif not rules.require_value_area_reclaim:
        a6 = True
        conditions.append(_cmp("A6", "Value-area reclaim", "auction", True, "not required by configuration"))
    elif vah is None:
        conditions.append(_cmp("A6", "Value-area reclaim", "auction", False, "VAH unavailable"))
        a6 = False
    else:
        a6 = price >= vah
        conditions.append(
            _cmp(
                "A6",
                "Value-area reclaim",
                "auction",
                a6,
                f"price {_f(price)} vs {config.analysis.value_area_hours}h VAH {_f(vah)}",
                vah,
            )
        )

    # A7 — execution economics.
    all_in = config.fees.all_in_bps
    if rules.max_entry_premium_bps is None or all_in is None or config.execution.max_total_cost_bps is None:
        conditions.append(_cmp("A7", "Entry economics acceptable", "execution", None, "cost or premium limits NOT CONFIGURED"))
        a7 = None
    else:
        premium = _pct(price, support)
        a7 = premium <= rules.max_entry_premium_bps and all_in <= config.execution.max_total_cost_bps
        conditions.append(
            _cmp(
                "A7",
                "Entry economics acceptable",
                "execution",
                a7,
                f"premium {_f(premium, '0.1')} bps, all-in cost {_f(all_in, '0.1')} bps",
                all_in,
            )
        )

    invalidation = None
    if rules.invalidation_buffer_bps is not None:
        invalidation = support * (Decimal("1") - rules.invalidation_buffer_bps / BP)

    core = [a1, a2, a3, a5, a6]
    state = _accumulation_state(
        halts=halts,
        price=price,
        support=support,
        invalidation=invalidation,
        core=core,
        a3=a3,
        a5=a5,
        cvd_slope=cvd_slope,
        all_required=(a1, a2, a3, a5, a6, a7),
        a4=conditions[3].status,
    )

    summary = {
        ModuleState.ELIGIBLE: "All accumulation conditions pass and entry economics are acceptable.",
        ModuleState.CONFIRMED: "Market confirmation achieved; execution economics still block eligibility.",
        ModuleState.SETUP: "Structural test occurred; confirmation is incomplete.",
        ModuleState.WATCH: "Support is in range but the sweep and confirmation sequence has not started.",
        ModuleState.INVALIDATED: "Price has lost the structural reference; accumulation thesis invalidated.",
        ModuleState.REVIEW: "Evidence conflicts: flow improvement without structural confirmation, or vice versa.",
        ModuleState.DATA_HALT: "A mandatory feed is not verified; no accumulation decision is possible.",
    }[state]

    return ModuleAssessment(
        module="A",
        state=state,
        conditions=tuple(conditions),
        summary=summary,
        proposed_limit_price=price,
        invalidation_price=invalidation,
        reference_level=support,
        data_halt_reasons=halts,
    )


def _accumulation_state(
    *,
    halts: tuple[str, ...],
    price: Decimal,
    support: Decimal,
    invalidation: Decimal | None,
    core: list[bool | None],
    a3: bool | None,
    a5: bool | None,
    cvd_slope: Decimal | None,
    all_required: Iterable[bool | None],
    a4: ConditionStatus,
) -> ModuleState:
    if halts:
        return ModuleState.DATA_HALT
    if invalidation is not None and price < invalidation:
        return ModuleState.INVALIDATED
    required = list(all_required)
    if any(value is None for value in required):
        # An unconfigured rule cannot certify a setup.
        if any(value is True for value in required):
            return ModuleState.REVIEW
        return ModuleState.WATCH
    if all(required):
        return ModuleState.ELIGIBLE
    market_core = [a3, a5, core[4]]
    market_pass = all(value is True for value in market_core)
    structural = core[0] is True and core[1] is True
    if structural and market_pass and any(value is False for value in required):
        return ModuleState.CONFIRMED
    passes = sum(1 for value in core if value is True)
    if structural and passes >= 3:
        if a3 is False and a5 is True and cvd_slope is not None and cvd_slope < ZERO:
            return ModuleState.REVIEW
        return ModuleState.SETUP
    if core[0]:
        return ModuleState.WATCH
    return ModuleState.WATCH


# --------------------------------------------------------------------------
# Module B — distribution
# --------------------------------------------------------------------------


def _sell_mechanism(snapshot: MarketSnapshot, config: Config, breakdown: bool, regime: str | None) -> SellMechanism | None:
    caps = config.mechanisms
    if breakdown and caps.defensive_exit_max_pct is not None:
        return SellMechanism.DEFENSIVE_EXIT
    if regime in ("DEFENSIVE", "DISLOCATION") and caps.defensive_exit_max_pct is not None:
        return SellMechanism.DEFENSIVE_EXIT
    absorption = snapshot.value("supply_absorption")
    if absorption is not None and caps.risk_reduction_max_pct is not None:
        return SellMechanism.RISK_REDUCTION
    average_cost = snapshot.value("average_cost_basis")
    target = config.sell.profit_target_pct
    price = snapshot.price
    if average_cost is not None and target is not None and price is not None and caps.profit_taking_max_pct is not None:
        if price >= average_cost * (Decimal("1") + target):
            return SellMechanism.PROFIT_TAKING
    return None


def assess_distribution(snapshot: MarketSnapshot, config: Config, regime: str | None = None) -> ModuleAssessment:
    rules = config.sell
    price = snapshot.price
    resistance = snapshot.value("resistance")
    support = snapshot.value("support")
    vah = snapshot.value("vah")
    cvd_slope = snapshot.value("spot_cvd_slope_15m")
    delta = snapshot.value("spot_delta_15m")
    price_change = snapshot.value("price_change_15m_pct")
    absorption = snapshot.value("supply_absorption")
    halts = data_halt_reasons(snapshot, config)

    if price is None or resistance is None:
        return ModuleAssessment(
            module="B",
            state=ModuleState.DATA_HALT,
            conditions=(),
            summary="No verified price or resistance reference; distribution cannot be assessed.",
            data_halt_reasons=halts or ("price or resistance reference missing",),
        )

    conditions: list[Condition] = []

    # B1 — price at a meaningful supply region.
    if rules.resistance_zone_bps is None:
        conditions.append(_cmp("B1", "Resistance / supply test", "structure", None, "resistance_zone_bps is NOT CONFIGURED"))
        b1 = None
    else:
        distance = _pct(resistance, price)
        b1 = ZERO <= distance <= rules.resistance_zone_bps
        conditions.append(
            _cmp("B1", "Resistance / supply test", "structure", b1, f"price {_f(price)} is {_f(distance, '0.1')} bps below resistance {_f(resistance)}", resistance)
        )

    # B2 — aggressive buying absorbed by supply.
    if rules.absorption_min is None:
        conditions.append(_cmp("B2", "Supply absorption", "flow", None, "absorption_min is NOT CONFIGURED"))
        b2 = None
    elif absorption is None:
        conditions.append(_cmp("B2", "Supply absorption", "flow", False, "absorption estimate unavailable"))
        b2 = False
    else:
        b2 = absorption >= rules.absorption_min
        conditions.append(_cmp("B2", "Supply absorption", "flow", b2, f"absorption {_f(absorption, '0.001')} vs minimum {_f(rules.absorption_min, '0.001')}", absorption))

    # B3 — buying volume without price progress.
    if delta is None or price_change is None:
        conditions.append(_cmp("B3", "Spot buying fails to sustain price", "flow", False, "delta or price change unavailable"))
        b3 = False
    else:
        b3 = delta > ZERO and price_change <= ZERO
        conditions.append(_cmp("B3", "Spot buying fails to sustain price", "flow", b3, f"delta {_f(delta)}, price change {_f(price_change)}%", price_change))

    # B4 — failed reclaim or confirmed breakdown.
    if rules.breakdown_buffer_bps is None:
        conditions.append(_cmp("B4", "Failed reclaim / breakdown", "auction", None, "breakdown_buffer_bps is NOT CONFIGURED"))
        b4 = None
        breakdown = False
    else:
        breakdown = price < support * (Decimal("1") - rules.breakdown_buffer_bps / BP)
        recent_high = snapshot.value("recent_high")
        tested = vah is not None and recent_high is not None and recent_high >= vah
        failed_reclaim = tested and price < vah
        b4 = breakdown or failed_reclaim
        if breakdown:
            detail = "confirmed breakdown of support"
        elif failed_reclaim:
            detail = f"tested the {config.analysis.value_area_hours}h VAH and closed back below it"
        elif vah is not None:
            detail = f"no test of the {config.analysis.value_area_hours}h VAH; price is simply below it"
        else:
            detail = "value area unavailable"
        conditions.append(_cmp("B4", "Failed reclaim / breakdown", "auction", b4, detail, price))

    # B5 — deteriorating spot participation.
    if rules.cvd_deterioration_max is None:
        conditions.append(_cmp("B5", "Spot CVD deteriorates", "flow", None, "cvd_deterioration_max is NOT CONFIGURED"))
        b5 = None
    elif cvd_slope is None:
        conditions.append(_cmp("B5", "Spot CVD deteriorates", "flow", False, "spot CVD slope unavailable"))
        b5 = False
    else:
        b5 = cvd_slope <= rules.cvd_deterioration_max
        conditions.append(_cmp("B5", "Spot CVD deteriorates", "flow", b5, f"CVD slope {_f(cvd_slope, '0.001')} vs maximum {_f(rules.cvd_deterioration_max, '0.001')}", cvd_slope))

    # B6 — exit economics.
    all_in = config.fees.all_in_bps
    if all_in is None or config.execution.max_total_cost_bps is None:
        conditions.append(_cmp("B6", "Exit economics acceptable", "execution", None, "cost limits NOT CONFIGURED"))
        b6 = None
    else:
        b6 = all_in <= config.execution.max_total_cost_bps
        conditions.append(_cmp("B6", "Exit economics acceptable", "execution", b6, f"all-in cost {dec_str(all_in)} bps vs maximum {dec_str(config.execution.max_total_cost_bps)}", all_in))

    mechanism = _sell_mechanism(snapshot, config, breakdown, regime)
    required = (b1, b2, b3, b4, b5, b6)
    if halts:
        state = ModuleState.DATA_HALT
        summary = "A mandatory feed is not verified; no distribution decision is possible."
    elif mechanism is None:
        state = ModuleState.REVIEW
        summary = "Distribution evidence exists but no selling mechanism is configured; classification is a required input."
    elif all(value is True for value in required):
        state = ModuleState.ELIGIBLE
        summary = f"Distribution qualified as {mechanism.value}; inventory and sizing are still controlled centrally."
    elif b1 and b2 and sum(1 for value in (b2, b3, b4, b5) if value) >= 2:
        state = ModuleState.SETUP
        summary = "Supply is present and buying is losing effect; the sequence is not yet complete."
    elif b1:
        state = ModuleState.WATCH
        summary = "Price is testing supply, but absorption and participation evidence are not yet in place."
    else:
        state = ModuleState.WATCH
        summary = "No meaningful supply test; distribution monitoring continues."

    return ModuleAssessment(
        module="B",
        state=state,
        conditions=tuple(conditions),
        summary=summary,
        proposed_limit_price=price,
        invalidation_price=None,
        reference_level=resistance,
        mechanism=mechanism
        if state in (ModuleState.SETUP, ModuleState.CONFIRMED, ModuleState.ELIGIBLE)
        else None,
        data_halt_reasons=halts,
    )


def classify_regime(snapshot: MarketSnapshot, config: Config) -> tuple[str | None, str]:
    """Regime from a configured rule set; never inferred from a single indicator."""
    rules = config.regime
    price = snapshot.price
    sma = snapshot.value("sma")
    if price is None or sma is None or rules.constructive_above_pct is None or rules.defensive_below_pct is None:
        return None, "regime rules NOT CONFIGURED or price/SMA unavailable"
    distance = ((price - sma) / sma) * Decimal("100")
    if distance >= rules.constructive_above_pct:
        regime = "CONSTRUCTIVE"
    elif distance <= -rules.defensive_below_pct:
        regime = "DEFENSIVE"
    else:
        regime = "CAUTIOUS"
    return regime, f"price {dec_str(distance.quantize(Decimal('0.01')))}% vs {rules.sma_days}-day SMA"


__all__ = [
    "FEED_FEATURES",
    "assess_accumulation",
    "assess_distribution",
    "classify_regime",
    "data_halt_reasons",
]
