"""Bot #2 exit manager — mechanical rules checked every cycle.

Exit triggers (first match wins):
  1. profit:   buyback debit ≤ 50% of entry credit   → bank it
  2. stop:     buyback debit ≥ 2× entry credit        → cut it
  3. time:     ≤ 21 DTE                               → close (gamma danger zone)
  4. laya:     reject with immediate urgency          → close early

Close orders are mleg with reversed position_intents and a POSITIVE limit
(debit we pay, mid + small buffer for fill certainty).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from lib.config import OptionsConfig
from lib.options_client import OptionsClient
from lib.options_positions import group_spreads
from lib.options_risk import root_in_open_orders

log = logging.getLogger("options.manage")

EXIT_PROFIT_PCT = 0.50      # buy back at ≤ 50% of credit
EXIT_MAX_DTE = 21
STOP_DEBIT_MULT = 2.0       # buy back at ≥ 2× credit
LIMIT_BUFFER = 0.02         # pay up slightly to ensure fill


def _exit_reason(spread: dict) -> str | None:
    credit = spread["credit"]
    debit = spread["current_debit"]
    dte = spread.get("dte")

    if credit <= 0:
        return "bad_credit_data"
    if debit <= EXIT_PROFIT_PCT * credit:
        return f"profit_50pct:debit={debit:.2f}<=credit*{EXIT_PROFIT_PCT}={EXIT_PROFIT_PCT * credit:.2f}"
    if debit >= STOP_DEBIT_MULT * credit:
        return f"stop_2x_credit:debit={debit:.2f}>=credit*{STOP_DEBIT_MULT}={STOP_DEBIT_MULT * credit:.2f}"
    if dte is not None and dte <= EXIT_MAX_DTE:
        return f"time_exit:{dte}dte<={EXIT_MAX_DTE}"
    return None


def _laya_urgent(spread: dict) -> str | None:
    """Advisory Laya check on open spreads; close only on urgent reject."""
    try:
        from lib.laya_signals import assess_options_position
    except ImportError:
        return None
    credit = spread["credit"]
    max_profit = spread["max_profit"]
    pnl_pct = round(spread["unrealized_pl"] / max_profit * 100, 2) if max_profit else 0.0
    result = assess_options_position(
        root=spread["root"],
        pnl_pct=pnl_pct,
        dte=spread.get("dte"),
        debit_multiple=round(spread["current_debit"] / credit, 2) if credit else None,
    )
    if result.get("error"):
        return None
    if result.get("decision") == "reject" and result.get("urgency") == "immediate":
        return f"laya_urgent:pnl={pnl_pct}%"
    return None


def close_spread(client: OptionsClient, spread: dict) -> dict:
    """Place the closing mleg order (buy back short, sell long).

    Priced at the combo ask (what closing actually costs) for a near-certain
    fill; falls back to mid + buffer when leg quotes are unavailable.
    """
    legs = [
        {"symbol": spread["short_symbol"], "ratio_qty": "1",
         "side": "buy", "position_intent": "buy_to_close"},
        {"symbol": spread["long_symbol"], "ratio_qty": "1",
         "side": "sell", "position_intent": "sell_to_close"},
    ]
    limit = None
    try:
        snaps = client.get_snapshots_by_symbols(
            [spread["short_symbol"], spread["long_symbol"]])
        sq = snaps[spread["short_symbol"]]["latestQuote"]
        lq = snaps[spread["long_symbol"]]["latestQuote"]
        combo_ask = float(sq["ap"]) - float(lq["bp"])
        limit = round(min(combo_ask + 0.01, spread["width"]), 2)
    except Exception:
        limit = None
    if limit is None or limit <= 0:
        limit = round(min(spread["current_debit"] + LIMIT_BUFFER, spread["width"]), 2)
    limit = max(limit, 0.01)
    order = client.place_mleg_order(legs=legs, qty=spread["qty"], limit_price=limit)
    order_id = str(order.get("id") or "")
    fill = None
    if order_id and order.get("status") not in ("dry-run", "error"):
        fill = client.wait_for_fill(order_id, timeout=120.0)
    return {"order": order, "fill": fill, "limit_price": limit}


def manage(client: OptionsClient, ocfg: OptionsConfig) -> dict:
    """Evaluate all open spreads and close any that trip an exit rule."""
    clock = client.get_clock()
    is_open = clock.get("is_open", False) if isinstance(clock, dict) else False
    if not is_open:
        return {"skipped": "market_closed", "actions": []}

    positions = client.get_positions()
    spreads, unmanaged = group_spreads(positions)
    open_orders = client.get_orders("open")

    actions = []
    for spread in spreads:
        if root_in_open_orders(spread["root"], open_orders):
            actions.append({
                "root": spread["root"], "action": "skip",
                "reason": "close_order_already_pending",
            })
            continue

        reason = _exit_reason(spread)
        if reason is None:
            reason = _laya_urgent(spread)
        if reason is None:
            actions.append({
                "root": spread["root"], "action": "hold",
                "dte": spread.get("dte"),
                "credit": spread["credit"],
                "current_debit": spread["current_debit"],
                "unrealized_pl": spread["unrealized_pl"],
            })
            continue

        result = close_spread(client, spread)
        status = (result["fill"] or {}).get("status") or result["order"].get("status", "unknown")
        actions.append({
            "root": spread["root"],
            "action": "close",
            "reason": reason,
            "status": status,
            "limit_price": result["limit_price"],
            "order_id": str(result["order"].get("id") or ""),
            "short_symbol": spread["short_symbol"],
            "long_symbol": spread["long_symbol"],
            "qty": spread["qty"],
            "realized_estimate": spread["unrealized_pl"],
        })
        if status not in ("filled", "dry-run"):
            log.warning("Close order for %s not filled: %s", spread["root"], status)

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "is_open": is_open,
        "spread_count": len(spreads),
        "unmanaged_legs": [l.get("symbol") for l in unmanaged],
        "actions": actions,
        "spreads": [
            {k: s[k] for k in (
                "root", "expiration", "dte", "qty", "width", "credit",
                "current_debit", "max_loss", "max_profit", "unrealized_pl",
            )}
            for s in spreads
        ],
    }
