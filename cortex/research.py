"""Runs the veto study over public Binance history and caches the report."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .backtest import build_report
from .market import SPOT_BASE, Kline, _get, parse_klines

SYMBOL = "BTCUSDT"


def fetch_klines(interval: str, total: int, *, symbol: str = SYMBOL) -> list[Kline]:
    """Page backwards until ``total`` bars are collected."""
    collected: list[Kline] = []
    end_time: int | None = None
    while len(collected) < total:
        limit = min(1000, total - len(collected))
        url = f"{SPOT_BASE}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        if end_time is not None:
            url += f"&endTime={end_time}"
        raw = _get(url, timeout=20)
        if not raw:
            break
        page = parse_klines(raw)
        collected = page + collected
        end_time = int(raw[0][0]) - 1
    # de-duplicate while preserving order
    seen: set[datetime] = set()
    unique: list[Kline] = []
    for bar in collected:
        if bar.open_time in seen:
            continue
        seen.add(bar.open_time)
        unique.append(bar)
    unique.sort(key=lambda bar: bar.open_time)
    return unique


def run_veto_study(*, daily_bars: int = 800, four_hour_bars: int = 5000) -> dict[str, Any]:
    daily = fetch_klines("1d", daily_bars)
    four_h = fetch_klines("4h", four_hour_bars)
    # Drop the forming bar so every input is a closed candle.
    now = datetime.now(tz=daily[-1].open_time.tzinfo) if daily else None
    daily_closed = [bar for bar in daily if now is None or bar.close_time <= now]
    four_h_closed = [bar for bar in four_h if now is None or bar.close_time <= now]
    report = build_report(daily_closed, four_h_closed)
    report["source"] = "binance public klines"
    return report


def save_report(report: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return target


__all__ = ["fetch_klines", "run_veto_study", "save_report"]
