"""Simple backtest engine for strategy validation.

Usage:
  from lib.backtest import Backtester
  bt = Backtester(bars)
  result = bt.run_ema_crossover(fast=9, slow=20)
  print(result)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Trade:
    entry_idx: int
    entry_price: float
    exit_idx: int = 0
    exit_price: float = 0.0
    side: str = "long"
    pnl: float = 0.0
    pnl_pct: float = 0.0


@dataclass
class BacktestResult:
    strategy: str
    return_pct: float
    sharpe: float
    max_dd_pct: float
    win_rate_pct: float
    trades: int
    profit_factor: float
    avg_win_pct: float
    avg_loss_pct: float
    trade_log: list[Trade] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "return_pct": round(self.return_pct, 2),
            "sharpe": round(self.sharpe, 2),
            "max_dd_pct": round(self.max_dd_pct, 2),
            "win_rate_pct": round(self.win_rate_pct, 2),
            "trades": self.trades,
            "profit_factor": round(self.profit_factor, 2),
            "avg_win_pct": round(self.avg_win_pct, 2),
            "avg_loss_pct": round(self.avg_loss_pct, 2),
        }


class Backtester:
    def __init__(self, bars: list[dict], initial_capital: float = 10000.0, commission_pct: float = 0.0):
        self.bars = bars
        self.closes = [b.get("c", 0) for b in bars]
        self.highs = [b.get("h", 0) for b in bars]
        self.lows = [b.get("l", 0) for b in bars]
        self.initial_capital = initial_capital
        self.commission_pct = commission_pct

    def run_ema_crossover(self, fast: int = 9, slow: int = 20) -> BacktestResult:
        from lib.indicators import ema
        fast_ema = ema(self.closes, fast)
        slow_ema = ema(self.closes, slow)
        signals = _ema_signals(fast_ema, slow_ema)
        return self._simulate("ema_crossover", signals)

    def run_rsi_reversal(self, period: int = 14, oversold: float = 30, overbought: float = 70) -> BacktestResult:
        from lib.indicators import rsi
        rsi_vals = rsi(self.closes, period)
        signals: list[str | None] = [None] * len(rsi_vals)
        for i in range(1, len(rsi_vals)):
            prev = rsi_vals[i - 1]
            curr = rsi_vals[i]
            if prev is not None and curr is not None:
                if prev < oversold and curr >= oversold:
                    signals[i] = "buy"
                elif prev > overbought and curr <= overbought:
                    signals[i] = "sell"
        return self._simulate("rsi_reversal", signals)

    def run_bollinger_breakout(self, period: int = 20, std_dev: float = 2.0) -> BacktestResult:
        from lib.indicators import bollinger_bands
        bb = bollinger_bands(self.closes, period, std_dev)
        signals: list[str | None] = [None] * len(self.closes)
        for i in range(1, len(self.closes)):
            bl = bb["lower"][i]
            bu = bb["upper"][i]
            bl_prev = bb["lower"][i - 1]
            bu_prev = bb["upper"][i - 1]
            if bl is not None and bu is not None and bl_prev is not None and bu_prev is not None:
                if self.closes[i - 1] <= bl_prev and self.closes[i] > bl:
                    signals[i] = "buy"
                elif self.closes[i - 1] >= bu_prev and self.closes[i] < bu:
                    signals[i] = "sell"
        return self._simulate("bollinger_breakout", signals)

    def run_atr_trailing_stop(self, entry_fast: int = 9, entry_slow: int = 20, atr_period: int = 14, atr_mult: float = 2.0) -> BacktestResult:
        from lib.indicators import ema, atr
        fast_ema = ema(self.closes, entry_fast)
        slow_ema = ema(self.closes, entry_slow)
        atr_vals = atr(self.highs, self.lows, self.closes, atr_period)
        signals: list[str | None] = [None] * len(self.closes)
        in_position = False
        trailing_stop = 0.0

        for i in range(1, len(self.closes)):
            if not in_position:
                fe = fast_ema[i]
                se = slow_ema[i]
                fe_prev = fast_ema[i - 1]
                se_prev = slow_ema[i - 1]
                if fe is not None and se is not None and fe_prev is not None and se_prev is not None:
                    if fe > se and fe_prev <= se_prev:
                        signals[i] = "buy"
                        in_position = True
                        atr_val = atr_vals[i]
                        trailing_stop = self.closes[i] - atr_mult * (atr_val if atr_val is not None else 0)
            else:
                av = atr_vals[i]
                if av is not None:
                    new_stop = self.closes[i] - atr_mult * av
                    trailing_stop = max(trailing_stop, new_stop)
                if self.closes[i] <= trailing_stop:
                    signals[i] = "sell"
                    in_position = False

        return self._simulate("atr_trailing_stop", signals)

    def run_all(self) -> list[BacktestResult]:
        results = []
        try:
            results.append(self.run_ema_crossover())
        except Exception:
            pass
        try:
            results.append(self.run_rsi_reversal())
        except Exception:
            pass
        try:
            results.append(self.run_bollinger_breakout())
        except Exception:
            pass
        try:
            results.append(self.run_atr_trailing_stop())
        except Exception:
            pass
        return results

    def _simulate(self, strategy_name: str, signals: list[str | None]) -> BacktestResult:
        position = None
        trades: list[Trade] = []
        equity_curve = [self.initial_capital]

        for i, sig in enumerate(signals):
            if sig == "buy" and position is None:
                entry_price = self.closes[i]
                commission = self.initial_capital * self.commission_pct / 100
                position = Trade(entry_idx=i, entry_price=entry_price)
            elif sig == "sell" and position is not None:
                exit_price = self.closes[i]
                if position.side == "long":
                    pnl_pct = (exit_price - position.entry_price) / position.entry_price * 100
                    pnl = (exit_price - position.entry_price) * (self.initial_capital / position.entry_price)
                else:
                    pnl_pct = (position.entry_price - exit_price) / position.entry_price * 100
                    pnl = (position.entry_price - exit_price) * (self.initial_capital / position.entry_price)

                commission = (self.initial_capital * self.commission_pct / 100)
                pnl -= commission
                position.exit_idx = i
                position.exit_price = exit_price
                position.pnl = pnl
                position.pnl_pct = pnl_pct
                trades.append(position)
                equity_curve.append(equity_curve[-1] + pnl)
                position = None

        if position is not None:
            exit_price = self.closes[-1]
            pnl_pct = (exit_price - position.entry_price) / position.entry_price * 100
            pnl = (exit_price - position.entry_price) * (self.initial_capital / position.entry_price)
            position.exit_idx = len(self.closes) - 1
            position.exit_price = exit_price
            position.pnl = pnl
            position.pnl_pct = pnl_pct
            trades.append(position)
            equity_curve.append(equity_curve[-1] + pnl)

        total_return = (equity_curve[-1] - self.initial_capital) / self.initial_capital * 100
        sharpe = _compute_sharpe(equity_curve)
        max_dd = _compute_max_drawdown(equity_curve)
        win_rate, avg_win, avg_loss, pf = _compute_trade_stats(trades)

        return BacktestResult(
            strategy=strategy_name,
            return_pct=total_return,
            sharpe=sharpe,
            max_dd_pct=max_dd,
            win_rate_pct=win_rate,
            trades=len(trades),
            profit_factor=pf,
            avg_win_pct=avg_win,
            avg_loss_pct=avg_loss,
            trade_log=trades,
        )


def _ema_signals(fast: list[float | None], slow: list[float | None]) -> list[str | None]:
    signals: list[str | None] = [None] * len(fast)
    for i in range(1, len(fast)):
        if fast[i] is None or slow[i] is None or fast[i - 1] is None or slow[i - 1] is None:
            continue
        if fast[i] > slow[i] and fast[i - 1] <= slow[i - 1]:  # type: ignore[operator]
            signals[i] = "buy"
        elif fast[i] < slow[i] and fast[i - 1] >= slow[i - 1]:  # type: ignore[operator]
            signals[i] = "sell"
    return signals


def _compute_sharpe(equity_curve: list[float], risk_free_rate: float = 0.0) -> float:
    if len(equity_curve) < 2:
        return 0.0
    returns = []
    for i in range(1, len(equity_curve)):
        r = (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
        returns.append(r)
    if not returns:
        return 0.0
    mean_r = sum(returns) / len(returns)
    if len(returns) < 2:
        return 0.0
    variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(variance) if variance > 0 else 0
    if std == 0:
        return 0.0
    return (mean_r - risk_free_rate / 252) / std * math.sqrt(252)


def _compute_max_drawdown(equity_curve: list[float]) -> float:
    peak = equity_curve[0]
    max_dd = 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd = (peak - val) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _compute_trade_stats(trades: list[Trade]) -> tuple[float, float, float, float]:
    if not trades:
        return 0.0, 0.0, 0.0, 0.0
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0
    total_wins = sum(t.pnl for t in wins)
    total_losses = abs(sum(t.pnl for t in losses))
    pf = total_wins / total_losses if total_losses > 0 else 999.0 if total_wins > 0 else 0.0
    return win_rate, avg_win, avg_loss, pf
