"""Acceptance tests for the stance authority."""

from __future__ import annotations

from decimal import Decimal

from conftest import make_market

from cortex import authority
from cortex.authority import decide
from cortex.domain import DataStatus, GateStatus, ModuleAssessment, ModuleState, SellClass, Verdict


def run(config, market) -> authority.Decision:
    return decide(config=config, market=market)


def test_qualified_accumulation_publishes_a_buy_zone(config):
    decision = run(config, make_market(config))
    assert decision.bullish.state is ModuleState.ELIGIBLE
    assert decision.verdict is Verdict.QUALIFIED_BUY_ZONE
    assert decision.bearish.state is not ModuleState.ELIGIBLE


def test_qualified_distribution_publishes_a_sell_zone(config):
    market = make_market(
        config,
        price=("64600", DataStatus.VERIFIED),
        support="64600",
        resistance="64700",
        vah="64700",
        recent_high="64800",
        supply_absorption="0.62",
        spot_delta_15m="25",
        price_change_15m_pct="0.0",
        spot_cvd_slope_15m="-0.12",
        sma="64000",
    )
    decision = run(config, market)
    assert decision.bearish.state is ModuleState.ELIGIBLE
    assert decision.bearish.sell_class is SellClass.DISTRIBUTION
    assert decision.verdict is Verdict.QUALIFIED_SELL_ZONE


def test_breakdown_is_classified_separately_from_distribution(config):
    market = make_market(
        config,
        price=("64300", DataStatus.VERIFIED),
        support="64600",
        resistance="64700",
        vah="64700",
        recent_high="64800",
        supply_absorption="0.62",
        spot_delta_15m="25",
        price_change_15m_pct="0.0",
        spot_cvd_slope_15m="-0.12",
        sma="64000",
    )
    decision = run(config, market)
    assert decision.bearish.sell_class is SellClass.BREAKDOWN
    assert decision.verdict is Verdict.QUALIFIED_SELL_ZONE


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
    assert decision.verdict is not Verdict.QUALIFIED_SELL_ZONE


def test_missing_critical_data_produces_data_halt(config):
    decision = run(config, make_market(config, price=None))
    assert decision.verdict is Verdict.DATA_HALT
    assert decision.gates[0].status is GateStatus.FAIL


def test_stale_cvd_fails_the_data_gate(config):
    decision = run(config, make_market(config, spot_cvd_slope_15m=("0.12", DataStatus.STALE)))
    assert decision.verdict is Verdict.DATA_HALT
    assert any("stale" in reason for reason in decision.gates[0].reasons)


def test_missing_regime_produces_data_halt(config):
    decision = run(config, make_market(config, sma=None))
    assert decision.verdict is Verdict.DATA_HALT
    assert decision.gates[1].status is GateStatus.UNAVAILABLE


def test_no_opportunity_is_no_setup(config):
    decision = run(config, make_market(config, price=("66000", DataStatus.VERIFIED), support="64600", vah="64700"))
    assert decision.verdict is Verdict.NO_SETUP


def test_both_sides_qualified_is_conflict(config, monkeypatch):
    def qualified(module: str):
        def inner(*args, **kwargs):
            return ModuleAssessment(
                module=module,
                state=ModuleState.ELIGIBLE,
                conditions=(),
                summary="forced for conflict test",
                reference_level=Decimal("64800"),
            )

        return inner

    monkeypatch.setattr(authority, "assess_accumulation", qualified("A"))
    monkeypatch.setattr(authority, "assess_distribution", qualified("B"))
    decision = run(config, make_market(config))
    assert decision.verdict is Verdict.CONFLICT
    assert decision.gates[4].status is GateStatus.FAIL


def test_resistance_touch_alone_is_not_a_sell_zone(config):
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
    assert decision.verdict is not Verdict.QUALIFIED_SELL_ZONE


def test_sharp_fall_without_confirmation_is_not_a_buy_zone(config):
    market = make_market(
        config,
        price=("63000", DataStatus.VERIFIED),
        support="64600",
        sweep_low="62900",
        spot_cvd_slope_15m="-0.4",
        demand_absorption="0.2",
    )
    decision = run(config, market)
    assert decision.verdict is not Verdict.QUALIFIED_BUY_ZONE


def test_same_snapshot_is_deterministic(config):
    market = make_market(config)
    first = run(config, market)
    second = run(config, market)
    assert first.replay_key == second.replay_key
    assert first.decision_id == second.decision_id
    assert [g.status for g in first.gates] == [g.status for g in second.gates]


def test_every_gate_is_reported(config):
    decision = run(config, make_market(config))
    assert [g.gate for g in decision.gates] == [1, 2, 3, 4, 5, 6]


def test_unconfigured_profile_never_publishes_a_zone():
    from cortex.config import empty_config

    config = empty_config()
    decision = run(config, make_market(config))
    assert decision.verdict in (Verdict.NO_SETUP, Verdict.DATA_HALT, Verdict.CONFLICT)


def test_value_area_horizon_is_configurable_and_defaults_to_a_day(config):
    assert config.analysis.value_area_hours == 24
    assert config.to_dict()["analysis"]["value_area_hours"] == 24
