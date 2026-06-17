import logging
from dataclasses import dataclass

from broker.alpaca_client import AlpacaClient

log = logging.getLogger("strategy.momentum")


@dataclass
class Signal:
    symbol: str
    action: str  # "buy", "sell", "hold"
    confidence: float  # 0.0 - 1.0
    qty_pct: float  # fraction of buying power to allocate
    reason: str


class MomentumStrategy:
    def __init__(self, client: AlpacaClient, lookback: int = 20):
        self.client = client
        self.lookback = lookback

    def evaluate(self, symbol: str) -> Signal:
        bars = self.client.get_bars(symbol, timeframe="5Min", limit=self.lookback)
        if not bars or len(bars) < self.lookback:
            return Signal(symbol, "hold", 0.0, 0.0, "insufficient data")

        closes = [b["c"] for b in bars]
        sma = sum(closes) / len(closes)
        last = closes[-1]

        price_change = (last - closes[0]) / closes[0]

        if last > sma and price_change > 0.005:
            confidence = min(abs(price_change) * 10, 0.9)
            return Signal(
                symbol, "buy", confidence, confidence * 0.5,
                f"price {last:.2f} above SMA {sma:.2f}, gain {price_change:.2%}",
            )
        elif last < sma and price_change < -0.005:
            confidence = min(abs(price_change) * 10, 0.9)
            return Signal(
                symbol, "sell", confidence, confidence * 0.5,
                f"price {last:.2f} below SMA {sma:.2f}, loss {price_change:.2%}",
            )

        return Signal(
            symbol, "hold", 0.0, 0.0,
            f"no clear signal (price {last:.2f}, SMA {sma:.2f})",
        )
