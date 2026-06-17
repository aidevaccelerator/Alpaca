#!/usr/bin/env python3
"""Check positions and account after execution."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.config import Config
from lib.alpaca_client import AlpacaClient

cfg = Config()
client = AlpacaClient(cfg)

print("=== POSITIONS ===")
positions = client.get_positions()
if positions:
    for p in positions:
        avg_entry = float(p["cost_basis"]) / float(p["qty"])
        curr_price = float(p["market_value"]) / float(p["qty"])
        print(f"  {p['symbol']:6s} | {float(p['qty']):>5.1f} sh | entry ${avg_entry:.2f} | curr ${curr_price:.2f} | P&L ${float(p['unrealized_pl']):+.2f}")
else:
    print("  No positions yet")

print()
print("=== ACCOUNT ===")
acct = client.get_account()
print(f"  Equity:          ${float(acct['equity']):>8.2f}")
print(f"  Cash:            ${float(acct['cash']):>8.2f}")
print(f"  Buying Power:    ${float(acct['buying_power']):>8.2f}")
print(f"  Position Value:  ${float(acct['position_market_value']):>8.2f}")

print()
print("=== OPEN ORDERS ===")
orders = client.get_orders("open")
if orders:
    for o in orders:
        print(f"  {o['symbol']:6s} | {o['side']:4s} | {o['qty']:>4s} | {o['status']}")
else:
    print("  No open orders")
