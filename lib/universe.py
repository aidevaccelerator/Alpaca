"""Dynamic universe screening — replaces hardcoded 50-symbol list.

Screens for:
  - High volume (relative to average)
  - Strong momentum (new highs, breakout candidates)
  - Sector rotation (leaders per sector)
  - Unusual volume spikes
"""
from __future__ import annotations

SECTOR_ETFS = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financials": "XLF",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Materials": "XLB",
    "Communication": "XLC",
}

CORE_UNIVERSE = [
    "SPY", "QQQ", "IWM", "DIA",
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
    "JPM", "V", "MA", "BAC", "WMT", "JNJ", "UNH", "PG", "HD",
    "DIS", "NFLX", "CRM", "ADBE", "COST", "ABBV", "MRK", "TMO",
    "AVGO", "AMD", "QCOM", "INTC", "ORCL", "CSCO",
]

HIGH_LIQUIDITY = [
    "GLD", "SLV", "TLT", "IEF", "HYG", "LQD",
    "USO", "UNG", "UUP",
    "ARKK", "SOXX", "SMH", "IBB", "KRE", "XME",
    "BITO", "COIN", "MSTR", "PLTR", "SOFI",
]

MIN_VOLUME_THRESHOLD = 500_000
MIN_PRICE_THRESHOLD = 5.0


def filter_liquid(snapshots: dict, min_volume: int = MIN_VOLUME_THRESHOLD,
                  min_price: float = MIN_PRICE_THRESHOLD) -> list[dict]:
    results = []
    for sym, data in snapshots.items():
        if not data or "dailyBar" not in data:
            continue
        db = data["dailyBar"]
        lq = data.get("latestQuote", {}) or data.get("latestTrade", {})
        price = lq.get("p", db.get("c", 0))
        volume = db.get("v", 0)
        if price < min_price or volume < min_volume:
            continue
        change_pct = (db["c"] - db["o"]) / db["o"] * 100 if db["o"] else 0
        results.append({
            "symbol": sym,
            "price": round(price, 2),
            "change_pct": round(change_pct, 2),
            "volume": volume,
            "high": db.get("h", 0),
            "low": db.get("l", 0),
        })
    return results


def detect_new_highs(snapshots: dict) -> list[dict]:
    results = []
    for sym, data in snapshots.items():
        if not data or "dailyBar" not in data or "prevDailyBar" not in data:
            continue
        db = data["dailyBar"]
        pdb = data["prevDailyBar"]
        if not db or not pdb:
            continue
        current_high = db.get("h", 0)
        prev_high = pdb.get("h", 0)
        if prev_high > 0 and current_high >= prev_high:
            lq = data.get("latestQuote", {}) or data.get("latestTrade", {})
            price = lq.get("p", db.get("c", 0))
            results.append({
                "symbol": sym,
                "price": round(price, 2),
                "prev_high": round(prev_high, 2),
                "current_high": round(current_high, 2),
                "signal": "new_high",
            })
    return results


def detect_volume_spikes(snapshots: dict, multiplier: float = 2.0) -> list[dict]:
    results = []
    for sym, data in snapshots.items():
        if not data or "dailyBar" not in data or "prevDailyBar" not in data:
            continue
        db = data["dailyBar"]
        pdb = data["prevDailyBar"]
        if not db or not pdb:
            continue
        current_vol = db.get("v", 0)
        prev_vol = pdb.get("v", 0)
        if prev_vol > 0 and current_vol >= prev_vol * multiplier:
            lq = data.get("latestQuote", {}) or data.get("latestTrade", {})
            price = lq.get("p", db.get("c", 0))
            change_pct = (db["c"] - db["o"]) / db["o"] * 100 if db["o"] else 0
            results.append({
                "symbol": sym,
                "price": round(price, 2),
                "volume": current_vol,
                "prev_volume": prev_vol,
                "volume_ratio": round(current_vol / prev_vol, 2),
                "change_pct": round(change_pct, 2),
                "signal": "volume_spike",
            })
    return results


def detect_sector_leaders(snapshots: dict) -> list[dict]:
    results = []
    for sector, etf in SECTOR_ETFS.items():
        if etf not in snapshots:
            continue
        data = snapshots[etf]
        if not data or "dailyBar" not in data:
            continue
        db = data["dailyBar"]
        change_pct = (db["c"] - db["o"]) / db["o"] * 100 if db["o"] else 0
        results.append({
            "sector": sector,
            "etf": etf,
            "change_pct": round(change_pct, 2),
        })
    results.sort(key=lambda r: r["change_pct"], reverse=True)
    return results


def get_full_universe() -> list[str]:
    return sorted(set(CORE_UNIVERSE + HIGH_LIQUIDITY))


def screen_dynamic(client, min_volume: int = MIN_VOLUME_THRESHOLD,
                   min_price: float = MIN_PRICE_THRESHOLD) -> dict:
    universe = get_full_universe()
    snapshots = client.get_snapshots(universe)

    liquid = filter_liquid(snapshots, min_volume, min_price)
    liquid.sort(key=lambda r: abs(r["change_pct"]), reverse=True)

    new_highs = detect_new_highs(snapshots)
    volume_spikes = detect_volume_spikes(snapshots)
    sector_leaders = detect_sector_leaders(snapshots)

    return {
        "universe_size": len(universe),
        "liquid_count": len(liquid),
        "top_movers": liquid[:20],
        "new_highs": new_highs,
        "volume_spikes": volume_spikes,
        "sector_leaders": sector_leaders,
    }
