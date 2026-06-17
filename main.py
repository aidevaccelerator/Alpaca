#!/usr/bin/env python3
import logging
import signal
import time
import sys
from datetime import datetime, timezone

from config import Config
from broker.alpaca_client import AlpacaClient
from strategy.simple_momentum import MomentumStrategy
from risk.manager import RiskManager
from portfolio.tracker import PortfolioTracker
from utils.logger import setup_logger

log = logging.getLogger("main")
shutdown_flag = False


def handle_shutdown(signum, frame):
    global shutdown_flag
    log.warning("Shutdown signal received — finishing current cycle...")
    shutdown_flag = True


def trading_cycle(client, strategy, risk_mgr, tracker, cfg):
    account = client.get_account()
    clock = client.get_clock()
    is_open = clock.get("is_open", False)
    positions = client.get_positions()

    now = datetime.now(timezone.utc).isoformat()
    log.info("=== Cycle %s | Market %s ===", now, "OPEN" if is_open else "CLOSED")

    tracker.snapshot(account, positions)

    if not is_open:
        log.info("Market closed. Skipping trade evaluation.")
        return

    account_equity = float(account["equity"])
    buying_power = float(account["buying_power"])
    cash = float(account["cash"])

    violations = risk_mgr.check(account)
    if violations:
        log.warning("Risk check FAILED — pausing trading: %s", violations)
        return

    log.info(
        "Equity=%.2f Cash=%.2f BP=%.2f Positions=%d",
        account_equity, cash, buying_power, len(positions),
    )

    held_symbols = {p["symbol"] for p in positions}

    for symbol in cfg.symbols:
        if shutdown_flag:
            break

        if not client.is_tradable(symbol):
            log.info("%s not tradable — skipping", symbol)
            continue

        signal = strategy.evaluate(symbol)

        if signal.action == "hold":
            log.info("%s: %s", symbol, signal.reason)
            continue

        qty = int(buying_power * signal.qty_pct / 100)
        if qty < 1:
            log.info("%s: signal=%s but qty < 1 share", symbol, signal.action)
            continue

        pos = next((p for p in positions if p["symbol"] == symbol), None)

        if signal.action == "buy":
            if pos and float(pos["qty"]) > 0:
                log.info("%s: already long — skipping", symbol)
                continue
            side = "buy"
        elif signal.action == "sell":
            if not pos or float(pos["qty"]) <= 0:
                log.info("%s: no position to sell", symbol)
                continue
            qty = min(qty, abs(float(pos["qty"])))
            if qty < 1:
                continue
            side = "sell"
        else:
            continue

        validation = risk_mgr.validate_order(symbol, side, qty, account, positions)
        if validation:
            log.warning("Order validation FAILED for %s %s %d: %s", symbol, side, qty, validation)
            continue

        log.info("Placing %s %d %s | confidence=%.2f reason=%s", side, qty, symbol, signal.confidence, signal.reason)
        result = client.place_order(symbol, qty, side)

        log.info("Order result: %s", result.get("id") or result.get("status"))


def main():
    cfg = Config()
    setup_logger("main", cfg.log_dir)
    setup_logger("alpaca_client", cfg.log_dir)
    setup_logger("strategy.momentum", cfg.log_dir)
    setup_logger("risk.manager", cfg.log_dir)
    setup_logger("portfolio.tracker", cfg.log_dir)

    if not cfg.api_key or not cfg.api_secret:
        log.error("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in environment")
        sys.exit(1)

    mode = "LIVE" if cfg.live_trading else "PAPER"
    log.info("Starting Alpaca Trader Bot (%s mode)", mode)
    log.info("Trading symbols: %s", cfg.symbols)
    log.info("Poll interval: %ds", cfg.poll_interval_seconds)

    client = AlpacaClient(cfg)
    strategy = MomentumStrategy(client)
    risk_mgr = RiskManager(cfg, client)
    tracker = PortfolioTracker(cfg)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    while not shutdown_flag:
        try:
            trading_cycle(client, strategy, risk_mgr, tracker, cfg)
        except Exception:
            log.exception("Error in trading cycle")

        log.info("Sleeping %ds...", cfg.poll_interval_seconds)
        for _ in range(cfg.poll_interval_seconds):
            if shutdown_flag:
                break
            time.sleep(1)

    log.warning("Shutdown complete.")


if __name__ == "__main__":
    main()
