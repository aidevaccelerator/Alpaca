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

    data = {
        "account": account,
        "clock": clock,
        "positions": positions,
    }

    for key, filename in OUTPUT_FILES.items():
        path = reports_dir / filename
        with open(path, "w") as f:
            json.dump(data[key], f, indent=2, default=str)

    is_open = clock.get("is_open", False) if isinstance(clock, dict) else False
    equity = float(account.get("equity", 0)) if isinstance(account, dict) else 0
    bp = float(account.get("buying_power", 0)) if isinstance(account, dict) else 0
    pos_count = len(positions) if isinstance(positions, list) else 0

    print(f"MARKET={'OPEN' if is_open else 'CLOSED'} | EQUITY={equity:.2f} | BP={bp:.2f} | POSITIONS={pos_count}")


if __name__ == "__main__":
    main()
