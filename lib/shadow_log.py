"""Shadow log — record Laya states for candidates we did NOT trade.

Why this exists
---------------
The history-based eval can only sample symbols the bot already held, which
(a) is survivorship-biased, (b) never records *selection* decisions (the
analyst's actual job), and (c) conditions the dataset on our own past risk
gates. This module logs the full candidate set every cycle — including the
symbols we skipped — so forward outcomes can later be joined on and the
resulting rows become an unbiased fine-tune / calibration dataset.

Layout
------
reports/shadow/YYYY-MM-DD.ndjson   one JSON object per line, append-only
reports/shadow_eval.json           scored output (metrics + per-case rows)

Each row stores the exact state handed to Laya plus its probabilities, the
sector context that history could never recover, whether we held the symbol,
and a schema version so old rows stay interpretable after prompts change.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Module-attribute access (not `from ... import`): under some import orders in
# this env (py3.14 + torch) a from-import can bind against a stale module object.
from lib import laya_eval as ev

SHADOW_SCHEMA_VERSION = 1
STATE_KEYS = (
    "rsi_14", "rsi_signal", "trend_signal", "atr_14", "vwap",
    "ema_9", "ema_20", "ema_50", "bb_position", "macd_hist", "volume_z",
)


def shadow_dir(reports_dir: Path) -> Path:
    d = reports_dir / "shadow"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _day_str(ts: "datetime | str") -> str:
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%d")


def append_cases(reports_dir: Path, rows: list[dict], header: dict | None = None) -> Path:
    """Append rows to the day's NDJSON shard. Returns the file path."""
    if not rows and not header:
        raise ValueError("nothing to append")
    day = _day_str(rows[0]["ts"]) if rows else _day_str(datetime.now(timezone.utc))
    path = shadow_dir(reports_dir) / f"{day}.ndjson"
    is_new = not path.exists()
    with path.open("a") as f:
        if is_new and header:
            f.write(json.dumps({"_header": header}, default=str) + "\n")
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")
    return path


def load_cases(reports_dir: Path, since: str | None = None) -> list[dict]:
    """Load shadow rows, newest last, de-duplicated on (ts, symbol)."""
    d = reports_dir / "shadow"
    if not d.exists():
        return []
    cases: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for f in sorted(d.glob("*.ndjson")):
        if since and f.name < since:
            continue
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "_header" in row or "ts" not in row:
                continue
            key = (row["ts"], row.get("symbol", ""))
            if key in seen:
                continue
            seen.add(key)
            row["_t"] = datetime.fromisoformat(row["ts"])
            cases.append(row)
    cases.sort(key=lambda r: r["_t"])
    return cases


def load_header(reports_dir: Path) -> dict:
    d = reports_dir / "shadow"
    for f in sorted(d.glob("*.ndjson"), reverse=True):
        for line in f.read_text().splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "_header" in row:
                    row["_file"] = f.name
                    return row["_header"]
    return {}


def build_row(symbol: str, ts: datetime, price: float, change_pct: float,
              indicators: dict, regime: str, sector: str, sector_exposure_pct: float,
              held: bool, signal: dict, backend: str,
              position_age_days: int | None = None) -> dict:
    """One shadow case. `signal` is classify_signal() output (may carry error)."""
    row = {
        "schema_version": SHADOW_SCHEMA_VERSION,
        "ts": ts.isoformat(),
        "symbol": symbol,
        "price": round(price, 4),
        "change_pct": round(change_pct, 3),
        "regime": regime,
        "sector": sector,
        "sector_exposure_pct": round(sector_exposure_pct, 2),
        "held": held,
        "position_age_days": position_age_days,
        "state": {k: (round(v, 4) if isinstance(v, float) else v)
                  for k, v in indicators.items() if k in STATE_KEYS},
        "action": signal.get("action"),
        "probabilities": signal.get("probabilities", {}),
        "conviction": signal.get("conviction"),
        "risk_reward": signal.get("risk_reward"),
        "backend": backend,
    }
    if signal.get("error"):
        row["laya_error"] = str(signal["error"])
    return row


# ------------------------------------------------------------------ scoring

def _fetch_bars(client, symbol: str, timeframe: str, start: str, end: str) -> list[dict]:
    raw = client.get_bars(symbol, timeframe=timeframe, limit=10000, start=start, end=end)
    out = []
    for b in raw:
        b = dict(b)
        b["_t"] = datetime.strptime(b["t"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        out.append(b)
    out.sort(key=lambda x: x["_t"])
    return out


def score_cases(cases: list[dict], client, horizon_hours: float, threshold_pct: float,
                use_laya: bool = False) -> dict:
    """Join forward outcomes onto shadow rows and score the logged probabilities.

    No model calls by default — the probabilities were captured at log time, so
    scoring is cheap and repeatable.
    """
    scored: list[dict] = []
    dropped = 0
    bars_cache: dict[str, list[dict]] = {}

    if cases:
        t_min = min(c["_t"] for c in cases)
        t_max = max(c["_t"] for c in cases)
        start = (t_min - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = (t_max + timedelta(hours=horizon_hours) + timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for sym in sorted({c["symbol"] for c in cases}):
            bars_cache[sym] = _fetch_bars(client, sym, "5Min", start, end)

    for c in cases:
        fwd, fwd_t = ev.forward_price(bars_cache.get(c["symbol"], []), c["_t"], horizon_hours)
        if fwd is None or not c.get("price"):
            dropped += 1
            continue
        ret = (fwd / float(c["price"]) - 1) * 100
        scored.append({
            "ts": c["ts"],
            "symbol": c["symbol"],
            "sector": c.get("sector"),
            "regime": c.get("regime"),
            "held": c.get("held"),
            "sector_exposure_pct": c.get("sector_exposure_pct"),
            "state": c.get("state", {}),
            "action": c.get("action"),
            "probabilities": c.get("probabilities", {}),
            "conviction": c.get("conviction"),
            "fwd_ret_pct": round(ret, 3),
            "fwd_time": fwd_t.isoformat() if fwd_t else None,
            "label": ev.label_of(ret, threshold_pct),
        })

    lrows = [r for r in scored if r.get("action")]
    m: dict = {
        "n_cases": len(scored),
        "n_unscorable": dropped,
        "n_with_action": len(lrows),
        "n_symbols": len({r["symbol"] for r in scored}),
        "label_dist": ev.dist(scored),
        "regime_dist": ev.dist(scored, "regime"),
        "sector_dist": ev.dist(scored, "sector"),
        "held_dist": ev.dist(scored, "held"),
    }
    if lrows:
        buy_pairs = [(float(r["probabilities"].get("buy", 0.0)), r["label"] == "up") for r in lrows]
        base_rate = sum(1 for _, y in buy_pairs if y) / len(buy_pairs) if buy_pairs else 0.0
        flat_acc = sum(1 for r in lrows if r["label"] == "flat") / len(lrows)
        m.update({
            "always_hold_accuracy": round(flat_acc, 4),
            "action_accuracy": round(sum(
                1 for r in lrows
                if (r["action"] == "buy" and r["label"] == "up")
                or (r["action"] == "sell" and r["label"] == "down")
                or (r["action"] == "hold" and r["label"] == "flat")
            ) / len(lrows), 4),
            "mean_fwd_ret_by_action": ev.mean_fwd(lrows, "action"),
            "confusion": ev.confusion(lrows, "action"),
            "brier_buy": ev.brier(buy_pairs),
            "brier_buy_baseline": ev.brier([(base_rate, y) for _, y in buy_pairs]),
            "ece_buy": ev.ece([(p, 1.0 if lab == "up" else 0.0) for p, lab in buy_pairs]),
            "signalability": ev.signalability(lrows, ["buy", "sell", "hold"]),
        })
        nonheld = [r for r in lrows if not r["held"]]
        if nonheld:
            m["signalability_nonheld_only"] = ev.signalability(nonheld, ["buy", "sell", "hold"])
            m["n_nonheld"] = len(nonheld)
    return {"metrics": m, "cases": scored}


def run_score(reports_dir: Path, client, horizon_hours: float, threshold_pct: float,
              since: str | None = None) -> dict:
    cases = load_cases(reports_dir, since=since)
    if not cases:
        raise SystemExit(f"no shadow cases found in {reports_dir}/shadow")
    result = score_cases(cases, client, horizon_hours, threshold_pct)
    header = load_header(reports_dir)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "horizon_hours": horizon_hours,
        "threshold_pct": threshold_pct,
        "n_logged_cases": len(cases),
        "log_header": header,
        "shadow_metrics": result["metrics"],
        "shadow_cases": result["cases"],
    }
    out_path = reports_dir / "shadow_eval.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    summary = {k: v for k, v in payload.items() if k != "shadow_cases"}
    summary["output"] = str(out_path)
    return summary
