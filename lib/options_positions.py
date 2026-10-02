"""Parse option positions and group them into spreads (Bot #2)."""
from __future__ import annotations

from datetime import date


def _parse_occ(symbol: str) -> dict | None:
    import re
    m = re.match(r"^([A-Z]+?)(\d{6})([CP])(\d{8})$", symbol)
    if not m:
        return None
    return {
        "root": m.group(1),
        "yymmdd": m.group(2),
        "right": m.group(3),
        "strike": int(m.group(4)) / 1000.0,
    }


def _expiry_from_yymmdd(yymmdd: str) -> date | None:
    try:
        yy, mm, dd = int(yymmdd[0:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
        return date(2000 + yy, mm, dd)
    except (ValueError, IndexError):
        return None


def enrich_position(p: dict) -> dict | None:
    """Add OCC fields to an option position dict. None for non-option/unknown."""
    asset_class = p.get("asset_class")
    if asset_class is not None and asset_class != "us_option":
        return None
    parsed = _parse_occ(p.get("symbol", ""))
    if not parsed:
        return None
    qty = float(p.get("qty", 0))
    return {
        **p,
        **parsed,
        "qty_f": qty,
        "entry_price": float(p.get("avg_entry_price", 0) or 0),
        "current_price_f": float(p.get("current_price", 0) or 0),
        "unrealized_pl_f": float(p.get("unrealized_pl", 0) or 0),
        "expiry": _expiry_from_yymmdd(parsed["yymmdd"]),
    }


def group_spreads(positions: list[dict]) -> tuple[list[dict], list[dict]]:
    """Group option positions into managed spreads.

    Returns (spreads, unmanaged_legs).
    A managed spread = exactly one short put + one long put,
    same root and same expiration.

    Spread fields:
      root, expiration, dte, qty (contracts),
      short/long (enriched legs),
      credit (per share, from entry prices),
      current_debit (per share, mid-based),
      width, max_loss, max_profit, unrealized_pl, pnl_pct_of_credit
    """
    legs = [e for p in positions if (e := enrich_position(p))]
    buckets: dict[tuple[str, str], list[dict]] = {}
    for leg in legs:
        buckets.setdefault((leg["root"], leg["yymmdd"]), []).append(leg)

    spreads: list[dict] = []
    unmanaged: list[dict] = []
    today = date.today()
    for (root, yymmdd), group in buckets.items():
        shorts = [l for l in group if l["qty_f"] < 0 and l["right"] == "P"]
        longs = [l for l in group if l["qty_f"] > 0 and l["right"] == "P"]
        others = [l for l in group if l not in shorts and l not in longs]
        if len(shorts) == 1 and len(longs) == 1 and not others and len(group) == 2:
            short, long_ = shorts[0], longs[0]
            credit = short["entry_price"] - long_["entry_price"]
            current_debit = short["current_price_f"] - long_["current_price_f"]
            width = round(short["strike"] - long_["strike"], 2)
            qty = int(abs(short["qty_f"]))
            expiry = short.get("expiry")
            max_loss = round((width - credit) * 100 * qty, 2)
            max_profit = round(credit * 100 * qty, 2)
            # unrealized = (credit - current_debit) * 100 * qty
            unrealized = round((credit - current_debit) * 100 * qty, 2)
            spreads.append({
                "root": root,
                "expiration": expiry.isoformat() if expiry else None,
                "yymmdd": yymmdd,
                "dte": (expiry - today).days if expiry else None,
                "qty": qty,
                "short_symbol": short["symbol"],
                "short_strike": short["strike"],
                "short_qty": short["qty_f"],
                "long_symbol": long_["symbol"],
                "long_strike": long_["strike"],
                "long_qty": long_["qty_f"],
                "width": width,
                "credit": round(credit, 4),
                "current_debit": round(current_debit, 4),
                "max_loss": max_loss,
                "max_profit": max_profit,
                "unrealized_pl": unrealized,
                "pnl_pct_of_credit": round(unrealized / (credit * 100 * qty), 4) if credit > 0 else None,
            })
        else:
            unmanaged.extend(group)
    return spreads, unmanaged
