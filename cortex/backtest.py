"""Backtest: does the 200D / 4H "hard downtrend veto" carry information?

This is a study of the external rule, not of our own stance. It answers three
questions with one pass over daily and 4H bars:

1. How often is the veto active?
2. What did price do after veto-active days versus veto-inactive days?
3. What would a veto-filtered hold have done versus an unfiltered hold?

Everything is computed from closed bars only, and the position implied by a
daily signal is applied from the *next* day, so there is no look-ahead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from statistics import mean, median
from typing import Any, Iterable, Sequence

from .market import Kline

HORIZONS = (1, 5, 10, 20)


@dataclass(frozen=True, slots=True)
class DayVeto:
    day: datetime
    close: Decimal
    sma200: Decimal | None
    structure: str
    bos_down: bool
    veto: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.strftime("%Y-%m-%d"),
            "close": float(self.close),
            "sma200": None if self.sma200 is None else float(self.sma200),
            "structure": self.structure,
            "bos_down": self.bos_down,
            "veto": self.veto,
        }


def _sma(values: Sequence[Decimal], period: int, index: int) -> Decimal | None:
    if index + 1 < period:
        return None
    window = values[index + 1 - period : index + 1]
    return sum(window) / period


def swing_structure(bars: Sequence[Kline], *, k: int = 3, lookback: int = 200) -> tuple[str, bool]:
    """Approximation of a 5x5-pivot HH/HL/LH/LL scanner plus a downside BOS flag.

    Deliberately simpler than the external scanner: fractal pivots with k bars on
    each side, last two pivot highs and lows compared, and a break of the most
    recent pivot low counted as a downside break of structure.
    """
    window = list(bars[-lookback:])
    if len(window) < 2 * k + 6:
        return "UNKNOWN", False

    pivots_high: list[int] = []
    pivots_low: list[int] = []
    for i in range(k, len(window) - k):
        segment = window[i - k : i + k + 1]
        if window[i].high == max(b.high for b in segment):
            pivots_high.append(i)
        if window[i].low == min(b.low for b in segment):
            pivots_low.append(i)

    structure = "MIXED"
    if len(pivots_high) >= 2 and len(pivots_low) >= 2:
        h1, h2 = window[pivots_high[-2]].high, window[pivots_high[-1]].high
        l1, l2 = window[pivots_low[-2]].low, window[pivots_low[-1]].low
        if h2 > h1 and l2 > l1:
            structure = "UPTREND"
        elif h2 < h1 and l2 < l1:
            structure = "DOWNTREND"

    bos_down = False
    if pivots_low:
        last_low_index = pivots_low[-1]
        if last_low_index < len(window) - 1:
            bos_down = window[-1].close < window[last_low_index].low

    return structure, bos_down


def veto_series(daily: Sequence[Kline], four_h: Sequence[Kline], *, period: int = 200) -> list[DayVeto]:
    closes = [bar.close for bar in daily]
    series: list[DayVeto] = []
    four_h_index = 0

    for i, bar in enumerate(daily):
        sma = _sma(closes, period, i)
        # Bars available as of this daily close.
        while four_h_index < len(four_h) and four_h[four_h_index].close_time <= bar.close_time:
            four_h_index += 1
        available = four_h[:four_h_index]
        structure, bos_down = swing_structure(available)
        veto = sma is not None and bar.close < sma and (structure == "DOWNTREND" or bos_down)
        series.append(
            DayVeto(
                day=bar.open_time,
                close=bar.close,
                sma200=sma,
                structure=structure,
                bos_down=bos_down,
                veto=bool(veto),
            )
        )
    return series


def _forward_return(closes: Sequence[Decimal], index: int, horizon: int) -> float | None:
    target = index + horizon
    if target >= len(closes):
        return None
    start = float(closes[index])
    if start <= 0:
        return None
    return (float(closes[target]) / start - 1.0) * 100.0


def _describe(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean_pct": None, "median_pct": None, "positive_pct": None, "worst_pct": None}
    return {
        "n": len(values),
        "mean_pct": round(mean(values), 3),
        "median_pct": round(median(values), 3),
        "positive_pct": round(100.0 * sum(1 for v in values if v > 0) / len(values), 1),
        "worst_pct": round(min(values), 3),
    }


def _equity_curve(closes: Sequence[Decimal], blocked: Sequence[bool]) -> list[float]:
    equity = [1.0]
    for i in range(1, len(closes)):
        held = not blocked[i - 1]  # signal at previous close, applied next day
        step = float(closes[i]) / float(closes[i - 1]) if held else 1.0
        equity.append(equity[-1] * step)
    return equity


def _drawdown(equity: Sequence[float]) -> float:
    peak = equity[0]
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1.0)
    return worst * 100.0


def _summary(equity: Sequence[float], days: int) -> dict[str, Any]:
    total = (equity[-1] - 1.0) * 100.0
    years = max(days / 365.0, 1e-9)
    cagr = ((equity[-1]) ** (1.0 / years) - 1.0) * 100.0 if equity[-1] > 0 else None
    return {
        "total_return_pct": round(total, 2),
        "cagr_pct": None if cagr is None else round(cagr, 2),
        "max_drawdown_pct": round(_drawdown(equity), 2),
        "final_equity": round(equity[-1], 4),
    }


def build_report(daily: Sequence[Kline], four_h: Sequence[Kline], *, period: int = 200) -> dict[str, Any]:
    series = veto_series(daily, four_h, period=period)
    pairs = [(index, row) for index, row in enumerate(series) if row.sma200 is not None]
    if not pairs:
        return {"ok": False, "reason": "not enough daily history for the 200-period average"}

    closes = [bar.close for bar in daily]
    active = [(i, r) for i, r in pairs if r.veto]
    inactive = [(i, r) for i, r in pairs if not r.veto]
    usable = len(pairs)

    forward: list[dict[str, Any]] = []
    for label, group in (("VETO ACTIVE", active), ("VETO INACTIVE", inactive)):
        for horizon in HORIZONS:
            values = []
            for index, _row in group:
                value = _forward_return(closes, index, horizon)
                if value is not None:
                    values.append(value)
            forward.append({"state": label, "horizon_days": horizon, **_describe(values)})

    blocked = [row.veto for row in series]
    full_equity = _equity_curve(closes, [False] * len(closes))
    veto_equity = _equity_curve(closes, blocked)

    days = (daily[-1].open_time - daily[0].open_time).days
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "window": {
            "from": daily[0].open_time.strftime("%Y-%m-%d"),
            "to": daily[-1].open_time.strftime("%Y-%m-%d"),
            "days": days,
            "daily_bars": len(daily),
            "four_hour_bars": len(four_h),
            "evaluated_days": usable,
        },
        "veto": {
            "active_days": len(active),
            "inactive_days": len(inactive),
            "active_share_pct": round(100.0 * len(active) / usable, 1),
            "current": series[-1].to_dict(),
        },
        "forward_returns": forward,
        "strategy": {
            "always_in": _summary(full_equity, days),
            "veto_filtered": _summary(veto_equity, days),
        },
        "assumptions": [
            "daily close-to-close returns, no fees, no slippage, no funding",
            "signal computed on a closed daily bar, position applied the next day",
            "4H structure approximated with fractal pivots, not the external 5x5 scanner",
            "in-sample over the fetched window; not out-of-sample validation",
        ],
    }


__all__ = ["DayVeto", "build_report", "swing_structure", "veto_series"]
