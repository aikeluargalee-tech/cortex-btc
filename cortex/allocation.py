"""Module C — portfolio allocation controller.

Sizing is a minimum over named constraints. The controller must always be able
to say which constraint bound the order; a lone unexplained quantity is a
defect, not an optimisation.
"""

from __future__ import annotations

from decimal import Decimal

from .config import Config
from .domain import (
    AllocationAssessment,
    ConditionStatus,
    LedgerState,
    MarketSnapshot,
    ModuleAssessment,
    ModuleState,
    SizingConstraint,
    TradeSide,
    dec_str,
    q,
)
from .ledger import available_btc, available_usdt

ZERO = Decimal("0")
BP = Decimal("10000")


def _money(value: Decimal | None) -> str:
    return "—" if value is None else dec_str(q(value, Decimal("0.01")))


def _candidate(name: str, quantity: Decimal | None, detail: str, configured: bool = True) -> SizingConstraint:
    return SizingConstraint(name=name, quantity=quantity, detail=detail, configured=configured)


def _finalise(
    constraints: list[SizingConstraint],
    *,
    side: TradeSide,
    price: Decimal | None,
    config: Config,
    state: LedgerState,
    total_value: Decimal | None,
) -> AllocationAssessment:
    configured = [c for c in constraints if c.configured and c.quantity is not None]
    missing = [c for c in constraints if not c.configured]
    notes: list[str] = []

    if price is None or total_value is None:
        return AllocationAssessment(
            side=side,
            status="UNAVAILABLE",
            quantity=None,
            limit_price=price,
            notional=None,
            fee_usdt=None,
            slippage_usdt=None,
            total_cost_usdt=None,
            binding_constraint=None,
            constraints=tuple(constraints),
            notes=("price or portfolio value unavailable",),
        )

    if missing:
        return AllocationAssessment(
            side=side,
            status="NOT CONFIGURED",
            quantity=None,
            limit_price=price,
            notional=None,
            fee_usdt=None,
            slippage_usdt=None,
            total_cost_usdt=None,
            binding_constraint=None,
            constraints=tuple(constraints),
            notes=tuple(f"{c.name}: {c.detail}" for c in missing),
        )

    if not configured:
        return AllocationAssessment(
            side=side,
            status="BLOCKED",
            quantity=None,
            limit_price=price,
            notional=None,
            fee_usdt=None,
            slippage_usdt=None,
            total_cost_usdt=None,
            binding_constraint=None,
            constraints=tuple(constraints),
            notes=("no usable constraint",),
        )

    binding = min(configured, key=lambda c: c.quantity)  # type: ignore[arg-type]
    quantity = q(binding.quantity or ZERO)

    if quantity <= ZERO:
        return AllocationAssessment(
            side=side,
            status="BLOCKED",
            quantity=ZERO,
            limit_price=price,
            notional=ZERO,
            fee_usdt=ZERO,
            slippage_usdt=ZERO,
            total_cost_usdt=ZERO,
            binding_constraint=binding.name,
            constraints=tuple(constraints),
            notes=(f"binding constraint {binding.name} leaves no room",),
        )

    notional = quantity * price
    fee = notional * (config.fees.taker_bps or ZERO) / BP
    slippage = notional * (config.fees.slippage_bps or ZERO) / BP

    btc_total = state.btc_total
    btc_after = btc_total + quantity if side is TradeSide.BUY else btc_total - quantity
    usdt_after = state.usdt_total - (notional + fee) if side is TradeSide.BUY else state.usdt_total + (notional - fee)
    value_after = btc_after * price + usdt_after
    btc_pct_after = None if value_after <= ZERO else (btc_after * price / value_after) * Decimal("100")
    usdt_pct_after = None if value_after <= ZERO else (usdt_after / value_after) * Decimal("100")

    floor = config.portfolio.min_btc_inventory
    floor_compliant = None
    if floor is not None:
        floor_compliant = btc_after >= floor
        if not floor_compliant:
            notes.append(f"resulting BTC {dec_str(btc_after)} breaches inventory floor {dec_str(floor)}")

    if side is TradeSide.BUY and usdt_after < (config.portfolio.min_usdt_inventory or ZERO):
        notes.append("resulting USDT would fall below the configured inventory floor")

    flags = {c.name for c in constraints if c.configured and c.quantity == binding.quantity}
    marked = tuple(
        SizingConstraint(name=c.name, quantity=c.quantity, detail=c.detail, configured=c.configured, binding=c.name in flags)
        for c in constraints
    )

    usable = floor_compliant is not False and not any("breaches" in n for n in notes)
    return AllocationAssessment(
        side=side,
        status="READY" if usable else "BLOCKED",
        quantity=quantity,
        limit_price=price,
        notional=notional,
        fee_usdt=fee,
        slippage_usdt=slippage,
        total_cost_usdt=fee + slippage,
        binding_constraint=binding.name,
        constraints=marked,
        resulting_btc_pct=btc_pct_after,
        resulting_usdt_pct=usdt_pct_after,
        floor_compliant=floor_compliant,
        notes=tuple(notes),
    )


def assess_buy(
    *,
    config: Config,
    state: LedgerState,
    snapshot: MarketSnapshot,
    bull: ModuleAssessment,
    regime: str | None,
) -> AllocationAssessment:
    price = snapshot.price
    total_value = None if price is None else state.btc_total * price + state.usdt_total
    constraints: list[SizingConstraint] = []

    if price is None or total_value is None:
        return _finalise([], side=TradeSide.BUY, price=price, config=config, state=state, total_value=None)

    all_in = config.fees.all_in_bps
    if all_in is None:
        constraints.append(_candidate("fees", None, "taker/slippage bps NOT CONFIGURED", configured=False))
        return _finalise(constraints, side=TradeSide.BUY, price=price, config=config, state=state, total_value=total_value)
    unit_cost = price * (Decimal("1") + all_in / BP)

    cash = available_usdt(state, config)
    if config.portfolio.min_usdt_inventory is None:
        constraints.append(_candidate("available USDT", None, "min_usdt_inventory NOT CONFIGURED", configured=False))
    else:
        constraints.append(_candidate("available USDT", cash / unit_cost, f"{_money(cash)} USDT available above floor"))

    max_notional = config.portfolio.max_trade_notional_usdt
    if max_notional is None:
        constraints.append(_candidate("max trade size", None, "max_trade_notional_usdt NOT CONFIGURED", configured=False))
    else:
        constraints.append(_candidate("max trade size", max_notional / price, f"cap {_money(max_notional)} USDT"))

    band = config.bands.for_regime(regime or "NEUTRAL")
    if band is None:
        constraints.append(_candidate("exposure band", None, "no exposure band configured", configured=False))
    else:
        headroom_value = (band / Decimal("100")) * total_value - state.btc_total * price
        constraints.append(
            _candidate("exposure band", max(ZERO, headroom_value) / price, f"target {dec_str(band)}% BTC exposure")
        )

    sleeve = config.sleeve.active_pct
    if sleeve is None:
        constraints.append(_candidate("active sleeve", None, "active_pct NOT CONFIGURED", configured=False))
    else:
        sleeve_value = (sleeve / Decimal("100")) * total_value
        tactical_value = state.btc_tactical * price + state.usdt_tactical
        constraints.append(
            _candidate("active sleeve", max(ZERO, sleeve_value - tactical_value) / price, f"sleeve {dec_str(sleeve)}% of portfolio")
        )

    risk_budget = config.portfolio.max_risk_per_trade_pct
    invalidation = bull.invalidation_price
    if risk_budget is None or invalidation is None or price <= invalidation:
        constraints.append(
            _candidate(
                "risk budget",
                None,
                "risk budget or invalidation reference NOT CONFIGURED",
                configured=False,
            )
        )
    else:
        allowance = (risk_budget / Decimal("100")) * total_value
        constraints.append(
            _candidate("risk budget", allowance / (price - invalidation), f"risk {dec_str(risk_budget)}% of portfolio")
        )

    return _finalise(constraints, side=TradeSide.BUY, price=price, config=config, state=state, total_value=total_value)


def assess_sell(
    *,
    config: Config,
    state: LedgerState,
    snapshot: MarketSnapshot,
    bear: ModuleAssessment,
    regime: str | None,
) -> AllocationAssessment:
    price = snapshot.price
    total_value = None if price is None else state.btc_total * price + state.usdt_total
    constraints: list[SizingConstraint] = []

    if price is None or total_value is None:
        return _finalise([], side=TradeSide.SELL, price=price, config=config, state=state, total_value=None)

    tradable = available_btc(state, config)
    if config.portfolio.min_btc_inventory is None:
        constraints.append(_candidate("available BTC", None, "min_btc_inventory NOT CONFIGURED", configured=False))
    else:
        constraints.append(_candidate("available BTC", tradable, f"{dec_str(tradable)} BTC above floor and reservations"))

    mechanism = bear.mechanism
    cap = None if mechanism is None else config.mechanisms.cap_for(mechanism.value)
    if mechanism is None or cap is None:
        constraints.append(_candidate("mechanism cap", None, "selling mechanism cap NOT CONFIGURED", configured=False))
    else:
        constraints.append(_candidate("mechanism cap", tradable * cap, f"{mechanism.value} limited to {dec_str(cap)} of available"))

    max_notional = config.portfolio.max_trade_notional_usdt
    if max_notional is None:
        constraints.append(_candidate("max trade size", None, "max_trade_notional_usdt NOT CONFIGURED", configured=False))
    else:
        constraints.append(_candidate("max trade size", max_notional / price, f"cap {_money(max_notional)} USDT"))

    band = config.bands.for_regime(regime or "NEUTRAL")
    if band is None:
        constraints.append(_candidate("exposure band", None, "no exposure band configured", configured=False))
    else:
        excess_value = state.btc_total * price - (band / Decimal("100")) * total_value
        if excess_value > ZERO:
            constraints.append(
                _candidate("exposure band", excess_value / price, f"reduce toward {dec_str(band)}% BTC exposure")
            )
        else:
            # Below the band, allocation does not forbid a risk-reduction or
            # defensive sale; it simply contributes no additional quantity cap.
            constraints.append(
                _candidate(
                    "exposure band",
                    None,
                    f"BTC exposure already at or below the {dec_str(band)}% band; no band-driven quantity",
                )
            )

    return _finalise(constraints, side=TradeSide.SELL, price=price, config=config, state=state, total_value=total_value)


def no_action(reason: str, *, snapshot: MarketSnapshot) -> AllocationAssessment:
    return AllocationAssessment(
        side=None,
        status="NOT APPLICABLE",
        quantity=None,
        limit_price=snapshot.price,
        notional=None,
        fee_usdt=None,
        slippage_usdt=None,
        total_cost_usdt=None,
        binding_constraint=None,
        constraints=(),
        notes=(reason,),
    )


__all__ = ["assess_buy", "assess_sell", "no_action"]
