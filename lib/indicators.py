"""Technical indicators for trading analysis.

All functions accept lists of bar dicts with keys: o, h, l, c, v, t
Returns are lists of floats aligned to input length (NaN for insufficient data).
"""
from __future__ import annotations


def sma(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        result[i] = sum(values[i - period + 1 : i + 1]) / period
    return result


def ema(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    k = 2.0 / (period + 1)
    result[period - 1] = sum(values[:period]) / period
    for i in range(period, len(values)):
        result[i] = values[i] * k + result[i - 1] * (1 - k)  # type: ignore[operator]
    return result


def rsi(closes: list[float], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(closes)
    if len(closes) < period + 1:
        return result

    gains = []
    losses = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        result[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        result[period] = 100.0 - (100.0 / (1.0 + rs))

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i + 1] = 100.0 - (100.0 / (1.0 + rs))

    return result


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(closes)
    if len(closes) < 2:
        return result

    trs: list[float] = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)

    if len(trs) < period:
        return result

    atr_val = sum(trs[:period]) / period
    result[period] = atr_val
    for i in range(period, len(trs)):
        atr_val = (atr_val * (period - 1) + trs[i]) / period
        result[i + 1] = atr_val

    return result


def vwap(bars: list[dict]) -> list[float | None]:
    result: list[float | None] = [None] * len(bars)
    cum_vol = 0.0
    cum_tp_vol = 0.0
    for i, bar in enumerate(bars):
        h = bar.get("h", 0)
        l = bar.get("l", 0)
        c = bar.get("c", 0)
        v = bar.get("v", 0)
        tp = (h + l + c) / 3.0
        cum_vol += v
        cum_tp_vol += tp * v
        if cum_vol > 0:
            result[i] = cum_tp_vol / cum_vol
    return result


def bollinger_bands(closes: list[float], period: int = 20, std_dev: float = 2.0) -> dict[str, list[float | None]]:
    middle = sma(closes, period)
    upper: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        mean = middle[i]
        if mean is None:
            continue
        variance = sum((x - mean) ** 2 for x in window) / period
        sd = variance ** 0.5
        upper[i] = mean + std_dev * sd
        lower[i] = mean - std_dev * sd
    return {"upper": upper, "middle": middle, "lower": lower}


def ema_crossover(fast: list[float | None], slow: list[float | None]) -> list[str | None]:
    result: list[str | None] = [None] * len(fast)
    for i in range(1, len(fast)):
        if fast[i] is None or slow[i] is None or fast[i - 1] is None or slow[i - 1] is None:
            continue
        if fast[i] > slow[i] and fast[i - 1] <= slow[i - 1]:  # type: ignore[operator]
            result[i] = "bullish_cross"
        elif fast[i] < slow[i] and fast[i - 1] >= slow[i - 1]:  # type: ignore[operator]
            result[i] = "bearish_cross"
    return result


def compute_all(bars: list[dict], daily_bars: list[dict] | None = None) -> dict:
    closes = [b.get("c", 0) for b in bars]
    highs = [b.get("h", 0) for b in bars]
    lows = [b.get("l", 0) for b in bars]

    rsi_14 = rsi(closes, 14)
    ema_9 = ema(closes, 9)
    ema_20 = ema(closes, 20)
    ema_50 = ema(closes, 50)
    atr_14 = atr(highs, lows, closes, 14)
    vwap_vals = vwap(bars)
    bb = bollinger_bands(closes, 20, 2.0)
    crosses = ema_crossover(ema_9, ema_20)

    result: dict = {
        "timeframe": bars[0].get("t", "") if bars else "",
        "bars_count": len(bars),
        "latest_price": closes[-1] if closes else 0,
        "rsi_14": _round(rsi_14[-1]),
        "ema_9": _round(ema_9[-1]),
        "ema_20": _round(ema_20[-1]),
        "ema_50": _round(ema_50[-1]),
        "atr_14": _round(atr_14[-1]),
        "vwap": _round(vwap_vals[-1]),
        "bb_upper": _round(bb["upper"][-1]),
        "bb_middle": _round(bb["middle"][-1]),
        "bb_lower": _round(bb["lower"][-1]),
        "ema_9_20_cross": crosses[-1],
        "rsi_signal": _rsi_signal(rsi_14[-1]),
        "trend_signal": _trend_signal(ema_9[-1], ema_20[-1], ema_50[-1]),
        "stop_suggestion": _atr_stop(closes[-1], atr_14[-1]) if closes and atr_14[-1] else None,
    }

    if daily_bars and len(daily_bars) >= 50:
        dc = [b.get("c", 0) for b in daily_bars]
        de_20 = ema(dc, 20)
        de_50 = ema(dc, 50)
        result["daily_ema_20"] = _round(de_20[-1])
        result["daily_ema_50"] = _round(de_50[-1])
        result["daily_regime"] = _regime(de_20[-1], de_50[-1])

    return result


def _round(val: float | None, decimals: int = 2) -> float | None:
    if val is None:
        return None
    return round(val, decimals)


def _rsi_signal(val: float | None) -> str:
    if val is None:
        return "unknown"
    if val >= 70:
        return "overbought"
    if val <= 30:
        return "oversold"
    if val >= 60:
        return "bullish"
    if val <= 40:
        return "bearish"
    return "neutral"


def _trend_signal(e9: float | None, e20: float | None, e50: float | None) -> str:
    if e9 is None or e20 is None:
        return "unknown"
    if e9 > e20:
        if e50 and e20 > e50:
            return "strong_uptrend"
        return "uptrend"
    if e50 and e20 < e50:
        return "strong_downtrend"
    return "downtrend"


def _atr_stop(price: float, atr_val: float) -> dict:
    stop = round(price - 2 * atr_val, 2)
    risk_pct = round((price - stop) / price * 100, 2)
    return {"stop_price": stop, "risk_pct": risk_pct, "method": "2x ATR"}


def _regime(fast: float | None, slow: float | None) -> str:
    if fast is None or slow is None:
        return "unknown"
    if fast > slow:
        return "uptrend"
    return "downtrend"
