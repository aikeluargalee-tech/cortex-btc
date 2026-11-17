"""External telemetry parsing and the veto study's pure functions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from cortex.backtest import build_report, swing_structure, veto_series
from cortex.market import Kline
from cortex.telemetry import freshness_warning, parse_telemetry

SAMPLE = """
**Retrieval Time (UTC):** `2026-11-17 02:24:06 UTC`
**Spot Price (BTCUSDT):** `$64,629.18`

## 0. HARD DOWNTREND MARKET VETO
| Gate Field | Value | Rule |
|---|---|---|
| Latest Closed 1D Candle Close | `$64,882.87` | `closed only` |
| Current 1D SMA 200 | `$72,921.15` | `200-bar SMA` |

**HARD DOWNTREND MARKET VETO STATUS:**
- **Veto Status:** `ACTIVE`
- **Condition A (Closed 1D Close < 1D SMA 200):** `YES`
- **Condition B (4H Downside Structure / BOS Down):** `YES`
- **Portfolio Status:** `100% USDT CASH`
- **New BTC Entry:** `PROHIBITED`

## 1. PRICE
| Metric | Value | Source | Status |
|---|---|---|---|
| Spot Price | `$64,629.18` | `All Tabs` | `OK` |
| Liquidations | `DATA UNAVAILABLE` | `0,8` | `UNAVAILABLE` |
"""


def test_parses_tables_and_bullets():
    report = parse_telemetry(SAMPLE, source="sample.md")
    assert report.spot_price == 64629.18
    labels = {row.label for row in report.rows}
    assert "Current 1D SMA 200" in labels
    assert "Veto Status" in labels


def test_gate_zero_is_read_verbatim():
    report = parse_telemetry(SAMPLE)
    gate = report.gate_zero()
    assert gate["veto_status"] == "ACTIVE"
    assert gate["condition_a"] == "YES"
    assert gate["new_entry"] == "PROHIBITED"


def test_status_column_is_preserved():
    report = parse_telemetry(SAMPLE)
    row = next(r for r in report.rows if r.label == "Liquidations")
    assert row.status == "UNAVAILABLE"


def test_freshness_warning_flags_old_snapshots():
    report = parse_telemetry(SAMPLE)
    report = type(report)(
        source=report.source,
        as_of="2026-11-17 02:24:06 UTC",
        spot_price=report.spot_price,
        coverage=report.coverage,
        rows=report.rows,
        lookup=report.lookup,
        warnings=report.warnings,
    )
    stale = freshness_warning(report, now=datetime(2026, 11, 17, 6, 0, tzinfo=timezone.utc), max_age_minutes=90)
    assert stale is not None and "old" in stale
    fresh = freshness_warning(report, now=datetime(2026, 11, 17, 2, 40, tzinfo=timezone.utc), max_age_minutes=90)
    assert fresh is None


def _bar(open_time: datetime, close: Decimal, high: Decimal | None = None, low: Decimal | None = None) -> Kline:
    return Kline(
        open_time=open_time,
        close_time=open_time + timedelta(hours=1),
        open=close,
        high=high or close * Decimal("1.005"),
        low=low or close * Decimal("0.995"),
        close=close,
        volume=Decimal("10"),
        taker_buy_base=Decimal("5"),
        trades=100,
    )


def _trending_series(
    start: datetime,
    count: int,
    *,
    step: timedelta,
    base: float = 100.0,
    drift: float = -0.2,
    amplitude: float = 2.0,
) -> list[Kline]:
    """A downtrend that still oscillates, so fractal pivots exist."""
    import math

    bars: list[Kline] = []
    for index in range(count):
        close = Decimal(str(round(base + drift * index + amplitude * math.sin(index / 3.0), 4)))
        high = close + Decimal("0.4")
        low = close - Decimal("0.4")
        bars.append(_bar(start + step * index, close, high=high, low=low))
    return bars


def test_downtrend_structure_is_detected():
    bars = _trending_series(datetime(2026, 1, 1, tzinfo=timezone.utc), 120, step=timedelta(hours=4))
    structure, _bos = swing_structure(bars)
    assert structure == "DOWNTREND"


def test_veto_requires_price_below_sma():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    daily = _trending_series(start, 240, step=timedelta(days=1), drift=-0.3, amplitude=1.5)
    four_h = _trending_series(start, 400, step=timedelta(hours=4), base=90.0, drift=-0.02, amplitude=0.8)
    series = veto_series(daily, four_h)
    assert series[-1].sma200 is not None
    assert series[-1].close < series[-1].sma200
    assert series[-1].veto is True


def test_report_has_no_lookahead_in_position_timing():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    daily = [_bar(start + timedelta(days=i), Decimal("100") + Decimal(i % 7)) for i in range(260)]
    four_h = [_bar(start + timedelta(hours=4 * i), Decimal("100")) for i in range(400)]
    report = build_report(daily, four_h)
    assert report["ok"] is True
    assert report["strategy"]["always_in"]["max_drawdown_pct"] <= 0
    assert len(report["forward_returns"]) == 8
