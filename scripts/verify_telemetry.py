"""Cross-check the attached telemetry snapshot against Binance public data."""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal


def get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "check/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def ms(v: int) -> str:
    return datetime.fromtimestamp(v / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


raw = get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=260")
bars = [
    {
        "open_time": ms(int(r[0])),
        "open": Decimal(r[1]),
        "high": Decimal(r[2]),
        "low": Decimal(r[3]),
        "close": Decimal(r[4]),
        "volume": Decimal(r[5]),
    }
    for r in raw
]
# exclude the forming candle
closed = bars[:-1]
last = closed[-1]
closes = [b["close"] for b in closed]

sma200 = sum(closes[-200:]) / 200
sma50 = sum(closes[-50:]) / 50


def rsi(series, period=14):
    gains, losses = [], []
    for i in range(1, len(series)):
        d = series[i] - series[i - 1]
        gains.append(max(d, Decimal(0)))
        losses.append(max(-d, Decimal(0)))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return Decimal(100)
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(bars_, period=14):
    trs = []
    for i in range(1, len(bars_)):
        h, l, pc = bars_[i]["high"], bars_[i]["low"], bars_[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period


print(f"last closed 1D bar     : {last['open_time']}  close={last['close']:.2f}")
print(f"expected (attachment)  : 2026-11-16 close=64882.87")
print(f"24h range              : {last['low']:.2f} - {last['high']:.2f}   expected 63905.01 - 65696.16")
print(f"SMA200 (closed 1D)     : {sma200:.2f}   expected 72921.15")
print(f"SMA50  (closed 1D)     : {sma50:.2f}   expected 79451.99")
print(f"RSI14  (closed 1D)     : {rsi(closes):.1f}   expected 23.4")
print(f"ATR14  (closed 1D)     : {atr(closed):.2f}   expected 2628.90")
dist = (last["close"] - sma200) / sma200 * 100
print(f"distance vs SMA200     : {dist:.2f}%   expected -11.02%")
