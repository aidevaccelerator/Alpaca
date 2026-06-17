import logging
from datetime import datetime, timezone

from config import Config
from broker.alpaca_client import AlpacaClient

log = logging.getLogger("risk.manager")


class RiskManager:
    def __init__(self, config: Config, client: AlpacaClient):
        self.cfg = config
        self.client = client
        self._day_start_equity: float | None = None

    def check(self, account: dict) -> list[str]:
        violations: list[str] = []
        equity = float(account["equity"])
        buying_power = float(account["buying_power"])
        cash = float(account["cash"])
        day_change = float(account.get("equity_change", 0))

        if self._day_start_equity is None:
            self._day_start_equity = equity

        dd_pct = (self._day_start_equity - equity) / self._day_start_equity * 100
        if dd_pct > self.cfg.max_daily_drawdown_pct:
            violations.append(
                f"Daily drawdown {dd_pct:.2f}% exceeds limit {self.cfg.max_daily_drawdown_pct}%"
            )

        leverage = float(account.get("multiplier", 1))
        if leverage > self.cfg.max_leverage:
            violations.append(f"Leverage {leverage}x exceeds max {self.cfg.max_leverage}x")

        pattern = account.get("pattern_day_trader", False)
        if pattern:
            violations.append("Pattern Day Trader flag is active — trading restricted")

        return violations

    def validate_order(self, symbol: str, side: str, qty: float, account: dict, positions: list[dict]) -> list[str]:
        violations: list[str] = []
        equity = float(account["equity"])

        order_value = qty * self._estimate_price(symbol)
        position_pct = order_value / equity * 100
        if position_pct > self.cfg.max_position_pct:
            violations.append(
                f"Position size {position_pct:.2f}% exceeds limit {self.cfg.max_position_pct}%"
            )

        current_pos = next((p for p in positions if p["symbol"] == symbol), None)
        if current_pos:
            current_qty = abs(float(current_pos["qty"]))
            new_qty = current_qty + qty if side == "buy" else current_qty - qty
            new_pct = (new_qty * self._estimate_price(symbol)) / equity * 100
            if new_pct > self.cfg.max_position_pct:
                violations.append(
                    f"Combined position would be {new_pct:.2f}% — exceeds limit"
                )

        return violations

    def _estimate_price(self, symbol: str) -> float:
        try:
            bars = self.client.get_bars(symbol, timeframe="1Min", limit=1)
            if bars:
                return bars[0]["c"]
        except Exception:
            pass
        return 100.0
