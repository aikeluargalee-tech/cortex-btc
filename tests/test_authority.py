"""Acceptance tests for the decision authority (handover section 20)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from conftest import make_ledger, make_market, make_portfolio

from cortex import authority
from cortex.authority import decide
from cortex.domain import (
    DataStatus,
    GateStatus,
    ModuleAssessment,
    ModuleState,
    PortfolioState,
    Verdict,
)
from cortex.ledger import available_btc, available_usdt


def run(config, market, ledger=None, previous=None) -> authority.Decision:
    ledger = ledger or make_ledger()
    portfolio = make_portfolio(ledger, config, market.price)
    return decide(config=config, market=market, portfolio=portfolio, previous_state=previous)


def test_qualified_accumulation_produces_buy(config):
    decision = run(config, make_market(config))
    assert decision.bullish.state is ModuleState.ELIGIBLE
    assert decision.verdict is Verdict.BUY
    assert decision.proposed_order is not None
    assert decision.proposed_order.quantity > 0
    assert decision.proposed_order.side.value == "BUY"


def test_buy_size_respects_available_usdt(config):
    decision = run(config, make_market(config))
    order = decision.proposed_order
    available = available_usdt(make_ledger().state(), config)
    assert order is not None
    assert order.notional + order.fee_usdt <= available


def test_unfunded_buy_is_never_issued(config):
    ledger = make_ledger(usdt_tactical="1000")  # exactly the floor, nothing spare
    decision = run(config, make_market(config), ledger=ledger)
    assert decision.bullish.state is ModuleState.ELIGIBLE
    assert decision.verdict is Verdict.HOLD
    assert decision.proposed_order is None
    assert any(g.gate == 7 and g.status is GateStatus.FAIL for g in decision.gates)


def test_missing_critical_data_forces_abstain(config):
    market = make_market(config, price=None)
    decision = run(config, market)
    assert decision.verdict is Verdict.ABSTAIN
    assert decision.portfolio_state is PortfolioState.DATA_HALT
    assert decision.bullish.state is ModuleState.DATA_HALT
    assert decision.proposed_order is None


def test_stale_cvd_fails_data_gate(config):
    market = make_market(config, spot_cvd_slope_15m=("0.12", DataStatus.STALE))
    decision = run(config, market)
    assert decision.verdict is Verdict.ABSTAIN
    assert decision.gates[0].status is GateStatus.FAIL
    assert any("stale" in reason for reason in decision.gates[0].reasons)


def test_unreconciled_portfolio_forces_review(config):
    ledger = make_ledger()
    portfolio = make_portfolio(ledger, config)
    decision = decide(config=config, market=make_market(config), portfolio=portfolio, seeded=False)
    assert decision.verdict is Verdict.ABSTAIN
    assert decision.portfolio_state is PortfolioState.HUMAN_REVIEW
    assert decision.gates[1].status is GateStatus.FAIL


def test_no_opportunity_holds_inventory(config):
    # Price far above support and below resistance: neither side can confirm.
    market = make_market(config, price=("66000", DataStatus.VERIFIED), support="64600", vah="64700")
    decision = run(config, market)
    assert decision.verdict is Verdict.HOLD
    assert decision.proposed_order is None
    assert decision.portfolio_state is PortfolioState.HOLD_BTC


def test_sharp_fall_without_confirmation_does_not_buy(config):
    market = make_market(
        config,
        price=("63000", DataStatus.VERIFIED),
        support="64600",
        sweep_low="62900",
        spot_cvd_slope_15m="-0.4",
        demand_absorption="0.2",
    )
    decision = run(config, market)
    assert decision.bullish.state in (ModuleState.INVALIDATED, ModuleState.WATCH, ModuleState.SETUP)
    assert decision.verdict is not Verdict.BUY


def test_resistance_touch_alone_does_not_sell(config):
    market = make_market(
        config,
        price=("65500", DataStatus.VERIFIED),
        resistance="65600",
        supply_absorption="0.2",
        spot_delta_15m="40",
        price_change_15m_pct="0.4",
        spot_cvd_slope_15m="0.2",
    )
    decision = run(config, market)
    assert decision.verdict is not Verdict.SELL


def test_qualified_distribution_sells_within_tactical_inventory(config):
    market = make_market(
        config,
        price=("64600", DataStatus.VERIFIED),
        support="64600",
        resistance="64700",
        vah="64700",
        supply_absorption="0.62",
        spot_delta_15m="25",
        price_change_15m_pct="0.0",
        spot_cvd_slope_15m="-0.12",
        sma="64000",
    )
    decision = run(config, market)
    assert decision.bearish.state is ModuleState.ELIGIBLE
    assert decision.bearish.mechanism is not None
    assert decision.verdict is Verdict.SELL
    order = decision.proposed_order
    assert order is not None
    assert order.side.value == "SELL"
    assert order.quantity <= available_btc(make_ledger().state(), config)


def test_sell_never_reaches_protected_core(config):
    ledger = make_ledger(btc_core="0.20", btc_tactical="0")
    market = make_market(
        config,
        price=("64600", DataStatus.VERIFIED),
        support="64600",
        resistance="64700",
        vah="64700",
        supply_absorption="0.62",
        spot_delta_15m="25",
        price_change_15m_pct="0.0",
        spot_cvd_slope_15m="-0.12",
    )
    decision = run(config, market, ledger=ledger)
    assert decision.verdict is Verdict.HOLD
    assert decision.proposed_order is None
    assert decision.allocation.quantity in (None, Decimal("0"))


def test_conflict_between_modules_abstains(config, monkeypatch):
    def qualified(module: str, state: ModuleState):
        def inner(*args, **kwargs):
            return ModuleAssessment(
                module=module,
                state=state,
                conditions=(),
                summary="forced for conflict test",
                proposed_limit_price=Decimal("64800"),
                invalidation_price=Decimal("64000"),
            )

        return inner

    monkeypatch.setattr(authority, "assess_accumulation", qualified("A", ModuleState.ELIGIBLE))
    monkeypatch.setattr(authority, "assess_distribution", qualified("B", ModuleState.ELIGIBLE))
    decision = run(config, make_market(config))
    assert decision.verdict is Verdict.ABSTAIN
    assert decision.portfolio_state is PortfolioState.HUMAN_REVIEW
    assert decision.gates[5].status is GateStatus.FAIL


def test_same_inputs_are_deterministic(config):
    market = make_market(config)
    ledger = make_ledger()
    first = run(config, market, ledger)
    second = run(config, market, ledger)
    assert first.replay_key == second.replay_key
    assert first.decision_id == second.decision_id
    assert [g.status for g in first.gates] == [g.status for g in second.gates]
    assert first.verdict == second.verdict


def test_every_gate_is_reported(config):
    decision = run(config, make_market(config))
    assert [g.gate for g in decision.gates] == list(range(1, 11))


def test_value_area_horizon_is_configurable_and_defaults_to_a_day(config):
    assert config.analysis.value_area_hours == 24
    assert config.to_dict()["analysis"]["value_area_hours"] == 24


def test_failed_reclaim_requires_an_actual_test(config):
    """Being below value is not a failed reclaim; price has to test it first."""
    market = make_market(
        config,
        price=("64600", DataStatus.VERIFIED),
        support="64600",
        resistance="64700",
        vah="64700",
        recent_high="64650",
        supply_absorption="0.62",
        spot_delta_15m="25",
        price_change_15m_pct="0.0",
        spot_cvd_slope_15m="-0.12",
        sma="64000",
    )
    decision = run(config, market)
    condition = next(c for c in decision.bearish.conditions if c.id == "B4")
    assert condition.status.value == "FAIL"
    assert "no test" in condition.detail


def test_unconfigured_profile_cannot_produce_a_verdict_beyond_hold_or_abstain():
    from cortex.config import empty_config
    from cortex.domain import build_market_snapshot

    config = empty_config()
    market = make_market(config)
    decision = run(config, market)
    assert decision.verdict in (Verdict.HOLD, Verdict.ABSTAIN)
    assert decision.proposed_order is None
