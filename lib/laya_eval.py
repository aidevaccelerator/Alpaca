"""Laya eval — replay logged states through Laya, score vs forward outcomes.

Builds cases from reports/history/*.json position snapshots, reconstructs the
state the analyst/risk agents would have seen at time T (indicators from
historical bars, regime from SPY EMA), replays Laya decisions, and scores them
against forward price returns over a horizon plus deterministic rule baselines.

Two tracks:
  risk    — assess_risk (approve/tighten/reject) on logged open positions
  signal  — classify_signal (buy/sell/hold) on reconstructed indicator states

Outputs a metrics summary (stdout) and full per-case rows to
reports/laya_eval.json — rows double as a seed dataset for future fine-tuning
(state inputs + probabilities + labels).

Limitations (known, documented):
- position age is approximated from first appearance in history (lower bound)
- sector_exposure_pct is unknown historically and passed as 0
- signal change_pct uses the snapshot's change_today, not (c-o)/o
- forward price = first 5Min bar at/after T+horizon (may span weekends)
"""
from __future__ import annotations

import json
import sys
from bisect import bisect_left
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lib.indicators import compute_all, ema
from lib.laya_signals import assess_risk, classify_signal, BACKEND

HORIZON_HOURS_DEFAULT = 4.0
THRESHOLD_PCT_DEFAULT = 0.5
DAILY_LOOKAHEAD_H = 16  # daily bar closes at 20:00 UTC; bar_t + 16h <= T
RISK_DECISIONS = ("approve", "tighten", "reject")
SIGNAL_ACTIONS = ("buy", "sell", "hold")


# ---------------------------------------------------------------- case build

def _parse_bar_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def load_snapshots(reports_dir: Path) -> list[dict]:
    hist = reports_dir / "history"
    snaps: list[dict] = []
    for f in sorted(hist.glob("*.json")):
        try:
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, list):
            continue
        for s in data:
            try:
                s["_t"] = datetime.fromisoformat(s["timestamp"])
            except (KeyError, ValueError):
                continue
            snaps.append(s)
    snaps.sort(key=lambda s: s["_t"])
    return snaps


def build_risk_cases(snaps: list[dict], horizon_hours: float) -> list[dict]:
    spacing = timedelta(hours=horizon_hours)
    last_seen: dict[tuple, datetime] = {}
    first_seen: dict[tuple, datetime] = {}
    cases: list[dict] = []
    for s in snaps:
        t = s["_t"]
        for p in s.get("positions", []):
            try:
                entry = float(p["avg_entry_price"])
                cur = float(p["current_price"])
                qty = float(p["qty"])
                side = p.get("side", "long")
                sym = p["symbol"]
            except (KeyError, ValueError, TypeError):
                continue
            if cur <= 0:
                continue
            key = (sym, side, entry)
            if key not in first_seen:
                first_seen[key] = t
            prev = last_seen.get(key)
            if prev is not None and t - prev < spacing:
                continue
            last_seen[key] = t
            cases.append({
                "t": t,
                "symbol": sym,
                "side": side,
                "qty": int(qty) if qty == int(qty) else qty,
                "entry_price": entry,
                "current_price": cur,
                "pnl_pct": float(p.get("unrealized_plpc") or 0) * 100,
                "age_days": max((t - first_seen[key]).days, 0),
                "change_pct": float(p.get("change_today") or 0) * 100,
                "lineage": f"{sym}@{entry}:{side}",
            })
    cases.sort(key=lambda c: c["t"])
    return cases


def build_signal_cases(snaps: list[dict], horizon_hours: float) -> list[dict]:
    spacing = timedelta(hours=horizon_hours)
    last_by_sym: dict[str, datetime] = {}
    cases: list[dict] = []
    for s in snaps:
        t = s["_t"]
        for p in s.get("positions", []):
            try:
                cur = float(p["current_price"])
                sym = p["symbol"]
            except (KeyError, ValueError, TypeError):
                continue
            if cur <= 0:
                continue
            prev = last_by_sym.get(sym)
            if prev is not None and t - prev < spacing:
                continue
            last_by_sym[sym] = t
            cases.append({
                "t": t,
                "symbol": sym,
                "current_price": cur,
                "change_pct": float(p.get("change_today") or 0) * 100,
            })
    cases.sort(key=lambda c: c["t"])
    return cases


# ---------------------------------------------------------------- market data

def _fetch_bars(client, symbol: str, timeframe: str, start: str, end: str) -> list[dict]:
    raw = client.get_bars(symbol, timeframe=timeframe, limit=10000, start=start, end=end)
    out = []
    for b in raw:
        b = dict(b)
        b["_t"] = _parse_bar_ts(b["t"])
        out.append(b)
    out.sort(key=lambda x: x["_t"])
    return out


def fetch_market(client, cases: list[dict], horizon_hours: float) -> dict | None:
    all_ts = [c["t"] for c in cases]
    if not all_ts:
        return None
    t_min, t_max = min(all_ts), max(all_ts)
    start5 = (t_min - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end5 = (t_max + timedelta(hours=horizon_hours) + timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
    start_d = (t_min - timedelta(days=150)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_d = (t_max + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    symbols = sorted({c["symbol"] for c in cases})
    market: dict = {"bars5": {}, "daily": {}}
    for sym in symbols:
        market["bars5"][sym] = _fetch_bars(client, sym, "5Min", start5, end5)
        market["daily"][sym] = _fetch_bars(client, sym, "1Day", start_d, end_d)
    market["spy_daily"] = _fetch_bars(client, "SPY", "1Day", start_d, end_d)
    return market


# ---------------------------------------------------------------- slicing

def _slice_le(bars: list[dict], cutoff: datetime, n: int) -> list[dict]:
    """Last n bars with _t <= cutoff (assumes sorted ascending)."""
    idx = bisect_left([b["_t"] for b in bars], cutoff + timedelta(microseconds=1), hi=len(bars))
    return bars[max(0, idx - n):idx]


def bars5_at(bars5: list[dict], t: datetime) -> list[dict]:
    # completed bars only: bar closes at _t + 5min
    return _slice_le(bars5, t - timedelta(minutes=5), 50)


def daily_at(daily: list[dict], t: datetime) -> list[dict]:
    # no lookahead: include day-D daily bar only once its close is known
    return _slice_le(daily, t - timedelta(hours=DAILY_LOOKAHEAD_H), 50)


def regime_at(spy_daily: list[dict], t: datetime) -> str:
    bars = daily_at(spy_daily, t)
    if len(bars) < 50:
        return "unknown"
    closes = [b["c"] for b in bars]
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    if e20[-1] is None or e50[-1] is None:
        return "unknown"
    return "uptrend" if e20[-1] > e50[-1] else "downtrend"


def forward_price(bars5: list[dict], t: datetime, horizon_hours: float):
    if not bars5:
        return None, None
    target = t + timedelta(hours=horizon_hours)
    times = [b["_t"] for b in bars5]
    idx = bisect_left(times, target)
    if idx >= len(bars5):
        return None, None
    return bars5[idx]["c"], bars5[idx]["_t"]


# ---------------------------------------------------------------- rule baselines

def rule_risk_decision(pnl_pct: float) -> str:
    if pnl_pct <= -5:
        return "reject"
    if pnl_pct <= -2 or pnl_pct >= 15:
        return "tighten"
    return "approve"


def rule_signal_action(ind: dict) -> str:
    rsi = ind.get("rsi_14")
    trend = ind.get("trend_signal")
    if rsi is None or trend in (None, "unknown"):
        return "hold"
    if rsi >= 75:
        return "sell"
    if rsi <= 25:
        return "buy"
    if trend in ("uptrend", "strong_uptrend") and rsi <= 70:
        return "buy"
    if trend in ("downtrend", "strong_downtrend") and rsi >= 30:
        return "sell"
    return "hold"


# ---------------------------------------------------------------- labels/metrics

def label_of(ret_pct: float, threshold_pct: float) -> str:
    if ret_pct > threshold_pct:
        return "up"
    if ret_pct < -threshold_pct:
        return "down"
    return "flat"


def risk_correct(decision: str, label: str) -> bool:
    if decision == "approve":
        return label in ("up", "flat")
    if decision == "tighten":
        return label == "flat"
    if decision == "reject":
        return label == "down"
    return False


def risk_binary_correct(decision: str, label: str):
    if decision == "approve":
        return label != "down"
    if decision == "reject":
        return label == "down"
    return None  # tighten excluded


def signal_correct(action: str, label: str) -> bool:
    return {"buy": label == "up", "sell": label == "down", "hold": label == "flat"}.get(action, False)


def signal_tradeable_correct(action: str, label: str):
    if action == "buy":
        return label == "up"
    if action == "sell":
        return label == "down"
    return None  # hold excluded


def _accuracy(rows: list[dict], dkey: str, fn) -> float | None:
    scored = [(r[dkey], r["label"]) for r in rows if fn(r[dkey], r["label"]) is not None]
    if not scored:
        return None
    return sum(1 for d, lab in scored if fn(d, lab)) / len(scored)


def _best_constant(rows: list[dict], decisions: tuple, fn) -> dict | None:
    best = None
    for d in decisions:
        if not rows:
            continue
        n = sum(1 for r in rows if fn(d, r["label"]) is not None)
        correct = sum(1 for r in rows if fn(d, r["label"]) is True)
        acc = correct / n if n else None
        if acc is not None and (best is None or acc > best["accuracy"]):
            best = {"decision": d, "accuracy": acc}
    return best


def _brier(pairs: list[tuple[float, bool]]) -> float | None:
    if not pairs:
        return None
    return sum((p - (1.0 if y else 0.0)) ** 2 for p, y in pairs) / len(pairs)


def _ece(pairs: list[tuple[float, float | bool]]) -> float | None:
    if len(pairs) < 6:
        return None
    try:
        import numpy as np
        import laya
        confs = np.array([p for p, _ in pairs])
        correct = np.array([1 if c else 0 for _, c in pairs])
        bins = max(3, min(15, len(pairs) // 4))
        return float(laya.ece_score(confs, correct, bins=bins))
    except Exception:
        return None


def _mean_fwd(rows: list[dict], dkey: str) -> dict:
    groups: dict[str, list[float]] = {}
    for r in rows:
        groups.setdefault(r[dkey], []).append(r["fwd_ret_pct"])
    return {
        k: {"n": len(v), "mean_fwd_ret_pct": round(sum(v) / len(v), 3)}
        for k, v in sorted(groups.items())
    }


def _confusion(rows: list[dict], dkey: str) -> dict:
    cm: dict[str, dict[str, int]] = {}
    for r in rows:
        cm.setdefault(r[dkey], {})
        cm[r[dkey]][r["label"]] = cm[r[dkey]].get(r["label"], 0) + 1
    return cm


def _dist(rows: list[dict], key: str = "label") -> dict:
    out: dict[str, int] = {}
    for r in rows:
        out[r[key]] = out.get(r[key], 0) + 1
    return out


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation — detects ordering skill that accuracy cannot see."""
    if len(xs) < 3 or len(xs) != len(ys):
        return None

    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return round(num / den, 4) if den else None


def auc(pairs: list[tuple[float, bool]]) -> float | None:
    """Probability a random positive outranks a random negative (0.5 = chance)."""
    pos = [s for s, y in pairs if y]
    neg = [s for s, y in pairs if not y]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 4)


def signalability(rows: list[dict], keys: list[str], flat_label: str = "flat") -> dict:
    """Skill metrics that survive a dominant flat class.

    ic_*    — rank correlation of each probability against forward return
    auc_*   — up-vs-down ranking accuracy on non-flat rows (chance = 0.5), i.e.
              does a higher p_buy rank "up" cases above "down" cases? For
              p_sell the informative reading is 1 - auc.
    """
    out: dict = {}
    fwd_key = "fwd_ret_pct"
    if rows and fwd_key in rows[0]:
        fwd = [float(r[fwd_key]) for r in rows]
        for k in keys:
            vals = [float(r.get("probabilities", {}).get(k, 0.0)) for r in rows]
            out[f"ic_{k}_vs_fwd"] = spearman(vals, fwd)
    directional = [r for r in rows if r["label"] != flat_label]
    for k in keys:
        sub = [(float(r.get("probabilities", {}).get(k, 0.0)), r["label"] == "up")
               for r in directional]
        out[f"auc_{k}_vs_up"] = auc(sub)
    out["n_excl_flat"] = len(directional)
    out["base_rate_up_excl_flat"] = (
        round(sum(1 for r in directional if r["label"] == "up") / len(directional), 4)
        if directional else None
    )
    return out


# ---------------------------------------------------------------- tracks

def eval_risk(cases: list[dict], market: dict, use_laya: bool, threshold_pct: float,
              horizon_hours: float, max_cases: int | None) -> dict:
    if max_cases:
        cases = cases[:max_cases]
    rows: list[dict] = []
    dropped = 0
    errors = 0
    spy = market["spy_daily"]
    for c in cases:
        bars5 = market["bars5"].get(c["symbol"], [])
        fwd, fwd_t = forward_price(bars5, c["t"], horizon_hours)
        if fwd is None:
            dropped += 1
            continue
        if c["side"] == "short":
            ret = (c["current_price"] / fwd - 1) * 100
        else:
            ret = (fwd / c["current_price"] - 1) * 100
        regime = regime_at(spy, c["t"])
        row = {
            "t": c["t"].isoformat(),
            "symbol": c["symbol"],
            "side": c["side"],
            "qty": c["qty"],
            "entry_price": c["entry_price"],
            "current_price": c["current_price"],
            "pnl_pct": round(c["pnl_pct"], 3),
            "age_days": c["age_days"],
            "regime": regime,
            "lineage": c["lineage"],
            "fwd_ret_pct": round(ret, 3),
            "fwd_time": fwd_t.isoformat() if fwd_t else None,
            "label": label_of(ret, threshold_pct),
            "rule_decision": rule_risk_decision(c["pnl_pct"]),
        }
        if use_laya:
            res = assess_risk(
                symbol=c["symbol"], side=c["side"], qty=c["qty"],
                entry_price=c["entry_price"], current_price=c["current_price"],
                pnl_pct=c["pnl_pct"], position_age_days=c["age_days"],
                market_regime=regime, sector_exposure_pct=0,
            )
            if "error" in res or "decision" not in res:
                errors += 1
                row["laya_error"] = str(res.get("error", "no decision"))
            else:
                row["laya_decision"] = res["decision"]
                row["probabilities"] = res.get("probabilities", {})
        rows.append(row)

    lrows = [r for r in rows if "laya_decision" in r]
    m: dict = {
        "n_cases": len(rows),
        "n_scored": len(lrows),
        "n_dropped_no_forward": dropped,
        "n_laya_errors": errors,
        "label_dist": _dist(rows),
    }
    if lrows:
        binary = [r for r in lrows if r["laya_decision"] != "tighten"]
        rej_pairs = [
            (float(r["probabilities"].get("reject", 0.0)), r["label"] == "down")
            for r in lrows
        ]
        base_rate = sum(1 for _, y in rej_pairs if y) / len(rej_pairs) if rej_pairs else 0.0
        m.update({
            "accuracy": _accuracy(lrows, "laya_decision", risk_correct),
            "best_constant_baseline": _best_constant(lrows, RISK_DECISIONS, risk_correct),
            "binary_accuracy_excl_tighten": _accuracy(binary, "laya_decision", risk_binary_correct),
            "laya_rule_agreement": sum(
                1 for r in lrows if r["laya_decision"] == r["rule_decision"]
            ) / len(lrows),
            "brier_reject": _brier(rej_pairs),
            "brier_reject_baseline": _brier([(base_rate, y) for _, y in rej_pairs]),
            "ece": _ece([(p, 1.0 if lab == "down" else 0.0) for p, lab in rej_pairs]),
            "mean_fwd_ret_by_laya_decision": _mean_fwd(lrows, "laya_decision"),
            "confusion_laya": _confusion(lrows, "laya_decision"),
            "signalability": signalability(lrows, ["approve", "reject", "tighten"]),
        })
    if rows:
        m.update({
            "rule_accuracy": _accuracy(rows, "rule_decision", risk_correct),
            "mean_fwd_ret_by_rule_decision": _mean_fwd(rows, "rule_decision"),
        })
    return {"metrics": m, "cases": rows}


def eval_signal(cases: list[dict], market: dict, use_laya: bool, threshold_pct: float,
                horizon_hours: float, max_cases: int | None) -> dict:
    if max_cases:
        cases = cases[:max_cases]
    rows: list[dict] = []
    dropped = 0
    errors = 0
    spy = market["spy_daily"]
    for c in cases:
        bars5 = market["bars5"].get(c["symbol"], [])
        fwd, fwd_t = forward_price(bars5, c["t"], horizon_hours)
        if fwd is None:
            dropped += 1
            continue
        ret = (fwd / c["current_price"] - 1) * 100
        b5 = bars5_at(bars5, c["t"])
        if len(b5) < 50:
            dropped += 1
            continue
        daily = daily_at(market["daily"].get(c["symbol"], []), c["t"])
        ind = compute_all(b5, daily)
        regime = regime_at(spy, c["t"])
        row = {
            "t": c["t"].isoformat(),
            "symbol": c["symbol"],
            "current_price": c["current_price"],
            "change_pct": round(c["change_pct"], 3),
            "regime": regime,
            "indicators": {
                k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in ind.items()
                if k in ("rsi_14", "trend_signal", "ema_20", "ema_50", "macd_hist",
                         "volume_z", "daily_regime")
            },
            "fwd_ret_pct": round(ret, 3),
            "fwd_time": fwd_t.isoformat() if fwd_t else None,
            "label": label_of(ret, threshold_pct),
            "rule_action": rule_signal_action(ind),
        }
        if use_laya:
            res = classify_signal(
                symbol=c["symbol"], price=c["current_price"],
                change_pct=c["change_pct"], indicators=ind, regime=regime,
            )
            if "error" in res or "action" not in res:
                errors += 1
                row["laya_error"] = str(res.get("error", "no action"))
            else:
                row["laya_decision"] = res["action"]
                row["probabilities"] = res.get("probabilities", {})
                row["conviction"] = res.get("conviction")
        rows.append(row)

    lrows = [r for r in rows if "laya_decision" in r]
    m: dict = {
        "n_cases": len(rows),
        "n_scored": len(lrows),
        "n_dropped_no_forward": dropped,
        "n_laya_errors": errors,
        "label_dist": _dist(rows),
    }
    if lrows:
        tradeable = [r for r in lrows if r["laya_decision"] != "hold"]
        buy_pairs = [
            (float(r["probabilities"].get("buy", 0.0)), r["label"] == "up")
            for r in lrows
        ]
        base_rate = sum(1 for _, y in buy_pairs if y) / len(buy_pairs) if buy_pairs else 0.0
        m.update({
            "accuracy": _accuracy(lrows, "laya_decision", signal_correct),
            "best_constant_baseline": _best_constant(lrows, SIGNAL_ACTIONS, signal_correct),
            "tradeable_accuracy_excl_hold": _accuracy(tradeable, "laya_decision", signal_tradeable_correct),
            "laya_rule_agreement": sum(
                1 for r in lrows if r["laya_decision"] == r["rule_action"]
            ) / len(lrows),
            "brier_buy": _brier(buy_pairs),
            "brier_buy_baseline": _brier([(base_rate, y) for _, y in buy_pairs]),
            "ece": _ece([(p, 1.0 if lab == "up" else 0.0) for p, lab in buy_pairs]),
            "mean_fwd_ret_by_laya_decision": _mean_fwd(lrows, "laya_decision"),
            "confusion_laya": _confusion(lrows, "laya_decision"),
            "signalability": signalability(lrows, ["buy", "sell", "hold"]),
        })
    if rows:
        m.update({
            "rule_accuracy": _accuracy(rows, "rule_action", signal_correct),
            "mean_fwd_ret_by_rule_decision": _mean_fwd(rows, "rule_action"),
        })
    return {"metrics": m, "cases": rows}


# ---------------------------------------------------------------- entrypoint

def run_eval(mode: str, client, reports_dir: Path, horizon_hours: float,
             threshold_pct: float, max_cases: int | None, use_laya: bool,
             log=print) -> dict:
    if use_laya and BACKEND == "none":
        raise SystemExit("Laya backend unavailable (BACKEND=none) — rerun with --no-laya")

    snaps = load_snapshots(reports_dir)
    if not snaps:
        raise SystemExit(f"no snapshots found in {reports_dir}/history")

    risk_cases = build_risk_cases(snaps, horizon_hours) if mode in ("risk", "all") else []
    signal_cases = build_signal_cases(snaps, horizon_hours) if mode in ("signal", "all") else []
    all_cases = risk_cases + signal_cases
    market = fetch_market(client, all_cases, horizon_hours)
    if market is None:
        raise SystemExit("no eval cases could be built from history")

    summary: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "backend": BACKEND if use_laya else "none",
        "mode": mode,
        "horizon_hours": horizon_hours,
        "threshold_pct": threshold_pct,
        "n_snapshots": len(snaps),
        "n_risk_candidates": len(risk_cases),
        "n_signal_candidates": len(signal_cases),
    }

    if mode in ("risk", "all"):
        log(f"[eval] risk: {len(risk_cases)} candidates", file=sys.stderr)
        out = eval_risk(risk_cases, market, use_laya, threshold_pct, horizon_hours, max_cases)
        summary["risk"] = out["metrics"]
        summary.setdefault("_cases", {})["risk"] = out["cases"]

    if mode in ("signal", "all"):
        log(f"[eval] signal: {len(signal_cases)} candidates", file=sys.stderr)
        out = eval_signal(signal_cases, market, use_laya, threshold_pct, horizon_hours, max_cases)
        summary["signal"] = out["metrics"]
        summary.setdefault("_cases", {})["signal"] = out["cases"]

    out_path = reports_dir / "laya_eval.json"
    payload = dict(summary)
    payload["cases"] = payload.pop("_cases", {})
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    summary["output"] = str(out_path)
    return summary


# Public aliases — other modules (shadow_log) reuse these metrics.
brier = _brier
ece = _ece
dist = _dist
confusion = _confusion
mean_fwd = _mean_fwd
