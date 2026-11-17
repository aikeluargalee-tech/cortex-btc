"""Module D — final decision authority.

Ten gates, evaluated in order, each with an explicit result. The verdict is a
deterministic function of those gates; there is no score, no weighting and no
room for a second authority to issue a competing instruction.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Iterable

from . import allocation
from .config import Config
from .domain import (
    AllocationAssessment,
    Decision,
    GateResult,
    GateStatus,
    LedgerState,
    Loop,
    MarketSnapshot,
    ModuleAssessment,
    ModuleState,
    PortfolioSnapshot,
    PortfolioState,
    ProposedOrder,
    SellMechanism,
    TradeSide,
    Verdict,
    content_hash,
    dec_str,
    make_decision_id,
    utc_now,
)
from .ledger import available_btc, available_usdt, integrity_check
from .modules import assess_accumulation, assess_distribution, classify_regime, data_halt_reasons

ZERO = Decimal("0")
BP = Decimal("10000")

QUALIFIED = {ModuleState.CONFIRMED, ModuleState.ELIGIBLE}
MECHANISM_TO_STATE = {
    SellMechanism.PROFIT_TAKING: PortfolioState.REDUCTION,
    SellMechanism.RISK_REDUCTION: PortfolioState.REDUCTION,
    SellMechanism.DEFENSIVE_EXIT: PortfolioState.DEFENSIVE,
}


def _gate(number: int, name: str, status: GateStatus, detail: str, reasons: Iterable[str] = ()) -> GateResult:
    return GateResult(gate=number, name=name, status=status, detail=detail, reasons=tuple(reasons))


def _portfolio_state(
    *,
    verdict: Verdict,
    previous: PortfolioState | None,
    btc_total: Decimal,
    halt: bool,
    review: bool,
    mechanism: SellMechanism | None,
    regime: str | None,
) -> PortfolioState:
    if halt:
        return PortfolioState.DATA_HALT
    if review:
        return PortfolioState.HUMAN_REVIEW
    if verdict is Verdict.BUY:
        if previous in (PortfolioState.ROTATION_TO_USDT, PortfolioState.DEFENSIVE):
            return PortfolioState.REACCUMULATION
        return PortfolioState.ACCUMULATION
    if verdict is Verdict.SELL:
        if mechanism is SellMechanism.DEFENSIVE_EXIT or regime in ("DEFENSIVE", "DISLOCATION"):
            return PortfolioState.DEFENSIVE
        if previous is PortfolioState.DEFENSIVE:
            return PortfolioState.ROTATION_TO_USDT
        return PortfolioState.REDUCTION
    if btc_total <= ZERO:
        return PortfolioState.ALL_USDT
    if previous in (PortfolioState.DATA_HALT, PortfolioState.HUMAN_REVIEW) or previous is None:
        return PortfolioState.HOLD_BTC if btc_total > ZERO else PortfolioState.ALL_USDT
    return previous


def decide(
    *,
    config: Config,
    market: MarketSnapshot,
    portfolio: PortfolioSnapshot,
    previous_state: PortfolioState | None = None,
    seeded: bool = True,
    registered_report: tuple[bool, list[str]] | None = None,
) -> Decision:
    state = portfolio.ledger
    price = market.price
    halts = data_halt_reasons(market, config)
    gates: list[GateResult] = []

    # Gate 1 — data integrity -------------------------------------------------
    if halts:
        gates.append(_gate(1, "Data integrity", GateStatus.FAIL, "mandatory market feeds are not verified", halts))
    else:
        gates.append(_gate(1, "Data integrity", GateStatus.PASS, f"all {len(config.mandatory_feeds)} mandatory feeds verified"))

    # Gate 2 — portfolio integrity -------------------------------------------
    if not seeded:
        gates.append(_gate(2, "Portfolio integrity", GateStatus.FAIL, "no balances have been recorded", ("ledger has no seed event",)))
        ledger_ok = False
    else:
        ledger = None  # integrity_check needs the ledger object, supplied by caller below
        problems: list[str] = []
        if registered_report is not None:
            ok, reasons = registered_report
            if not ok:
                problems.extend(reasons)
        if state.reserved("BTC") > state.btc_tactical:
            problems.append("BTC reservations exceed tactical inventory")
        if state.reserved("USDT") > state.usdt_tactical:
            problems.append("USDT reservations exceed tactical inventory")
        if state.btc_total < ZERO or state.usdt_total < ZERO:
            problems.append("negative balance")
        if problems:
            gates.append(_gate(2, "Portfolio integrity", GateStatus.FAIL, "ledger cannot be reconciled", problems))
            ledger_ok = False
        else:
            gates.append(_gate(2, "Portfolio integrity", GateStatus.PASS, "ledger balances and reservations reconcile"))
            ledger_ok = True

    # Gate 3 — regime ---------------------------------------------------------
    regime, regime_detail = classify_regime(market, config)
    if regime is None:
        gates.append(_gate(3, "Market regime", GateStatus.UNAVAILABLE, regime_detail))
    else:
        gates.append(_gate(3, "Market regime", GateStatus.PASS, f"{regime} — {regime_detail}"))

    # Gates 4 and 5 — the two independent assessments ------------------------
    bull = assess_accumulation(market, config)
    bear = assess_distribution(market, config, regime)
    gates.append(_module_gate(4, "Bullish assessment (Module A)", bull))
    gates.append(_module_gate(5, "Bearish assessment (Module B)", bear))

    bull_qualified = bull.state in QUALIFIED
    bear_qualified = bear.state in QUALIFIED

    # Gate 6 — conflict resolution -------------------------------------------
    conflict = bull_qualified and bear_qualified
    if conflict:
        gates.append(
            _gate(
                6,
                "Conflict resolution",
                GateStatus.FAIL,
                "both modules qualified for the same snapshot",
                ("accumulation qualified", "distribution qualified"),
            )
        )
    elif bull.state is ModuleState.REVIEW or bear.state is ModuleState.REVIEW:
        gates.append(_gate(6, "Conflict resolution", GateStatus.UNAVAILABLE, "an assessment is in REVIEW"))
    else:
        gates.append(_gate(6, "Conflict resolution", GateStatus.PASS, "no material conflict between assessments"))

    # Gate 7 and 8 — allocation, sizing --------------------------------------
    side: TradeSide | None = None
    if bull_qualified and not conflict:
        side = TradeSide.BUY
    elif bear_qualified and not conflict:
        side = TradeSide.SELL

    if side is TradeSide.BUY:
        alloc = allocation.assess_buy(config=config, state=state, snapshot=market, bull=bull, regime=regime)
    elif side is TradeSide.SELL:
        alloc = allocation.assess_sell(config=config, state=state, snapshot=market, bear=bear, regime=regime)
    else:
        alloc = allocation.no_action("no qualified opportunity", snapshot=market)

    if side is None:
        gates.append(_gate(7, "Allocation and risk", GateStatus.UNAVAILABLE, "no transaction to size"))
        gates.append(_gate(8, "Transaction sizing", GateStatus.UNAVAILABLE, "no transaction to size"))
    else:
        if alloc.status == "READY" and alloc.floor_compliant is not False:
            gates.append(
                _gate(
                    7,
                    "Allocation and risk",
                    GateStatus.PASS,
                    f"binding constraint: {alloc.binding_constraint}",
                    (),
                )
            )
        elif alloc.status == "NOT CONFIGURED":
            gates.append(_gate(7, "Allocation and risk", GateStatus.UNAVAILABLE, "portfolio constraints are not configured", alloc.notes))
        else:
            gates.append(_gate(7, "Allocation and risk", GateStatus.FAIL, "the portfolio controller refused the size", alloc.notes))

        if alloc.quantity is not None and alloc.quantity > ZERO and alloc.status == "READY":
            gates.append(
                _gate(
                    8,
                    "Transaction sizing",
                    GateStatus.PASS,
                    f"{dec_str(alloc.quantity)} BTC at {dec_str(alloc.limit_price)} = {dec_str(alloc.notional.quantize(Decimal('0.01')))} USDT",
                )
            )
        else:
            gates.append(_gate(8, "Transaction sizing", GateStatus.FAIL, "no funded, permitted quantity", alloc.notes))

    # Gate 9 — execution economics -------------------------------------------
    all_in = config.fees.all_in_bps
    if all_in is None or config.execution.max_total_cost_bps is None:
        gates.append(_gate(9, "Execution economics", GateStatus.UNAVAILABLE, "fee or cost ceiling NOT CONFIGURED"))
    elif all_in > config.execution.max_total_cost_bps:
        gates.append(_gate(9, "Execution economics", GateStatus.FAIL, f"all-in cost {dec_str(all_in)} bps exceeds ceiling"))
    else:
        gates.append(_gate(9, "Execution economics", GateStatus.PASS, f"all-in cost {dec_str(all_in)} bps within ceiling"))

    # Gate 10 — verdict -------------------------------------------------------
    verdict, explanation, notes, review = _verdict(
        gates=gates,
        halts=halts,
        bull=bull,
        bear=bear,
        conflict=conflict,
        alloc=alloc,
        side=side,
        bull_qualified=bull_qualified,
        bear_qualified=bear_qualified,
        ledger_ok=ledger_ok,
        regime=regime,
    )

    gates.append(_gate(10, "Final verdict", GateStatus.PASS if verdict is not Verdict.ABSTAIN else GateStatus.FAIL, verdict.value, notes))

    halt = bool(halts) or verdict is Verdict.ABSTAIN and any(g.gate == 1 and g.status is GateStatus.FAIL for g in gates)
    new_state = _portfolio_state(
        verdict=verdict,
        previous=previous_state,
        btc_total=state.btc_total,
        halt=halt,
        review=review,
        mechanism=bear.mechanism,
        regime=regime,
    )

    proposed = None
    if verdict in (Verdict.BUY, Verdict.SELL) and alloc.quantity and alloc.limit_price:
        side_value = TradeSide.BUY if verdict is Verdict.BUY else TradeSide.SELL
        order_seed = {
            "market": market.snapshot_id,
            "portfolio": portfolio.snapshot_id,
            "config": config.config_hash,
            "side": side_value.value,
            "qty": dec_str(alloc.quantity),
            "price": dec_str(alloc.limit_price),
        }
        proposed = ProposedOrder(
            order_id="ord_" + content_hash(order_seed)[:18],
            side=side_value,
            limit_price=alloc.limit_price,
            quantity=alloc.quantity,
            notional=alloc.notional or ZERO,
            fee_usdt=alloc.fee_usdt or ZERO,
            expiry_minutes=config.execution.order_expiry_minutes or 0,
            invalidation=(
                "price loses structural reference"
                if verdict is Verdict.BUY
                else "price reclaims the supply region"
            ),
            loop=Loop.A if verdict is Verdict.BUY else Loop.B,
        )

    replay_key = content_hash(
        {
            "market": market.snapshot_id,
            "portfolio": portfolio.snapshot_id,
            "config": config.config_hash,
            "previous_state": None if previous_state is None else previous_state.value,
        }
    )

    return Decision(
        decision_id=make_decision_id(replay_key),
        created_at=utc_now(),
        verdict=verdict,
        portfolio_state=new_state,
        previous_state=previous_state,
        explanation=explanation,
        gates=tuple(gates),
        bullish=bull,
        bearish=bear,
        allocation=alloc,
        proposed_order=proposed,
        market_snapshot=market,
        portfolio_snapshot=portfolio,
        config_version=config.config_version,
        config_hash=config.config_hash,
        replay_key=replay_key,
        notes=tuple(notes),
        plain_english=_plain_english(verdict, bull, bear, alloc, halts, regime, state),
    )


def _module_gate(number: int, name: str, assessment: ModuleAssessment) -> GateResult:
    if assessment.state is ModuleState.DATA_HALT:
        return _gate(number, name, GateStatus.UNAVAILABLE, "assessment halted on data", assessment.data_halt_reasons)
    if assessment.state in QUALIFIED:
        return _gate(number, name, GateStatus.PASS, f"{assessment.state.value} — {assessment.summary}")
    return _gate(number, name, GateStatus.FAIL, f"{assessment.state.value} — {assessment.summary}")


def _verdict(
    *,
    gates: list[GateResult],
    halts: tuple[str, ...],
    bull: ModuleAssessment,
    bear: ModuleAssessment,
    conflict: bool,
    alloc: AllocationAssessment,
    side: TradeSide | None,
    bull_qualified: bool,
    bear_qualified: bool,
    ledger_ok: bool,
    regime: str | None,
) -> tuple[Verdict, str, list[str], bool]:
    notes: list[str] = []
    review = False

    if halts:
        notes.append("DATA HALT: " + "; ".join(halts))
        return Verdict.ABSTAIN, "Critical market data is not verified, so no transaction decision may be formed.", notes, False

    if not ledger_ok:
        review = True
        notes.append("Portfolio records cannot be reconciled.")
        return Verdict.ABSTAIN, "The portfolio ledger cannot be reconciled; new transaction approvals are suspended.", notes, review

    if regime is None:
        review = True
        notes.append("Market regime could not be classified from configured rules.")
        return Verdict.ABSTAIN, "Market regime is unavailable, so permitted exposure cannot be established.", notes, review

    if conflict:
        review = True
        notes.append("Module A and Module B both qualified on the same snapshot.")
        return Verdict.ABSTAIN, "Both directions qualified simultaneously; a forced trade would compromise decision integrity.", notes, review

    if bull_qualified and bear.state is ModuleState.REVIEW:
        review = True
        return Verdict.ABSTAIN, "Bearish evidence is unresolved, so a purchase cannot be authorised.", notes, review
    if bear_qualified and bull.state is ModuleState.REVIEW:
        review = True
        return Verdict.ABSTAIN, "Bullish evidence is unresolved, so a sale cannot be authorised.", notes, review

    if side is None:
        return Verdict.HOLD, "No qualified transaction; inventory is maintained unchanged.", notes, False

    if alloc.status == "NOT CONFIGURED":
        review = True
        notes.extend(alloc.notes)
        return Verdict.ABSTAIN, "The portfolio controller lacks the configuration required to size this transaction.", notes, review

    if alloc.status != "READY" or alloc.quantity is None or alloc.quantity <= ZERO:
        direction = "purchase" if side is TradeSide.BUY else "sale"
        notes.extend(alloc.notes)
        notes.append(f"A qualified {direction} existed but no permitted size survived the allocation controller.")
        return Verdict.HOLD, f"Opportunity qualified, but the permitted {direction} size is zero under current inventory and risk rules.", notes, False

    if side is TradeSide.BUY:
        notes.append(f"Size bound by {alloc.binding_constraint}.")
        return Verdict.BUY, "A qualified accumulation passed market, portfolio, risk and inventory checks. Execution still requires human approval.", notes, False

    mechanism = bear.mechanism.value if bear.mechanism else "UNCLASSIFIED"
    notes.append(f"Exit classification: {mechanism}. Size bound by {alloc.binding_constraint}.")
    return Verdict.SELL, f"A qualified {mechanism} sale passed market, portfolio, risk and inventory checks. Execution still requires human approval.", notes, False


def _plain_english(
    verdict: Verdict,
    bull: ModuleAssessment,
    bear: ModuleAssessment,
    alloc: AllocationAssessment,
    halts: tuple[str, ...],
    regime: str | None,
    state: LedgerState,
) -> str:
    if halts:
        return (
            "CORTEX is not making a call right now. "
            + f"{len(halts)} mandatory data check(s) failed, so any BUY or SELL would be unsupported. "
            "The correct action is to do nothing until the feeds recover."
        )
    parts = [f"Accumulation is in {bull.state.value} and distribution is in {bear.state.value}."]
    if regime:
        parts.append(f"Regime reads {regime}.")
    if verdict is Verdict.HOLD:
        if bull.state in QUALIFIED or bear.state in QUALIFIED:
            parts.append(
                "One side qualified, but the portfolio controller would not permit a positive size "
                f"under the current exposure, sleeve, inventory and risk rules ({alloc.binding_constraint or 'no constraint available'}). "
                "CORTEX does not force a trade it cannot fund within your own limits."
            )
        else:
            parts.append(
                "Neither side produced a fully confirmed opportunity that survives portfolio and risk limits, "
                "so the portfolio keeps its current inventory."
            )
    elif verdict is Verdict.BUY:
        parts.append(
            f"A purchase of {dec_str(alloc.quantity)} BTC is proposed, limited by {alloc.binding_constraint}. "
            "CORTEX does not execute; you would place the limit order yourself."
        )
    elif verdict is Verdict.SELL:
        mechanism = bear.mechanism.value if bear.mechanism else "sale"
        parts.append(
            f"A {mechanism} sale of {dec_str(alloc.quantity)} BTC is proposed, limited by {alloc.binding_constraint}. "
            "Core holdings are untouched and execution remains manual."
        )
    else:
        parts.append(
            "The system is withholding a decision because something required for integrity is missing or conflicting."
        )
    return " ".join(parts)


__all__ = ["decide", "QUALIFIED"]
