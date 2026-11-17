"""Manual smoke run: synthetic snapshot through the full decision chain."""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cortex.authority import decide  # noqa: E402
from cortex.config import Config  # noqa: E402
from cortex.domain import (  # noqa: E402
    DataStatus,
    Feature,
    build_market_snapshot,
    build_portfolio_snapshot,
    utc_now,
)
from cortex.ledger import Ledger, available_btc, available_usdt  # noqa: E402


def feature(value: str, status: DataStatus = DataStatus.VERIFIED, detail: str = "") -> Feature:
    return Feature(Decimal(value), status, "synthetic", utc_now(), detail)


def main() -> None:
    config = Config.from_dict(json.loads((ROOT / "config" / "research-seed.json").read_text()))
    now = utc_now()
    price = Decimal("64800")
    features = {
        "price": feature("64800"),
        "support": feature("64600"),
        "resistance": feature("65600"),
        "vah": feature("64700"),
        "val": feature("63900"),
        "poc": feature("64300"),
        "sweep_low": feature("64420"),
        "spot_delta_15m": feature("18"),
        "spot_delta_prev_15m": feature("-40"),
        "spot_cvd_slope_15m": feature("0.12"),
        "price_change_15m_pct": feature("0.35"),
        "demand_absorption": feature("0.62", DataStatus.PROXY, "derived proxy"),
        "supply_absorption": feature("0.31", DataStatus.PROXY, "derived proxy"),
        "book_imbalance": feature("0.18"),
        "sma": feature("63200"),
        "liquidations": Feature(None, DataStatus.UNAVAILABLE, "none", now, "no verified feed"),
        "average_cost_basis": feature("64000"),
    }
    market = build_market_snapshot(
        as_of=now,
        instrument="BTC/USDT",
        price=price,
        features=features,
        config_version=config.config_version,
        source="synthetic",
    )
    ledger = Ledger()
    ledger.seed(
        btc_core=Decimal("0.15"),
        btc_tactical=Decimal("0.05"),
        usdt_core=Decimal("0"),
        usdt_tactical=Decimal("5000"),
    )
    state = ledger.state()
    portfolio = build_portfolio_snapshot(
        as_of=now,
        price=price,
        ledger=state,
        available_btc=available_btc(state, config),
        available_usdt=available_usdt(state, config),
        config_version=config.config_version,
    )
    decision = decide(config=config, market=market, portfolio=portfolio, previous_state=None)
    print("verdict:", decision.verdict)
    print("state:", decision.portfolio_state)
    print("explanation:", decision.explanation)
    print("plain:", decision.plain_english)
    print("module A:", decision.bullish.state, "| B:", decision.bearish.state)
    for gate in decision.gates:
        print(f"  G{gate.gate:<2} {gate.status.value:<11} {gate.name}: {gate.detail}")
    print("allocation:", decision.allocation.status, decision.allocation.binding_constraint,
          decision.allocation.quantity)
    for constraint in decision.allocation.constraints:
        print("   -", constraint.name, constraint.quantity, "BINDING" if constraint.binding else "")
    print("order:", None if decision.proposed_order is None else decision.proposed_order.to_dict())
    print("replay key:", decision.replay_key[:16])


if __name__ == "__main__":
    main()
