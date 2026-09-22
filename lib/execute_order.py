#!/usr/bin/env python3
"""Place one order with bracket support and fill confirmation.

Usage:
  python3 lib/execute_order.py <symbol> <side> <qty> [options]

Options:
  --limit-price <price>        Limit order price
  --stop-price <price>         Stop loss price (creates bracket order)
  --take-profit-price <price>  Take profit price (creates bracket order)
  --order-type <type>          Order type: market (default), limit
  --time-in-force <tif>        Time in force: day (default), gtc, ioc, fok
  --no-confirm                 Skip fill confirmation wait
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.config import Config
from lib.alpaca_client import AlpacaClient

FILL_POLL_INTERVAL = 2.0
FILL_POLL_TIMEOUT = 60.0


def parse_args(argv: list[str]) -> dict:
    if len(argv) < 4:
        print(json.dumps({"error": "Usage: execute_order.py <symbol> <side> <qty> [options]"}))
        sys.exit(1)

    args = {
        "symbol": argv[1].upper(),
        "side": argv[2].lower(),
        "qty": float(argv[3]),
        "limit_price": None,
        "stop_price": None,
        "take_profit_price": None,
        "order_type": "market",
        "time_in_force": "day",
        "no_confirm": False,
    }

    i = 4
    while i < len(argv):
        if argv[i] == "--limit-price" and i + 1 < len(argv):
            args["limit_price"] = argv[i + 1]
            args["order_type"] = "limit"
            i += 2
        elif argv[i] == "--stop-price" and i + 1 < len(argv):
            args["stop_price"] = argv[i + 1]
            i += 2
        elif argv[i] == "--take-profit-price" and i + 1 < len(argv):
            args["take_profit_price"] = argv[i + 1]
            i += 2
        elif argv[i] == "--order-type" and i + 1 < len(argv):
            args["order_type"] = argv[i + 1]
            i += 2
        elif argv[i] == "--time-in-force" and i + 1 < len(argv):
            args["time_in_force"] = argv[i + 1]
            i += 2
        elif argv[i] == "--no-confirm":
            args["no_confirm"] = True
            i += 1
        else:
            print(json.dumps({"error": f"Unknown argument: {argv[i]}"}))
            sys.exit(1)

    return args


def wait_for_fill(client: AlpacaClient, order_id: str, timeout: float = FILL_POLL_TIMEOUT) -> dict:
    """Poll order status until filled, canceled, expired, or timed out."""
    elapsed = 0.0
    while elapsed < timeout:
        try:
            order = client.get_order(order_id)
        except Exception as e:
            return {"status": "error", "error": str(e)}

        status = order.get("status", "unknown")
        if status in ("filled", "canceled", "expired", "rejected"):
            return order

        time.sleep(FILL_POLL_INTERVAL)
        elapsed += FILL_POLL_INTERVAL

    return {"status": "timeout", "order_id": order_id}


def log_execution(reports_dir: Path, record: dict, existing: dict | None = None) -> None:
    """Append execution record to trader.json."""
    if existing is None:
        existing = {"timestamp": None, "executions": []}

    existing.setdefault("executions", []).append(record)
    existing["timestamp"] = datetime.now(timezone.utc).isoformat()

    trader_file = reports_dir / "trader.json"
    with open(trader_file, "w") as f:
        json.dump(existing, f, indent=2, default=str)


def main():
    args = parse_args(sys.argv)
    cfg = Config()
    client = AlpacaClient(cfg)

    has_bracket = args["stop_price"] or args["take_profit_price"]

    if has_bracket:
        result = client.place_bracket_order(
            symbol=args["symbol"],
            qty=args["qty"],
            side=args["side"],
            limit_price=args["limit_price"],
            take_profit_price=args["take_profit_price"],
            stop_loss_price=args["stop_price"],
        )
    else:
        result = client.place_order(
            symbol=args["symbol"],
            qty=args["qty"],
            side=args["side"],
            order_type=args["order_type"],
            time_in_force=args["time_in_force"],
            limit_price=args["limit_price"],
            stop_price=args["stop_price"],
        )

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

    order_id = str(result.get("id") or result.get("order_id", ""))
    status = result.get("status", "unknown")

    if not args["no_confirm"] and order_id and status not in ("dry-run", "error"):
        print(json.dumps({"confirming": True, "order_id": order_id}))
        fill = wait_for_fill(client, order_id)
        status = fill.get("status", status)
        result = {**result, **fill}

    record = {
        "symbol": args["symbol"],
        "side": args["side"],
        "qty": args["qty"],
        "order_type": args["order_type"],
        "status": status,
        "order_id": order_id,
    }
    if args["stop_price"]:
        record["stop_price"] = args["stop_price"]
    if args["take_profit_price"]:
        record["take_profit_price"] = args["take_profit_price"]
    if "filled_avg_price" in result:
        record["fill_price"] = result["filled_avg_price"]

    log_execution(reports_dir, record, existing)


if __name__ == "__main__":
    main()
