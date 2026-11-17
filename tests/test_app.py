"""Store, configuration governance and the human order lifecycle."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from conftest import SEED, make_ledger, make_market, make_portfolio

from cortex.app import AppError, CortexApp
from cortex.authority import decide


@pytest.fixture()
def app(tmp_path) -> CortexApp:
    return CortexApp(tmp_path / "cortex.db", seed_config=SEED)


def test_config_is_seeded_and_versioned(app: CortexApp):
    first = app.config()
    assert first.config_version == json.loads(SEED.read_text(encoding="utf-8"))["config_version"]
    updated = app.change_config(
        {"buy.absorption_min": "0.60"},
        operator="operator",
        note="tighten absorption requirement",
    )
    assert updated.config_version == "cfg-0001"
    assert updated.buy.absorption_min == Decimal("0.60")
    assert app.config().config_version == "cfg-0001"
    history = app.config_history()
    assert history[-1]["changes"]["buy.absorption_min"] == {"from": "0.55", "to": "0.60"}


def test_config_change_requires_operator(app: CortexApp):
    with pytest.raises(AppError):
        app.change_config({"buy.absorption_min": "0.6"}, operator="  ", note="x")


def test_config_hash_changes_with_parameters(app: CortexApp):
    before = app.config().config_hash
    after = app.change_config({"buy.absorption_min": "0.61"}, operator="operator", note="tweak").config_hash
    assert before != after


def test_portfolio_can_only_be_seeded_once(app: CortexApp):
    app.set_portfolio(
        {"btc_core": "0.15", "btc_tactical": "0.05", "usdt_core": "0", "usdt_tactical": "5000"},
        operator="operator",
    )
    assert app.store.is_seeded()
    with pytest.raises(AppError):
        app.set_portfolio({"btc_core": "1"}, operator="operator")


def test_negative_balances_rejected(app: CortexApp):
    with pytest.raises(AppError):
        app.set_portfolio({"btc_core": "-0.1", "btc_tactical": "0", "usdt_core": "0", "usdt_tactical": "0"}, operator="o")


def test_decision_recording_is_idempotent(app: CortexApp):
    config = app.config()
    market = make_market(config)
    portfolio = make_portfolio(make_ledger(), config, market.price)
    decision = decide(config=config, market=market, portfolio=portfolio)
    assert app.store.save_decision(decision) is True
    assert app.store.save_decision(decision) is False
    assert len(app.store.decisions()) == 1


def test_approval_reserves_inventory_and_fill_updates_it(app: CortexApp):
    config = app.config()
    market = make_market(config)
    ledger = make_ledger()
    for event in ledger.events:
        app.store.append_event(event)
    portfolio = make_portfolio(ledger, config, market.price)
    decision = decide(config=config, market=market, portfolio=portfolio)
    app.store.save_decision(decision)
    assert decision.proposed_order is not None

    result = app.approve(decision.decision_id, operator="operator")
    assert result["order"]["status"] == "APPROVED"
    state = app.store.ledger().state()
    assert state.reserved("USDT") > Decimal("0")

    order_id = decision.proposed_order.order_id
    app.transition(order_id, "submit", {})
    app.transition(order_id, "work", {})

    quantity = Decimal("0.002")
    app.transition(
        order_id,
        "fill",
        {"quantity": str(quantity), "price": "64800", "fee": "1"},
    )
    after = app.store.ledger().state()
    assert after.btc_tactical == Decimal("0.05") + quantity
    order = app.store.order(order_id)
    assert order is not None
    assert order["status"] in ("PARTIALLY FILLED", "FILLED")


def test_unconfirmed_cancellation_keeps_reservation(app: CortexApp):
    config = app.config()
    market = make_market(config)
    ledger = make_ledger()
    for event in ledger.events:
        app.store.append_event(event)
    portfolio = make_portfolio(ledger, config, market.price)
    decision = decide(config=config, market=market, portfolio=portfolio)
    app.store.save_decision(decision)
    app.approve(decision.decision_id, operator="operator")
    order_id = decision.proposed_order.order_id  # type: ignore[union-attr]
    with pytest.raises(AppError):
        app.transition(order_id, "cancel", {"confirmed": False})
    assert app.store.ledger().state().reserved("USDT") > Decimal("0")
    app.transition(order_id, "cancel", {"confirmed": True})
    assert app.store.ledger().state().reserved("USDT") == Decimal("0")


def test_order_transition_order_is_enforced(app: CortexApp):
    config = app.config()
    market = make_market(config)
    ledger = make_ledger()
    for event in ledger.events:
        app.store.append_event(event)
    decision = decide(config=config, market=market, portfolio=make_portfolio(ledger, config, market.price))
    app.store.save_decision(decision)
    app.approve(decision.decision_id, operator="operator")
    order_id = decision.proposed_order.order_id  # type: ignore[union-attr]
    with pytest.raises(AppError):
        app.transition(order_id, "work", {})  # cannot work before submit
