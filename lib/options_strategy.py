"""Bot #2 rule engine — bull put credit spreads on index ETFs.

Deterministic selection (no Laya here — the CLI applies the Laya veto):

  1. Regime gate: entries blocked only when BOTH SPY and QQQ are below
     their 50-day EMA (downtrend). Uptrend/neutral allowed; flat is fine
     (theta works for you).
  2. Per underlying (SPY/QQQ/IWM): nearest expiration with 30–45 DTE.
  3. Short put: |delta| closest to 0.20 within [0.15, 0.30].
  4. Long put: ~$2 below (gap accepted in [1.0, 3.0], closest to 2.0).
  5. Entry priced at the EXECUTABLE credit: combo bid (immediately sellable),
     not the mid — mid-priced limits rarely fill. Floor: executable
     credit ≥ max(10% of width, $0.20); credit/width tracks the long leg's
     delta ≈ 0.13–0.15 in real chains — the delta band is the real quality
     gate; the floor only blocks dust.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from lib.indicators import ema
from lib.options_client import OptionsClient

TARGET_DELTA = 0.20
DELTA_MIN, DELTA_MAX = 0.15, 0.30
WIDTH_TARGET = 2.0
WIDTH_GAP_MIN, WIDTH_GAP_MAX = 1.0, 3.0
DTE_MIN, DTE_MAX = 30, 45
MIN_CREDIT_PCT = 0.10
MIN_CREDIT_ABS = 0.20
MAX_LEG_QUOTE_WIDTH = 0.15
MAX_ROOTS_SCANNED = 3


def market_regime(client: OptionsClient) -> str:
    """'uptrend' | 'neutral' | 'downtrend' from SPY/QQQ daily EMA-20 vs EMA-50.

    Downtrend only when BOTH indexes are bearish — mixed = neutral (allowed).
    """
    verdicts = []
    for sym in ("SPY", "QQQ"):
        bars = client.get_bars(sym, timeframe="1Day", limit=60, feed="iex")
        closes = [b["c"] for b in bars]
        if len(closes) < 50:
            return "unknown"
        e20, e50 = ema(closes, 20)[-1], ema(closes, 50)[-1]
        if e20 is None or e50 is None:
            return "unknown"
        verdicts.append(e20 > e50)
    if all(verdicts):
        return "uptrend"
    if not any(verdicts):
        return "downtrend"
    return "neutral"


def _underlying_price(client: OptionsClient, root: str) -> float | None:
    q = client.get_quote(root)
    quote = q.get("quote") if isinstance(q, dict) else None
    quote = quote or (q if isinstance(q, dict) else {})
    bid, ask = quote.get("bp"), quote.get("ap")
    if bid and ask:
        return (float(bid) + float(ask)) / 2.0
    return None


def _dte(expiration: date, today: date) -> int:
    return (expiration - today).days


def _leg_ok(quote: dict) -> bool:
    bid, ask = quote.get("bp"), quote.get("ap")
    if bid is None or ask is None:
        return False
    bid, ask = float(bid), float(ask)
    return bid > 0 and ask >= bid and (ask - bid) <= MAX_LEG_QUOTE_WIDTH


def _mid(quote: dict) -> float:
    return (float(quote["bp"]) + float(quote["ap"])) / 2.0


def pick_spread(client: OptionsClient, root: str, regime: str,
                today: date | None = None) -> tuple[dict | None, str]:
    """Select one bull put spread for `root`. Returns (proposal, "") or (None, reason)."""
    if regime == "downtrend":
        return None, "regime_downtrend"
    if regime == "unknown":
        return None, "regime_unknown"

    today = today or date.today()
    exp_gte = (today + timedelta(days=DTE_MIN)).isoformat()
    exp_lte = (today + timedelta(days=DTE_MAX)).isoformat()

    contracts = client.get_option_contracts(root, exp_gte, exp_lte, opt_type="put")
    if not contracts:
        return None, "no_contracts_in_dte_window"

    expirations = sorted({c["expiration_date"] for c in contracts})
    expiration_str = expirations[0]
    expiration = date.fromisoformat(expiration_str)
    dte = _dte(expiration, today)
    if not (DTE_MIN <= dte <= DTE_MAX):
        return None, f"dte_out_of_range:{dte}"

    contracts = [c for c in contracts if c["expiration_date"] == expiration_str]
    symbols = {c["symbol"] for c in contracts}
    strikes = {c["symbol"]: float(c["strike_price"]) for c in contracts}

    # Targeted batch fetch for this expiration only (full-chain is paginated
    # and starts at the front month — wrong window for us).
    chain = client.get_snapshots_by_symbols(sorted(symbols))
    available = {
        s: snap for s, snap in chain.items()
        if s in symbols and isinstance(snap, dict) and snap.get("latestQuote")
        and snap.get("greeks") and snap["greeks"].get("delta") is not None
    }
    if not available:
        return None, "no_chain_data"

    # --- short leg: |delta| closest to target within band ---
    short_candidates = []
    for sym, snap in available.items():
        delta = float(snap["greeks"]["delta"])
        ad = abs(delta)
        if DELTA_MIN <= ad <= DELTA_MAX and _leg_ok(snap["latestQuote"]):
            short_candidates.append((abs(ad - TARGET_DELTA), sym, delta))
    if not short_candidates:
        return None, "no_short_strike_in_delta_band"
    short_candidates.sort()
    _, short_sym, short_delta = short_candidates[0]
    short_strike = strikes[short_sym]

    # --- long leg: gap closest to WIDTH_TARGET within [GAP_MIN, GAP_MAX] ---
    long_candidates = []
    for sym, snap in available.items():
        strike = strikes[sym]
        gap = short_strike - strike
        if WIDTH_GAP_MIN <= gap <= WIDTH_GAP_MAX and _leg_ok(snap["latestQuote"]):
            long_candidates.append((abs(gap - WIDTH_TARGET), sym, gap))
    if not long_candidates:
        return None, "no_long_strike_near_target_width"
    long_candidates.sort()
    _, long_sym, width = long_candidates[0]
    long_strike = strikes[long_sym]

    short_q = available[short_sym]["latestQuote"]
    long_q = available[long_sym]["latestQuote"]
    credit_mid = round(_mid(short_q) - _mid(long_q), 4)
    combo_bid = round(float(short_q["bp"]) - float(long_q["ap"]), 4)
    combo_ask = round(float(short_q["ap"]) - float(long_q["bp"]), 4)
    # We are the seller: credit ≤ combo bid is immediately executable.
    # Price at the touch — selling for MORE than the market bid won't fill.
    limit_credit = round(combo_bid, 2)

    if credit_mid <= 0:
        return None, "non_positive_credit"
    if limit_credit < max(MIN_CREDIT_PCT * width, MIN_CREDIT_ABS):
        return None, (f"executable_credit_too_small:{limit_credit:.2f}"
                      f"/width{width:.0f} (mid {credit_mid:.2f})")
    if limit_credit >= width:
        return None, "credit_exceeds_width"

    price = _underlying_price(client, root)
    max_loss = round((width - limit_credit) * 100, 2)
    max_profit = round(limit_credit * 100, 2)

    proposal = {
        "root": root,
        "strategy": "bull_put_spread",
        "direction": "credit",
        "regime": regime,
        "expiration": expiration_str,
        "dte": dte,
        "qty": 1,
        "short_symbol": short_sym,
        "short_strike": short_strike,
        "short_delta": round(short_delta, 4),
        "long_symbol": long_sym,
        "long_strike": long_strike,
        "width": round(width, 2),
        "credit_mid": round(credit_mid, 2),
        "combo_bid": combo_bid,
        "combo_ask": combo_ask,
        "limit_credit": limit_credit,
        "credit_pct_width": round(limit_credit / width, 4),
        "max_profit": max_profit,
        "max_loss": max_loss,
        "underlying_price": round(price, 2) if price else None,
        "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return proposal, ""


def executable_credit(client: OptionsClient, proposal: dict) -> float | None:
    """Re-quote the combo right before placement (quotes move during the
    ≤5 min risk window). Returns the executable limit credit, or None when
    the fresh price is below the floor (skip the entry)."""
    try:
        snaps = client.get_snapshots_by_symbols(
            [proposal["short_symbol"], proposal["long_symbol"]])
        sq = snaps[proposal["short_symbol"]]["latestQuote"]
        lq = snaps[proposal["long_symbol"]]["latestQuote"]
        limit = round(float(sq["bp"]) - float(lq["ap"]), 2)
    except Exception:
        limit = proposal.get("limit_credit")
    floor = max(MIN_CREDIT_PCT * proposal["width"], MIN_CREDIT_ABS)
    if limit is None or limit <= 0 or limit < floor:
        return None
    return limit


def scan_candidates(client: OptionsClient, universe: tuple[str, ...],
                    regime: str) -> dict:
    """Scan each underlying; returns proposals + per-root rejection reasons."""
    proposals: list[dict] = []
    rejected: list[dict] = []
    for root in universe[:MAX_ROOTS_SCANNED]:
        proposal, reason = pick_spread(client, root, regime)
        if proposal:
            proposals.append(proposal)
        else:
            rejected.append({"root": root, "reason": reason})
    return {
        "regime": regime,
        "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "proposals": proposals,
        "rejected": rejected,
    }
