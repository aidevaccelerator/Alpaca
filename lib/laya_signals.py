"""Laya signal classifier for trading decisions.

Uses laya (CPU, PyTorch) — runs anywhere, ~33ms per decision.

Usage:
    from lib.laya_signals import classify_signal, assess_risk, triage_symbol
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

LAYA_VENV = Path.home() / "laya_env"
LAYA_PYTHON = LAYA_VENV / "bin" / "python"
LAYA_CPU_MODEL = "convaiinnovations/laya"
TIMEOUT = 30


def _detect_backend() -> str:
    """Detect which laya backend is available."""
    # Try laya (CPU) in project venv first
    try:
        import laya as _laya  # noqa: F401
        return "laya_cpu"
    except ImportError:
        pass
    # Try laya-mlx in laya venv
    if LAYA_PYTHON.exists():
        return "laya_mlx"
    return "none"


BACKEND = _detect_backend()


def _run_laya_cpu(state: dict, questions: dict) -> dict:
    """Run laya CPU backend in-process."""
    try:
        import laya as _laya
        agent = _laya.load(LAYA_CPU_MODEL)
        result = agent.predict(state, questions)
        return result if isinstance(result, dict) else {"raw": str(result)}
    except Exception as e:
        return {"error": f"laya CPU error: {e}"}


def _run_laya_mlx(state: dict, questions: dict) -> dict:
    """Run laya-mlx via subprocess in laya venv."""
    if not LAYA_PYTHON.exists():
        return {"error": f"Laya venv not found at {LAYA_PYTHON}"}

    state_json = json.dumps(state)
    questions_json = json.dumps(questions)

    script = f"""
import json, laya_mlx as laya
agent = laya.load("aac6fef/laya-mlx")
state = json.loads({state_json!r})
questions = json.loads({questions_json!r})
result = agent.predict(state, questions)
print(json.dumps(result, default=str))
"""
    try:
        r = subprocess.run(
            [str(LAYA_PYTHON), "-c", script],
            capture_output=True, text=True, timeout=TIMEOUT,
        )
        if r.returncode != 0:
            return {"error": r.stderr.strip()[-500:]}
        return json.loads(r.stdout.strip().split("\n")[-1])
    except subprocess.TimeoutExpired:
        return {"error": "Laya inference timed out"}
    except Exception as e:
        return {"error": str(e)}


def _run_laya(state: dict, questions: dict) -> dict:
    """Dispatch to available backend."""
    if BACKEND == "laya_cpu":
        return _run_laya_cpu(state, questions)
    elif BACKEND == "laya_mlx":
        return _run_laya_mlx(state, questions)
    return {"error": "No laya backend available. Install 'laya' (CPU) or 'laya-mlx' (Apple Silicon)."}


def classify_signal(symbol: str, price: float, change_pct: float, indicators: dict,
                    regime: str = "unknown") -> dict:
    """Classify a trading signal for a symbol.

    Returns buy/sell/hold with conviction and probability breakdown.
    """
    state = {
        "symbol": symbol,
        "price": price,
        "change_pct": change_pct,
        "rsi": indicators.get("rsi_14"),
        "trend": indicators.get("trend_signal", "unknown"),
        "rsi_signal": indicators.get("rsi_signal", "unknown"),
        "atr": indicators.get("atr_14"),
        "vwap": indicators.get("vwap"),
        "ema_9": indicators.get("ema_9"),
        "ema_20": indicators.get("ema_20"),
        "bb_position": _bb_position(indicators),
        "regime": regime,
    }

    questions = {
        "action": {
            "type": "choice",
            "instructions": (
                "Given the technical indicators and market regime, what is the best action? "
                "Consider: trend direction (EMA alignment), momentum (RSI), volatility (ATR), "
                "price vs VWAP, Bollinger Band position, and broader market regime."
            ),
            "criteria": {
                "buy": "Uptrend confirmed, oversold or breakout, favorable risk/reward",
                "sell": "Downtrend confirmed, overbought or breakdown, stop triggered",
                "hold": "Mixed signals, no clear edge, or already positioned",
            },
        },
        "conviction": {
            "type": "score",
            "instructions": "How strong is the signal? Consider indicator alignment and regime clarity.",
            "criteria": ["weak", "moderate", "strong"],
        },
        "risk_reward": {
            "type": "score",
            "instructions": "Is the risk/reward favorable for this trade?",
            "criteria": ["poor", "acceptable", "excellent"],
        },
    }

    result = _run_laya(state, questions)
    if "error" in result:
        return result

    answers = result.get("answers", {})
    action = answers.get("action", {})
    conviction = answers.get("conviction", {})
    rr = answers.get("risk_reward", {})

    return {
        "symbol": symbol,
        "action": action.get("choice", "hold"),
        "probabilities": action.get("probabilities", {}),
        "conviction": conviction.get("legend", {}).get(str(int(conviction.get("score", 0))), "unknown"),
        "conviction_score": conviction.get("score"),
        "risk_reward": rr.get("legend", {}).get(str(int(rr.get("score", 0))), "unknown"),
        "risk_reward_score": rr.get("score"),
    }


def assess_risk(symbol: str, side: str, qty: int, entry_price: float,
                current_price: float, pnl_pct: float, position_age_days: int,
                market_regime: str = "unknown", sector_exposure_pct: float = 0) -> dict:
    """Fast risk assessment for an existing or proposed position.

    Returns approve/reject with risk score and reasoning.
    """
    state = {
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "entry_price": entry_price,
        "current_price": current_price,
        "pnl_pct": round(pnl_pct, 2),
        "position_age_days": position_age_days,
        "market_regime": market_regime,
        "sector_exposure_pct": round(sector_exposure_pct, 2),
        "drawdown_from_high": round(pnl_pct, 2) if pnl_pct < 0 else 0,
    }

    questions = {
        "decision": {
            "type": "choice",
            "instructions": (
                "Should this position be approved? Consider: P&L relative to stop (-5%) and "
                "target (+15%), market regime, sector concentration, and position age. "
                "Reject if loss exceeds stop or risk limits are breached."
            ),
            "criteria": {
                "approve": "Position within risk parameters, no stop breach, regime supportive",
                "reject": "Stop breached, excessive drawdown, sector overconcentration, or regime hostile",
                "tighten": "Position is borderline — move stop to breakeven or reduce size",
            },
        },
        "risk_score": {
            "type": "score",
            "instructions": "Overall risk level of this position right now?",
            "criteria": ["low", "medium", "high"],
        },
        "urgency": {
            "type": "score",
            "instructions": "How urgently does this position need attention?",
            "criteria": ["none", "soon", "immediate"],
        },
    }

    result = _run_laya(state, questions)
    if "error" in result:
        return result

    answers = result.get("answers", {})
    decision = answers.get("decision", {})
    risk = answers.get("risk_score", {})
    urgency = answers.get("urgency", {})

    return {
        "symbol": symbol,
        "decision": decision.get("choice", "reject"),
        "probabilities": decision.get("probabilities", {}),
        "risk_level": risk.get("legend", {}).get(str(int(risk.get("score", 0))), "unknown"),
        "urgency": urgency.get("legend", {}).get(str(int(urgency.get("score", 0))), "unknown"),
    }


def triage_symbol(symbol: str, sector: str, indicators: dict, regime: str) -> dict:
    """Triage a symbol to determine if it warrants deeper analysis.

    Returns a routing decision with priority score.
    """
    state = {
        "symbol": symbol,
        "sector": sector,
        "rsi": indicators.get("rsi_14"),
        "trend": indicators.get("trend_signal", "unknown"),
        "atr": indicators.get("atr_14"),
        "regime": regime,
    }

    questions = {
        "priority": {
            "type": "choice",
            "instructions": (
                "How should this symbol be prioritized for analysis? "
                "Consider trend strength, momentum extremes, and regime alignment."
            ),
            "criteria": {
                "high": "Strong signal, regime-aligned, potential trade setup",
                "medium": "Worth monitoring, developing setup",
                "low": "No edge, skip for now",
            },
        },
        "setup_type": {
            "type": "choice",
            "instructions": "What kind of setup does this look like?",
            "criteria": ["trend_following", "mean_reversion", "breakout", "none"],
        },
    }

    result = _run_laya(state, questions)
    if "error" in result:
        return result

    answers = result.get("answers", {})
    return {
        "symbol": symbol,
        "priority": answers.get("priority", {}).get("choice", "low"),
        "setup_type": answers.get("setup_type", {}).get("choice", "none"),
    }


def _bb_position(indicators: dict) -> str:
    """Determine position relative to Bollinger Bands."""
    price = indicators.get("latest_price")
    upper = indicators.get("bb_upper")
    lower = indicators.get("bb_lower")
    middle = indicators.get("bb_middle")
    if not all([price, upper, lower, middle]):
        return "unknown"
    assert isinstance(price, (int, float))
    assert isinstance(upper, (int, float))
    assert isinstance(lower, (int, float))
    assert isinstance(middle, (int, float))
    if price >= upper:
        return "above_upper"
    if price <= lower:
        return "below_lower"
    if price > middle:
        return "upper_half"
    return "lower_half"
