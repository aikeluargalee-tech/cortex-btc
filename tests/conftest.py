"""Shared fixtures: synthetic snapshots with every feature explicitly stated."""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cortex.config import Config  # noqa: E402
from cortex.domain import (  # noqa: E402
    DataStatus,
    Feature,
    MarketSnapshot,
    PortfolioSnapshot,
    build_market_snapshot,
    build_portfolio_snapshot,
    utc_now,
)
from cortex.ledger import Ledger, available_btc, available_usdt  # noqa: E402

SEED = ROOT / "config" / "research-seed.json"


@pytest.fixture()
def config() -> Config:
    return Config.from_dict(json.loads(SEED.read_text(encoding="utf-8")))


def feature(value: str | None, status: DataStatus = DataStatus.VERIFIED, detail: str = "") -> Feature:
    return Feature(
        None if value is None else Decimal(value),
        status,
        "test",
        utc_now(),
        detail,
    )


DEFAULT_FEATURES = {
    "price": ("64800", DataStatus.VERIFIED),
    "support": ("64600", DataStatus.VERIFIED),
    "resistance": ("65600", DataStatus.VERIFIED),
    "vah": ("64700", DataStatus.VERIFIED),
    "val": ("63900", DataStatus.VERIFIED),
    "poc": ("64300", DataStatus.VERIFIED),
    "sweep_low": ("64420", DataStatus.VERIFIED),
    "recent_high": ("64800", DataStatus.VERIFIED),
    "spot_delta_15m": ("18", DataStatus.VERIFIED),
    "spot_delta_prev_15m": ("-40", DataStatus.VERIFIED),
    "spot_cvd_slope_15m": ("0.12", DataStatus.VERIFIED),
    "price_change_15m_pct": ("0.35", DataStatus.VERIFIED),
    "demand_absorption": ("0.62", DataStatus.PROXY),
    "supply_absorption": ("0.31", DataStatus.PROXY),
    "book_imbalance": ("0.18", DataStatus.VERIFIED),
    "sma": ("63200", DataStatus.VERIFIED),
    "liquidations": (None, DataStatus.UNAVAILABLE),
    "average_cost_basis": ("64000", DataStatus.VERIFIED),
}


def make_market(config: Config, **overrides: tuple[str | None, DataStatus] | str | None) -> MarketSnapshot:
    features = {}
    for key, spec in DEFAULT_FEATURES.items():
        value, status = spec
        features[key] = feature(value, status)
    for key, raw in overrides.items():
        if isinstance(raw, tuple):
            value, status = raw
            features[key] = feature(value, status)
        elif raw is None:
            features[key] = feature(None, DataStatus.UNAVAILABLE, "omitted by test")
        else:
            features[key] = feature(raw)
    price = features["price"].value
    return build_market_snapshot(
        as_of=utc_now(),
        instrument="BTC/USDT",
        price=price,
        features=features,
        config_version=config.config_version,
        source="test",
    )


def make_ledger(
    *,
    btc_core: str = "0.15",
    btc_tactical: str = "0.05",
    usdt_core: str = "0",
    usdt_tactical: str = "5000",
) -> Ledger:
    ledger = Ledger()
    ledger.seed(
        btc_core=Decimal(btc_core),
        btc_tactical=Decimal(btc_tactical),
        usdt_core=Decimal(usdt_core),
        usdt_tactical=Decimal(usdt_tactical),
    )
    return ledger


def make_portfolio(ledger: Ledger, config: Config, price: Decimal | None = None) -> PortfolioSnapshot:
    state = ledger.state()
    if price is None:
        price = Decimal("64800")
    return build_portfolio_snapshot(
        as_of=utc_now(),
        price=price,
        ledger=state,
        available_btc=available_btc(state, config),
        available_usdt=available_usdt(state, config),
        config_version=config.config_version,
    )
