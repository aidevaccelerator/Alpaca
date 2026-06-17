#!/usr/bin/env python3
"""Place one order. Usage: python3 lib/execute_order.py <symbol> <side> <qty>"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.config import Config
from lib.alpaca_client import AlpacaClient

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(json.dumps({"error": "Usage: execute_order.py <symbol> <side> <qty>"}))
        sys.exit(1)

    symbol = sys.argv[1].upper()
    side = sys.argv[2].lower()
    qty = int(sys.argv[3])

    cfg = Config()
    client = AlpacaClient(cfg)

    result = client.place_order(symbol, qty, side)
    print(json.dumps(result, indent=2, default=str))

    reports_dir = cfg.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    trader_file = reports_dir / "trader.json"

    existing = {"timestamp": None, "executions": []}
    if trader_file.exists():
        try:
            existing = json.loads(trader_file.read_text())
        except Exception:
            pass

    existing.setdefault("executions", []).append({
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "status": result.get("status", "unknown"),
        "order_id": str(result.get("id") or result.get("order_id", "")),
    })
    existing["timestamp"] = datetime.now(timezone.utc).isoformat()

    with open(trader_file, "w") as f:
        json.dump(existing, f, indent=2, default=str)
