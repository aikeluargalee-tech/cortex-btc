"""Ledger invariants: reservations, partial fills, core protection, floors."""

from __future__ import annotations

from decimal import Decimal

import pytest
from conftest import make_ledger

from cortex.config import Config
from cortex.ledger import Ledger, LedgerError, available_btc, available_usdt, integrity_check


def test_seed_sets_balances():
    ledger = make_ledger()
    state = ledger.state()
    assert state.btc_total == Decimal("0.20")
    assert state.usdt_total == Decimal("5000")
    assert state.btc_core == Decimal("0.15")


def test_seed_cannot_be_negative():
    ledger = Ledger()
    with pytest.raises(LedgerError):
        ledger.seed(
            btc_core=Decimal("-1"),
            btc_tactical=Decimal("0"),
            usdt_core=Decimal("0"),
            usdt_tactical=Decimal("0"),
        )


def test_second_reservation_of_same_btc_is_rejected():
    ledger = make_ledger(btc_tactical="0.10")
    ledger.reserve(order_id="o1", asset="BTC", quantity=Decimal("0.06"))
    with pytest.raises(LedgerError):
        ledger.reserve(order_id="o2", asset="BTC", quantity=Decimal("0.06"))


def test_second_reservation_of_same_usdt_is_rejected():
    ledger = make_ledger(usdt_tactical="1000")
    ledger.reserve(order_id="o1", asset="USDT", quantity=Decimal("600"))
    with pytest.raises(LedgerError):
        ledger.reserve(order_id="o2", asset="USDT", quantity=Decimal("600"))


def test_duplicate_order_reservation_is_rejected():
    ledger = make_ledger()
    ledger.reserve(order_id="o1", asset="USDT", quantity=Decimal("100"))
    with pytest.raises(LedgerError):
        ledger.reserve(order_id="o1", asset="USDT", quantity=Decimal("100"))


def test_partial_fill_moves_only_executed_quantity():
    ledger = make_ledger(btc_tactical="0.10")
    ledger.reserve(order_id="sell1", asset="BTC", quantity=Decimal("0.10"))
    ledger.fill(order_id="sell1", side="SELL", quantity=Decimal("0.04"), price=Decimal("65000"), fee_usdt=Decimal("13"))
    state = ledger.state()
    assert state.btc_tactical == Decimal("0.06")
    assert state.reserved("BTC") == Decimal("0.06")
    assert state.usdt_tactical == Decimal("5000") + Decimal("0.04") * Decimal("65000") - Decimal("13")


def test_fill_above_reservation_is_rejected():
    ledger = make_ledger(btc_tactical="0.10")
    ledger.reserve(order_id="sell1", asset="BTC", quantity=Decimal("0.05"))
    with pytest.raises(LedgerError):
        ledger.fill(order_id="sell1", side="SELL", quantity=Decimal("0.06"), price=Decimal("65000"), fee_usdt=Decimal("0"))


def test_cancellation_releases_only_when_recorded():
    ledger = make_ledger(btc_tactical="0.10")
    ledger.reserve(order_id="sell1", asset="BTC", quantity=Decimal("0.05"))
    assert ledger.state().reserved("BTC") == Decimal("0.05")
    ledger.release(order_id="sell1", note="exchange confirmed")
    assert ledger.state().reserved("BTC") == Decimal("0")


def test_release_without_reservation_is_rejected():
    ledger = make_ledger()
    with pytest.raises(LedgerError):
        ledger.release(order_id="nope")


def test_sell_cannot_drive_btc_negative():
    ledger = make_ledger(btc_tactical="0.01")
    ledger.reserve(order_id="s", asset="BTC", quantity=Decimal("0.01"))
    ledger.fill(order_id="s", side="SELL", quantity=Decimal("0.01"), price=Decimal("64000"), fee_usdt=Decimal("0"))
    assert ledger.state().btc_tactical == Decimal("0")
    with pytest.raises(LedgerError):
        ledger.reserve(order_id="s2", asset="BTC", quantity=Decimal("0.01"))


def test_available_btc_respects_core_floor_and_reservations(config: Config):
    ledger = make_ledger(btc_core="0.15", btc_tactical="0.05")
    state = ledger.state()
    assert available_btc(state, config) == Decimal("0.05")
    ledger.reserve(order_id="r", asset="BTC", quantity=Decimal("0.02"))
    assert available_btc(ledger.state(), config) == Decimal("0.03")


def test_available_btc_zero_when_floor_not_met(config: Config):
    ledger = make_ledger(btc_core="0.10", btc_tactical="0.02")
    # floor is 0.15; total is 0.12 -> every tactical unit counts toward the shortfall
    assert available_btc(ledger.state(), config) == Decimal("0")


def test_available_usdt_respects_floor(config: Config):
    ledger = make_ledger(usdt_tactical="5000")
    assert available_usdt(ledger.state(), config) == Decimal("4000")
    ledger.reserve(order_id="b", asset="USDT", quantity=Decimal("1500"))
    assert available_usdt(ledger.state(), config) == Decimal("2500")


def test_cost_basis_and_realised_result():
    ledger = make_ledger(btc_tactical="0", usdt_tactical="10000")
    ledger.reserve(order_id="b1", asset="USDT", quantity=Decimal("6400"))
    ledger.fill(order_id="b1", side="BUY", quantity=Decimal("0.1"), price=Decimal("64000"), fee_usdt=Decimal("0"))
    assert ledger.average_cost() == Decimal("64000")
    ledger.reserve(order_id="s1", asset="BTC", quantity=Decimal("0.1"))
    ledger.fill(order_id="s1", side="SELL", quantity=Decimal("0.1"), price=Decimal("65000"), fee_usdt=Decimal("0"))
    state = ledger.state()
    assert state.realised_usdt == Decimal("100")
    assert state.btc_tactical == Decimal("0")


def test_integrity_check_clean():
    ok, problems = integrity_check(make_ledger())
    assert ok and not problems
