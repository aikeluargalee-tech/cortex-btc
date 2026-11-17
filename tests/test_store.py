"""Store, configuration governance and decision idempotency."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from conftest import SEED, make_market

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


def test_config_change_requires_operator_and_reason(app: CortexApp):
    with pytest.raises(AppError):
        app.change_config({"buy.absorption_min": "0.6"}, operator="  ", note="x")
    with pytest.raises(AppError):
        app.change_config({"buy.absorption_min": "0.6"}, operator="operator", note="  ")


def test_config_hash_changes_with_parameters(app: CortexApp):
    before = app.config().config_hash
    after = app.change_config({"buy.absorption_min": "0.61"}, operator="operator", note="tweak").config_hash
    assert before != after


def test_decision_recording_is_idempotent(app: CortexApp):
    config = app.config()
    market = make_market(config)
    decision = decide(config=config, market=market)
    assert app.store.save_decision(decision) is True
    assert app.store.save_decision(decision) is False
    assert len(app.store.decisions()) == 1


def test_decisions_are_exportable(app: CortexApp):
    config = app.config()
    app.store.save_decision(decide(config=config, market=make_market(config)))
    exported = app.export_decisions()
    assert len(exported) == 1
    assert exported[0]["verdict"] in {
        "QUALIFIED BUY ZONE",
        "QUALIFIED SELL ZONE",
        "NO SETUP",
        "CONFLICT",
        "DATA HALT",
    }
