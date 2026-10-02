"""Code-enforced risk checks for Bot #2 entries.

Unlike Bot #1 (prose limits in agent markdown), every cap here is executable.

Caps (defaults):
  - max 3 concurrent spreads, max 1 per underlying
  - per-trade max loss  ≤ 2% of options-account equity
  - total spread exposure ≤ 6% of equity
  - daily loss halt at -2% of equity (entries blocked until next day)
  - sufficient buying power (margin = width*100 - credit*100)
  - proposal freshness < 5 minutes (same-cycle decisions only)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

MAX_CONCURRENT_SPREADS = 3
MAX_PER_TRADE_LOSS_PCT = 0.02
MAX_TOTAL_EXPOSURE_PCT = 0.06
DAILY_LOSS_HALT_PCT = 0.02
PROPOSAL_MAX_AGE = timedelta(minutes=5)


def _f(d: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(d.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _proposal_age_ok(scanned_at: str) -> tuple[bool, str]:
    try:
        ts = datetime.fromisoformat(scanned_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False, "unparseable_scanned_at"
    age = datetime.now(timezone.utc) - ts
    return age <= PROPOSAL_MAX_AGE, f"age={age.total_seconds():.0f}s"


def _daily_loss_halted(account: dict, history_today: list[dict]) -> tuple[bool, float]:
    """True (halted) if equity is down ≥ DAILY_LOSS_HALT_PCT from day-start snapshot."""
    equity = _f(account, "equity")
    if equity <= 0 or not history_today:
        return False, 0.0
    start = _f(history_today[0], "equity")
    if start <= 0:
        return False, 0.0
    day_pnl_pct = (equity - start) / start
    return day_pnl_pct <= -DAILY_LOSS_HALT_PCT, day_pnl_pct


def root_in_open_orders(root: str, open_orders: list[dict]) -> bool:
    for order in open_orders if isinstance(open_orders, list) else []:
        if not isinstance(order, dict):
            continue
        if order.get("symbol") == root:
            return True
        for leg in order.get("legs") or []:
            sym = leg.get("symbol", "")
            # OCC symbols start with the root (SPY..., QQQ..., IWM...)
            if sym.startswith(root):
                return True
    return False


def validate(proposal: dict, account: dict, spreads: list[dict],
             open_orders: list[dict], history_today: list[dict]) -> dict:
    """Validate one proposal. Returns {"approved": bool, "checks": [...], "reasons": [...]}."""
    reasons: list[str] = []
    checks: list[str] = []

    equity = _f(account, "equity")
    buying_power = _f(account, "buying_power", _f(account, "regt_buying_power"))
    max_loss = _f(proposal, "max_loss")
    root = proposal.get("root", "?")

    ok, age_detail = _proposal_age_ok(proposal.get("scanned_at", ""))
    checks.append(f"freshness:{age_detail}")
    if not ok:
        reasons.append("stale_proposal")

    if equity <= 0:
        reasons.append("no_equity")
    if len(spreads) >= MAX_CONCURRENT_SPREADS:
        reasons.append(f"max_spreads:{len(spreads)}>={MAX_CONCURRENT_SPREADS}")
    else:
        checks.append(f"spread_count:{len(spreads)}<{MAX_CONCURRENT_SPREADS}")

    if any(s.get("root") == root for s in spreads):
        reasons.append(f"already_open_on_{root}")
    else:
        checks.append(f"root_free:{root}")

    if root_in_open_orders(root, open_orders):
        reasons.append(f"open_order_on_{root}")
    else:
        checks.append(f"no_open_order:{root}")

    if equity > 0 and max_loss > MAX_PER_TRADE_LOSS_PCT * equity:
        reasons.append(f"per_trade_loss:{max_loss:.0f}>{MAX_PER_TRADE_LOSS_PCT * equity:.0f}")
    elif equity > 0:
        checks.append(f"per_trade_loss:{max_loss:.0f}<={MAX_PER_TRADE_LOSS_PCT * equity:.0f}")

    total_exposure = sum(_f(s, "max_loss") for s in spreads) + max_loss
    if equity > 0 and total_exposure > MAX_TOTAL_EXPOSURE_PCT * equity:
        reasons.append(f"total_exposure:{total_exposure:.0f}>{MAX_TOTAL_EXPOSURE_PCT * equity:.0f}")
    elif equity > 0:
        checks.append(f"total_exposure:{total_exposure:.0f}<={MAX_TOTAL_EXPOSURE_PCT * equity:.0f}")

    halted, day_pnl_pct = _daily_loss_halted(account, history_today)
    checks.append(f"daily_pnl:{day_pnl_pct * 100:+.2f}%")
    if halted:
        reasons.append(f"daily_loss_halt:{day_pnl_pct * 100:.2f}%")

    if buying_power < max_loss:
        reasons.append(f"insufficient_bp:{buying_power:.0f}<{max_loss:.0f}")
    else:
        checks.append(f"bp_ok:{buying_power:.0f}>={max_loss:.0f}")

    return {
        "root": root,
        "approved": not reasons,
        "reasons": reasons,
        "checks": checks,
        "equity": equity,
        "max_loss": max_loss,
        "validated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
