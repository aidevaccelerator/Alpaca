#!/usr/bin/env python3
"""Collect all current market/account data and write to reports/ as JSON."""
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.config import Config
from lib.alpaca_client import AlpacaClient

logging.basicConfig(level=logging.WARNING, format="%(message)s")

OUTPUT_FILES = {
    "account": "account.json",
    "clock": "clock.json",
    "positions": "positions.json",
    "market_data": "market_data.json",
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

    market_data = {}
    for sym in cfg.symbols:
        try:
            bars = client.get_bars(sym, timeframe="5Min", limit=5)
            if bars:
                last = bars[-1]
                market_data[sym] = {
                    "price": last["c"],
                    "change_pct": (last["c"] - bars[-2]["c"]) / bars[-2]["c"] * 100 if len(bars) > 1 else 0,
                    "volume": last["v"],
                    "high": last["h"],
                    "low": last["l"],
                }
        except Exception as e:
            market_data[sym] = {"error": str(e)}

    data = {
        "account": account,
        "clock": clock,
        "positions": positions,
        "market_data": market_data,
    }

    for key, filename in OUTPUT_FILES.items():
        path = reports_dir / filename
        with open(path, "w") as f:
            json.dump(data[key], f, indent=2, default=str)

    # Print summary for the orchestrator
    is_open = clock.get("is_open", False) if isinstance(clock, dict) else False
    equity = float(account.get("equity", 0)) if isinstance(account, dict) else 0
    bp = float(account.get("buying_power", 0)) if isinstance(account, dict) else 0
    pos_count = len(positions) if isinstance(positions, list) else 0

    print(f"MARKET={'OPEN' if is_open else 'CLOSED'} | EQUITY={equity:.2f} | BP={bp:.2f} | POSITIONS={pos_count}")

    for sym, info in market_data.items():
        if "price" in info:
            print(f"DATA  {sym} | {info['price']:.2f} | {info['change_pct']:+.2f}% | vol={info['volume']}")
        elif "error" in info:
            print(f"ERROR {sym} | {info['error']}")


if __name__ == "__main__":
    main()
