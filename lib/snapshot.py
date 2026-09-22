#!/usr/bin/env python3
"""Collect all current market/account data and write to reports/ as JSON.

Writes:
  - account.json      Current account state
  - clock.json        Market open/close status
  - positions.json    All open positions
  - open_orders.json  Pending orders (NEW)
  - history/          Daily snapshots for P&L tracking (NEW)
"""
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.config import Config
from lib.alpaca_client import AlpacaClient

logging.basicConfig(level=logging.WARNING, format="%(message)s")

OUTPUT_FILES = {
    "account": "account.json",
    "clock": "clock.json",
    "positions": "positions.json",
    "open_orders": "open_orders.json",
}


def main():
    cfg = Config()
    client = AlpacaClient(cfg)

    reports_dir = cfg.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)

    try:
        account = client.get_account()
    except Exception as e:
        account = {"error": str(e)}

    try:
        clock = client.get_clock()
    except Exception as e:
        clock = {"error": str(e)}

    try:
        positions = client.get_positions()
    except Exception as e:
        positions = {"error": str(e)}

    try:
        open_orders = client.get_orders("open")
    except Exception as e:
        open_orders = {"error": str(e)}

    data = {
        "account": account,
        "clock": clock,
        "positions": positions,
        "open_orders": open_orders,
    }

    for key, filename in OUTPUT_FILES.items():
        path = reports_dir / filename
        with open(path, "w") as f:
            json.dump(data[key], f, indent=2, default=str)

    # Write daily snapshot to history/
    history_dir = reports_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    snapshot = {
        "timestamp": now.isoformat(),
        "equity": float(account.get("equity", 0)) if isinstance(account, dict) and "error" not in account else 0,
        "cash": float(account.get("cash", 0)) if isinstance(account, dict) and "error" not in account else 0,
        "buying_power": float(account.get("buying_power", 0)) if isinstance(account, dict) and "error" not in account else 0,
        "position_count": len(positions) if isinstance(positions, list) else 0,
        "positions": positions if isinstance(positions, list) else [],
    }
    history_file = history_dir / f"{today_str}.json"
    existing_history = []
    if history_file.exists():
        try:
            existing_history = json.loads(history_file.read_text())
        except Exception:
            pass
    existing_history.append(snapshot)
    with open(history_file, "w") as f:
        json.dump(existing_history, f, indent=2, default=str)

    is_open = clock.get("is_open", False) if isinstance(clock, dict) else False
    equity = float(account.get("equity", 0)) if isinstance(account, dict) else 0
    bp = float(account.get("buying_power", 0)) if isinstance(account, dict) else 0
    pos_count = len(positions) if isinstance(positions, list) else 0
    orders_count = len(open_orders) if isinstance(open_orders, list) else 0

    print(f"MARKET={'OPEN' if is_open else 'CLOSED'} | EQUITY={equity:.2f} | BP={bp:.2f} | POSITIONS={pos_count} | OPEN_ORDERS={orders_count}")


if __name__ == "__main__":
    main()
