"""The stance authority — one market verdict per snapshot.

Six gates, evaluated in order, each with an explicit result. There is no score,
no weighting, and no instruction to transact: the output describes a market
condition, and the reader decides what to do with it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from .config import Config
from .domain import (
    Decision,
    GateResult,
    GateStatus,
    MarketSnapshot,
    ModuleAssessment,
    ModuleState,
    SellClass,
    Verdict,
    content_hash,
    make_decision_id,
    utc_now,
)
from .modules import assess_accumulation, assess_distribution, classify_regime, data_halt_reasons

QUALIFIED = {ModuleState.CONFIRMED, ModuleState.ELIGIBLE}


def _gate(number: int, name: str, status: GateStatus, detail: str, reasons: Iterable[str] = ()) -> GateResult:
    return GateResult(gate=number, name=name, status=status, detail=detail, reasons=tuple(reasons))


def decide(*, config: Config, market: MarketSnapshot) -> Decision:
    halts = data_halt_reasons(market, config)
    gates: list[GateResult] = []

    # Gate 1 — data integrity ------------------------------------------------
    if halts:
        gates.append(_gate(1, "Data integrity", GateStatus.FAIL, "mandatory market feeds are not verified", halts))
    else:
        gates.append(
            _gate(1, "Data integrity", GateStatus.PASS, f"all {len(config.mandatory_feeds)} mandatory feeds verified")
        )

    # Gate 2 — regime --------------------------------------------------------
    regime, regime_detail = classify_regime(market, config)
    if regime is None:
        gates.append(_gate(2, "Market regime", GateStatus.UNAVAILABLE, regime_detail))
    else:
        gates.append(_gate(2, "Market regime", GateStatus.PASS, f"{regime} — {regime_detail}"))

    # Gates 3 and 4 — the two independent assessments ------------------------
    bull = assess_accumulation(market, config)
    bear = assess_distribution(market, config, regime)
    gates.append(_module_gate(3, "Bullish assessment (Module A)", bull))
    gates.append(_module_gate(4, "Bearish assessment (Module B)", bear))

    bull_qualified = bull.state in QUALIFIED
    bear_qualified = bear.state in QUALIFIED
    unresolved = ModuleState.REVIEW

    # Gate 5 — conflict resolution ------------------------------------------
    conflict = (bull_qualified and bear_qualified) or (
        (bull_qualified and bear.state is unresolved) or (bear_qualified and bull.state is unresolved)
    )
    if conflict:
        gates.append(
            _gate(
                5,
                "Conflict resolution",
                GateStatus.FAIL,
                "bullish and bearish evidence point in opposite directions on the same snapshot",
                tuple(
                    filter(
                        None,
                        (
                            f"accumulation {bull.state.value}" if bull_qualified else None,
                            f"distribution {bear.state.value}" if bear_qualified else None,
                            "an assessment is unresolved (REVIEW)" if bull.state is unresolved or bear.state is unresolved else None,
                        ),
                    )
                ),
            )
        )
    else:
        gates.append(_gate(5, "Conflict resolution", GateStatus.PASS, "no material conflict between assessments"))

    # Gate 6 — the stance ----------------------------------------------------
    verdict, explanation, notes = _stance(
        halts=halts,
        regime=regime,
        bull=bull,
        bear=bear,
        conflict=conflict,
        bull_qualified=bull_qualified,
        bear_qualified=bear_qualified,
    )
    gates.append(_gate(6, "Stance", GateStatus.PASS, verdict.value))

    replay_key = content_hash(
        {
            "market": market.snapshot_id,
            "config": config.config_hash,
        }
    )

    return Decision(
        decision_id=make_decision_id(replay_key),
        created_at=utc_now(),
        verdict=verdict,
        explanation=explanation,
        plain_english=_plain_english(verdict, bull, bear, regime, halts),
        gates=tuple(gates),
        bullish=bull,
        bearish=bear,
        market_snapshot=market,
        config_version=config.config_version,
        config_hash=config.config_hash,
        replay_key=replay_key,
        notes=tuple(notes),
    )


def _module_gate(number: int, name: str, assessment: ModuleAssessment) -> GateResult:
    if assessment.state is ModuleState.DATA_HALT:
        return _gate(number, name, GateStatus.UNAVAILABLE, "assessment halted on data", assessment.data_halt_reasons)
    if assessment.state in QUALIFIED:
        return _gate(number, name, GateStatus.PASS, f"{assessment.state.value} — {assessment.summary}")
    return _gate(number, name, GateStatus.FAIL, f"{assessment.state.value} — {assessment.summary}")


def _stance(
    *,
    halts: tuple[str, ...],
    regime: str | None,
    bull: ModuleAssessment,
    bear: ModuleAssessment,
    conflict: bool,
    bull_qualified: bool,
    bear_qualified: bool,
) -> tuple[Verdict, str, list[str]]:
    notes: list[str] = []

    if halts:
        notes.append("DATA HALT: " + "; ".join(halts))
        return (
            Verdict.DATA_HALT,
            "A mandatory feed is stale, missing or conflicting, so no stance may be published.",
            notes,
        )

    if regime is None:
        notes.append("Market regime could not be classified from the configured rules.")
        return Verdict.DATA_HALT, "Regime is unavailable, so the evidence cannot be placed in context.", notes

    if conflict:
        notes.append("Bullish and bearish evidence are simultaneously strong, or one side is unresolved.")
        return (
            Verdict.CONFLICT,
            "The evidence points both ways on this snapshot; a stance would be manufactured, not observed.",
            notes,
        )

    if bull_qualified:
        notes.append(f"Accumulation qualified in a {regime} regime.")
        return (
            Verdict.QUALIFIED_BUY_ZONE,
            "Selling was absorbed at a verified support and price reclaimed value: an accumulation zone.",
            notes,
        )

    if bear_qualified:
        sell_class = bear.sell_class.value if bear.sell_class else "distribution"
        notes.append(f"Distribution qualified as {sell_class} in a {regime} regime.")
        return (
            Verdict.QUALIFIED_SELL_ZONE,
            f"Supply absorbed the buying and price failed to hold value ({sell_class.lower()}): a distribution zone.",
            notes,
        )

    notes.append(f"Accumulation {bull.state.value}, distribution {bear.state.value}.")
    return Verdict.NO_SETUP, "Neither side has completed its confirmation sequence; there is nothing to act on.", notes


def _plain_english(
    verdict: Verdict,
    bull: ModuleAssessment,
    bear: ModuleAssessment,
    regime: str | None,
    halts: tuple[str, ...],
) -> str:
    if halts:
        return (
            "CORTEX is not publishing a stance. "
            f"{len(halts)} required data check(s) failed, and a stance built on stale or missing data is worthless."
        )
    parts = [f"Accumulation is {bull.state.value.lower()} and distribution is {bear.state.value.lower()}."]
    if regime:
        parts.append(f"Regime is {regime.lower()}.")
    if verdict is Verdict.QUALIFIED_BUY_ZONE:
        parts.append(
            "Sellers pushed into support and were absorbed; price has recovered back into value. "
            "That is the condition this system treats as an accumulation zone — not a prediction that price must rise."
        )
    elif verdict is Verdict.QUALIFIED_SELL_ZONE:
        cls = bear.sell_class.value.lower() if bear.sell_class else "distribution"
        parts.append(
            f"Buyers pushed into supply and were absorbed, and price failed to hold value — a {cls} condition. "
            "Distribution zones often resolve lower, but this is an observation, not a forecast."
        )
    elif verdict is Verdict.CONFLICT:
        parts.append(
            "Both sides produced credible evidence at once. Rather than pick the one that looks better, "
            "CORTEX declines to publish a stance."
        )
    else:
        parts.append(
            "The sequence is incomplete on both sides, so the honest output is that nothing is happening yet. "
            "Most of the time this is the correct answer."
        )
    return " ".join(parts)


__all__ = ["decide", "QUALIFIED"]
