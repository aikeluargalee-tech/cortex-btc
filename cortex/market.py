"""Public market data ingestion and deterministic feature derivation.

No API keys, no private endpoints, no order placement. Every feature carries a
provenance string and a data status; anything that cannot be observed is
reported UNAVAILABLE rather than estimated.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Sequence

from .domain import DataStatus, Feature, MarketSnapshot, build_market_snapshot, utc_now

SPOT_BASE = "https://api.binance.com"
FUTURES_BASE = "https://fapi.binance.com"
USER_AGENT = "CORTEX-BTC/0.1 (decision support, read-only)"


class MarketDataError(RuntimeError):
    pass


def _get(url: str, timeout: float = 10.0) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


@dataclass(frozen=True, slots=True)
class Kline:
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    taker_buy_base: Decimal
    trades: int


def parse_klines(raw: Sequence[Sequence[Any]]) -> list[Kline]:
    out: list[Kline] = []
    for row in raw:
        out.append(
            Kline(
                open_time=_ms(int(row[0])),
                close_time=_ms(int(row[6])),
                open=_dec(row[1]),
                high=_dec(row[2]),
                low=_dec(row[3]),
                close=_dec(row[4]),
                volume=_dec(row[5]),
                taker_buy_base=_dec(row[9]),
                trades=int(row[8]),
            )
        )
    return out


class BinancePublic:
    def __init__(self, symbol: str = "BTCUSDT", timeout: float = 10.0) -> None:
        self.symbol = symbol
        self.timeout = timeout

    def price(self) -> tuple[Decimal, datetime]:
        data = _get(f"{SPOT_BASE}/api/v3/ticker/price?symbol={self.symbol}", self.timeout)
        return _dec(data["price"]), utc_now()

    def klines(self, interval: str, limit: int = 200) -> list[Kline]:
        raw = _get(
            f"{SPOT_BASE}/api/v3/klines?symbol={self.symbol}&interval={interval}&limit={limit}",
            self.timeout,
        )
        return parse_klines(raw)

    def depth(self) -> dict[str, Any]:
        return _get(f"{SPOT_BASE}/api/v3/depth?symbol={self.symbol}&limit=100", self.timeout)

    def open_interest(self) -> tuple[Decimal, datetime]:
        data = _get(f"{FUTURES_BASE}/fapi/v1/openInterest?symbol={self.symbol}", self.timeout)
        return _dec(data["openInterest"]), _ms(int(data["time"]))

    def funding(self) -> tuple[Decimal, datetime]:
        data = _get(f"{FUTURES_BASE}/fapi/v1/premiumIndex?symbol={self.symbol}", self.timeout)
        return _dec(data["lastFundingRate"]), _ms(int(data["time"]))


def _value_area(klines: Iterable[Kline], buckets: int = 120) -> tuple[Decimal, Decimal, Decimal]:
    """Deterministic volume profile: POC plus the 70% value area around it."""
    bars = list(klines)
    if not bars:
        raise ValueError("no bars")
    low = min(bar.low for bar in bars)
    high = max(bar.high for bar in bars)
    if high <= low:
        return high, high, high
    width = (high - low) / buckets
    profile = [Decimal("0")] * buckets
    for bar in bars:
        typical = (bar.high + bar.low + bar.close) / Decimal("3")
        index = int((typical - low) / width)
        index = max(0, min(buckets - 1, index))
        profile[index] += bar.volume
    poc_index = max(range(buckets), key=lambda i: profile[i])
    total = sum(profile)
    target = total * Decimal("0.70")
    lower = upper = poc_index
    accumulated = profile[poc_index]
    while accumulated < target and (lower > 0 or upper < buckets - 1):
        down = profile[lower - 1] if lower > 0 else Decimal("-1")
        up = profile[upper + 1] if upper < buckets - 1 else Decimal("-1")
        if up >= down:
            upper += 1
            accumulated += profile[upper]
        else:
            lower -= 1
            accumulated += profile[lower]
    poc = low + width * (Decimal(poc_index) + Decimal("0.5"))
    val = low + width * Decimal(lower)
    vah = low + width * Decimal(upper + 1)
    return poc, val, vah


def _absorption(bars: Sequence[Kline]) -> tuple[Decimal | None, Decimal | None]:
    """Proxy for absorption, derived (not observed).

    Demand absorption: heavy sell-side volume that closes near the bar high.
    Supply absorption: heavy buy-side volume that closes near the bar low.
    """
    if not bars:
        return None, None
    demand = Decimal("0")
    supply = Decimal("0")
    demand_weight = Decimal("0")
    supply_weight = Decimal("0")
    for bar in bars:
        span = bar.high - bar.low
        if span <= 0:
            continue
        sell_volume = bar.volume - bar.taker_buy_base
        buy_volume = bar.taker_buy_base
        if bar.volume > 0:
            demand += (sell_volume / bar.volume) * ((bar.close - bar.low) / span)
            demand_weight += sell_volume / bar.volume
            supply += (buy_volume / bar.volume) * ((bar.high - bar.close) / span)
            supply_weight += buy_volume / bar.volume
    demand_ratio = demand / demand_weight if demand_weight > 0 else None
    supply_ratio = supply / supply_weight if supply_weight > 0 else None
    return demand_ratio, supply_ratio


def _book_imbalance(depth: dict[str, Any], band: Decimal = Decimal("0.005")) -> tuple[Decimal | None, Decimal]:
    bids = depth.get("bids") or []
    asks = depth.get("asks") or []
    if not bids or not asks:
        return None, Decimal("0")
    best_bid = _dec(bids[0][0])
    best_ask = _dec(asks[0][0])
    mid = (best_bid + best_ask) / Decimal("2")
    lower = mid * (Decimal("1") - band)
    upper = mid * (Decimal("1") + band)
    bid_volume = sum((_dec(price) * _dec(qty) for price, qty in bids if _dec(price) >= lower), Decimal("0"))
    ask_volume = sum((_dec(price) * _dec(qty) for price, qty in asks if _dec(price) <= upper), Decimal("0"))
    if bid_volume + ask_volume <= 0:
        return None, mid
    return (bid_volume - ask_volume) / (bid_volume + ask_volume), mid


def build_snapshot(
    *,
    config,
    average_cost: Decimal | None = None,
    max_age_seconds: int = 60,
) -> MarketSnapshot:
    """Fetch public data and derive features. Failures degrade, never invent."""
    client = BinancePublic()
    features: dict[str, Feature] = {}
    observed: list[datetime] = []

    def unavailable(key: str, detail: str) -> None:
        features[key] = Feature(value=None, status=DataStatus.UNAVAILABLE, source="binance-public", detail=detail)

    price: Decimal | None = None
    price_time: datetime | None = None
    try:
        price, price_time = client.price()
        features["price"] = Feature(price, DataStatus.VERIFIED, "binance spot ticker", price_time, "top of book mid")
        observed.append(price_time)
    except Exception as error:  # noqa: BLE001 - network failure must degrade
        unavailable("price", f"ticker unavailable: {error}")
        features["spot_price"] = features["price"]

    flow_bars: list[Kline] = []
    try:
        flow_bars = client.klines("15m", 96)
        closed = [bar for bar in flow_bars if bar.close_time <= utc_now()]
        if len(closed) < 20:
            raise MarketDataError("insufficient closed 15m bars")
        recent = closed[-8:]
        last = closed[-1]
        older = closed[-2]
        delta = last.taker_buy_base * Decimal("2") - last.volume
        delta_prev = older.taker_buy_base * Decimal("2") - older.volume
        window_volume = sum((bar.volume for bar in recent), Decimal("0"))
        window_delta = sum((bar.taker_buy_base * Decimal("2") - bar.volume for bar in recent), Decimal("0"))
        slope = window_delta / window_volume if window_volume > 0 else Decimal("0")
        change = ((last.close - older.close) / older.close) * Decimal("100") if older.close > 0 else Decimal("0")
        features["spot_delta_15m"] = Feature(delta, DataStatus.VERIFIED, "binance 15m taker split", last.close_time, "aggressor delta of last closed bar")
        features["spot_delta_prev_15m"] = Feature(delta_prev, DataStatus.VERIFIED, "binance 15m taker split", older.close_time, "aggressor delta of prior bar")
        features["spot_cvd_slope_15m"] = Feature(slope, DataStatus.VERIFIED, "derived: binance 15m taker split", last.close_time, "8-bar normalised cumulative delta")
        features["price_change_15m_pct"] = Feature(change, DataStatus.VERIFIED, "derived: binance 15m klines", last.close_time, "close-over-close change")
        demand, supply = _absorption(recent)
        if demand is not None:
            features["demand_absorption"] = Feature(demand, DataStatus.PROXY, "derived proxy: sell volume vs close location", last.close_time, "not an observed order-flow measurement")
        if supply is not None:
            features["supply_absorption"] = Feature(supply, DataStatus.PROXY, "derived proxy: buy volume vs close location", last.close_time, "not an observed order-flow measurement")
        features["sweep_low"] = Feature(min(bar.low for bar in recent[-4:]), DataStatus.VERIFIED, "derived: binance 15m klines", last.close_time, "lowest low of last four bars")
        features["recent_high"] = Feature(max(bar.high for bar in recent[-4:]), DataStatus.VERIFIED, "derived: binance 15m klines", last.close_time, "highest high of last four bars")
        observed.append(last.close_time)
    except Exception as error:  # noqa: BLE001
        for key in ("spot_delta_15m", "spot_cvd_slope_15m", "price_change_15m_pct", "demand_absorption", "supply_absorption", "sweep_low"):
            unavailable(key, f"15m bars unavailable: {error}")

    try:
        structure = client.klines("1h", 168)
        closed = [bar for bar in structure if bar.close_time <= utc_now()]
        if len(closed) < 30:
            raise MarketDataError("insufficient closed 1h bars")
        hours = max(2, int(config.analysis.value_area_hours))
        context_hours = max(hours, int(config.analysis.context_profile_hours))
        poc, val, vah = _value_area(closed[-min(len(closed), hours):])
        _, ctx_val, ctx_vah = _value_area(closed[-min(len(closed), context_hours):])
        window = closed[-24:]
        swing_low = min(bar.low for bar in window)
        swing_high = max(bar.high for bar in window)
        if price is not None:
            below = [bar.low for bar in closed[-72:] if bar.low < price]
            above = [bar.high for bar in closed[-72:] if bar.high > price]
            support = max(below) if below else swing_low
            resistance = min(above) if above else swing_high
        else:
            support, resistance = swing_low, swing_high
        stamp = closed[-1].close_time
        features["support"] = Feature(support, DataStatus.VERIFIED, "derived: 1h swing structure", stamp, "nearest structural low below price")
        features["resistance"] = Feature(resistance, DataStatus.VERIFIED, "derived: 1h swing structure", stamp, "nearest structural high above price")
        features["poc"] = Feature(poc, DataStatus.VERIFIED, f"derived: {hours}h volume profile", stamp, "point of control")
        features["val"] = Feature(val, DataStatus.VERIFIED, f"derived: {hours}h volume profile", stamp, "value area low (70%)")
        features["vah"] = Feature(vah, DataStatus.VERIFIED, f"derived: {hours}h volume profile", stamp, "value area high (70%)")
        features["vah_context"] = Feature(ctx_vah, DataStatus.VERIFIED, f"derived: {context_hours}h volume profile", stamp, "longer-horizon context only, never a gate")
        features["val_context"] = Feature(ctx_val, DataStatus.VERIFIED, f"derived: {context_hours}h volume profile", stamp, "longer-horizon context only, never a gate")
        observed.append(stamp)
    except Exception as error:  # noqa: BLE001
        for key in ("support", "resistance", "poc", "val", "vah"):
            unavailable(key, f"1h bars unavailable: {error}")

    try:
        depth = client.depth()
        imbalance, mid = _book_imbalance(depth)
        if imbalance is None:
            unavailable("book_imbalance", "empty order book")
        else:
            features["book_imbalance"] = Feature(imbalance, DataStatus.VERIFIED, "binance spot depth", utc_now(), "0.5% band bid/ask imbalance")
            observed.append(utc_now())
    except Exception as error:  # noqa: BLE001
        unavailable("book_imbalance", f"depth unavailable: {error}")

    regen_days = getattr(config.regime, "sma_days", None) or 50
    try:
        daily = client.klines("1d", max(regen_days + 5, 60))
        closed_daily = [bar for bar in daily if bar.close_time <= utc_now()]
        if len(closed_daily) < regen_days:
            raise MarketDataError("insufficient daily bars for SMA")
        sma = sum((bar.close for bar in closed_daily[-regen_days:]), Decimal("0")) / Decimal(regen_days)
        features["sma"] = Feature(sma, DataStatus.VERIFIED, f"derived: {regen_days}d close SMA", closed_daily[-1].close_time, "regime reference")
    except Exception as error:  # noqa: BLE001
        unavailable("sma", f"daily bars unavailable: {error}")

    # Contextual derivatives telemetry — never a gate on its own.
    try:
        oi, oi_time = client.open_interest()
        features["open_interest"] = Feature(oi, DataStatus.VERIFIED, "binance usdt-m futures", oi_time, "contextual only")
    except Exception as error:  # noqa: BLE001
        unavailable("open_interest", f"open interest unavailable: {error}")
    try:
        funding, funding_time = client.funding()
        features["funding_rate"] = Feature(funding, DataStatus.VERIFIED, "binance usdt-m futures", funding_time, "contextual only")
    except Exception as error:  # noqa: BLE001
        unavailable("funding_rate", f"funding unavailable: {error}")

    # No trustworthy free liquidation feed exists; say so rather than guess.
    features["liquidations"] = Feature(
        value=None,
        status=DataStatus.UNAVAILABLE,
        source="none configured",
        detail="no verified liquidation source; heatmap estimates are not confirmed liquidations",
    )

    if average_cost is not None:
        features["average_cost_basis"] = Feature(
            average_cost, DataStatus.VERIFIED, "internal ledger", utc_now(), "weighted average cost of acquired tactical BTC"
        )
    else:
        features["average_cost_basis"] = Feature(
            value=None, status=DataStatus.UNAVAILABLE, source="internal ledger", detail="no purchases recorded"
        )

    as_of = max(observed) if observed else utc_now()
    snapshot = build_market_snapshot(
        as_of=as_of,
        instrument="BTC/USDT",
        price=price,
        features=features,
        config_version=config.config_version,
        source="binance-public",
    )
    return snapshot


__all__ = ["BinancePublic", "Kline", "build_snapshot", "parse_klines"]
