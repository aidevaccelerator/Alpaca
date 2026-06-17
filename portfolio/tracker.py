import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from config import Config

log = logging.getLogger("portfolio.tracker")


class PortfolioTracker:
    def __init__(self, config: Config):
        self.cfg = config
        self.history_file = config.data_dir / "portfolio_history.json"
        self._history: list[dict] = self._load_history()

    def _load_history(self) -> list[dict]:
        if self.history_file.exists():
            with open(self.history_file) as f:
                return json.load(f)
        return []

    def snapshot(self, account: dict, positions: list[dict]) -> dict:
        snap = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "equity": float(account["equity"]),
            "cash": float(account["cash"]),
            "buying_power": float(account["buying_power"]),
            "positions": [
                {
                    "symbol": p["symbol"],
                    "qty": float(p["qty"]),
                    "market_value": float(p["market_value"]),
                    "cost_basis": float(p["cost_basis"]),
                    "unrealized_pl": float(p["unrealized_pl"]),
                    "unrealized_plpc": float(p["unrealized_plpc"]),
                }
                for p in positions
            ],
        }
        self._history.append(snap)
        self._trim_history()
        self._save_history()
        return snap

    def _trim_history(self, max_entries: int = 10000):
        if len(self._history) > max_entries:
            self._history = self._history[-max_entries:]

    def _save_history(self):
        with open(self.history_file, "w") as f:
            json.dump(self._history, f, indent=2, default=str)

    def todays_pnl(self) -> float:
        today = datetime.now(timezone.utc).date()
        today_snaps = [
            s for s in self._history
            if s["timestamp"][:10] == today.isoformat()
        ]
        if len(today_snaps) < 2:
            return 0.0
        return today_snaps[-1]["equity"] - today_snaps[0]["equity"]
