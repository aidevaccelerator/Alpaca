# Alpaca Trader Bot — Agent Orchestration

## Roles
See `agents/<role>-agent.md` for full instructions.

## Pipeline (each cycle)
```
1. bash cycle.sh         → write reports/ (account, clock, positions, open_orders)
                           → skip if market closed (use FORCE_CYCLE=1 to override)
                           → write daily snapshot to reports/history/
                           → shadow-log candidate states → reports/shadow/ (never trades)
2. ANALYST               → read reports/, check regime (SPY/QQQ trend),
                           triage symbols with Laya AI (research triage),
                           compute indicators, classify signals with Laya AI (research signals),
                           write proposals to reports/analyst.json
3. ORCHESTRATOR (me)     → read proposals (exits + entries), forward both to risk
4. RISK                  → validate ALL (exits + entries) with sector/volatility checks,
                           fast-path risk assessment with Laya AI (research assess),
                           write to reports/risk.json (with approved_at timestamps)
5. ORCHESTRATOR          → read risk decision
   - REJECTED → log, skip
   - STALE (approved > 1 cycle ago) → reject, re-run risk
   - APPROVED exits → trader sells first
   - APPROVED entries → trader buys after exits
6. TRADER (via Python)   → execute approved exits, then entries
                           uses bracket orders when stop/target provided
                           polls for fill confirmation (60s timeout)
7. PORTFOLIO             → confirm fills, update snapshot
8. Sleep 60s → repeat
```

## Laya AI Integration
Laya-MLX runs locally on Apple Silicon (~30ms/decision) as a signal classifier and risk gatekeeper.

| Command | Used by | Purpose |
|---------|---------|---------|
| `research triage <sym>` | Analyst | Prioritize symbols for analysis (high/medium/low) |
| `research signals <sym>` | Analyst | Classify buy/sell/hold with conviction + risk/reward |
| `research assess <sym> --pnl PCT` | Risk | Fast approve/reject/tighten for positions |
| `research shadow-log [--top N]` | cycle.sh | Log states for candidates incl. ones we skip |
| `research shadow-score` | Orchestrator | Score logged shadow cases vs forward returns |
| `research eval [risk\|signal\|all]` | Orchestrator | Replay Laya on position history + baselines |

Laya is advisory — it provides a second opinion alongside indicator analysis. The LLM agent makes the final decision, but Laya's "reject with immediate urgency" overrides other signals.

### Laya status: shadow mode (measured, not assumed)
`research eval all` (121 scored cases, 2026-09-22 → 10-02) found **no signal**:
risk track approved 118/121 (accuracy 0.744 vs 0.769 for constant "always-approve"),
signal track never held (accuracy 0.231 vs 0.529 for constant "always-hold"),
IC(p, forward return) 0.07–0.13, Brier worse than base rate on both tracks.
Every case was a single `uptrend` regime, so down-market behaviour is untested.

Laya is therefore **log-only** and must not block trading until it clears the
promotion gate on held-out time:
- ≥300 scored cases across ≥2 distinct market regimes
- AUC ≥ 0.60 *in the direction the probability claims* (p_buy → up,
  p_reject → down; i.e. `auc_buy_vs_up` ≥ 0.60 and `1 - auc_reject_vs_up` ≥ 0.60)
- IC ≥ 0.15 versus the constant baseline
- ECE materially better than the base-rate Brier

Current: `auc_buy_vs_up` 0.48, `1 - auc_reject_vs_up` 0.37, IC 0.07–0.13 — at or
below chance in the claimed directions. Re-check with `research eval all`.

Data collection continues meanwhile — `cycle.sh` shadow-logs every cycle.

## Communication
- All IPC via `reports/*.json` files
- Daily history in `reports/history/YYYY-MM-DD.json`
- The orchestrator (me) reads/writes summaries as needed
- Agents never submit orders directly — trader calls `python3 lib/execute_order.py`

## Key Files
| File | Purpose |
|------|---------|
| `reports/account.json` | Account state (equity, buying power) |
| `reports/clock.json` | Market open/close status |
| `reports/positions.json` | All open positions |
| `reports/open_orders.json` | Pending orders |
| `reports/analyst.json` | Trade proposals (includes laya_signal, laya_conviction) |
| `reports/risk.json` | Risk decisions |
| `reports/trader.json` | Execution log |
| `reports/laya_eval.json` | Laya eval: per-case rows (fine-tune seed) + metrics |
| `reports/shadow/YYYY-MM-DD.ndjson` | Per-cycle candidate states (incl. skipped symbols) |
| `reports/shadow_eval.json` | Shadow cases joined with forward outcomes + metrics |
| `reports/history/` | Daily snapshots |

## Critical Rules
- No live orders without risk approval
- Paper trading default (set ALPACA_LIVE_TRADE=true for live)
- Log every cycle
- Bracket orders mandatory when stop/target prices provided
- Risk decisions expire after 1 cycle (must be fresh)
- Sector allocation capped at 25% per sector

---

## Bot #2 — Options Income (rule-based, separate paper account)

Bull put credit spreads on SPY/QQQ/IWM. **No LLM analyst** — deterministic rules;
Laya provides a veto/advisory gate only. Separate credentials
(`ALPACA_OPTIONS_API_KEY` / `ALPACA_OPTIONS_SECRET_KEY`), separate reports namespace.

### Cycle (runs inside the same GitHub Actions workflow, after Bot #1)
```
python bin/options snapshot   → reports/options_{account,clock,positions,open_orders}.json
                                + append reports/options_history/YYYY-MM-DD.json
python bin/options manage     → exits first: 50% profit | 2x credit stop | 21 DTE | Laya urgent
                                → reports/options_manage.json
python bin/options scan       → once per day (ET), regime gate + strategy rules + Laya veto
                                → reports/options_proposals.json + options_state.json
python bin/options enter      → code-enforced risk (lib/options_risk.py) → mleg order
                                → reports/options_risk.json + options_trader.json
```

### Entry rules (lib/options_strategy.py)
- Blocked only when SPY **and** QQQ are below their 50-day EMA (flat/mixed = fine)
- Nearest expiration 30–45 DTE, short put |delta| ≈ 0.20 (band 0.15–0.30)
- Long put ~$2 lower; entry priced at executable credit (combo bid + $0.01) with floor ≥ max(10% of width, $0.20); both legs liquid
- Max 1 spread per underlying; scan self-gates to once/day

### Risk caps (lib/options_risk.py — enforced in code, not prose)
- Max 3 concurrent spreads · per-trade loss ≤ 2% equity · total ≤ 6% equity
- Daily loss halt −2% · buying power ≥ max loss · proposal age < 5 min

### mleg order sign convention (critical)
`limit_price` **negative = credit received** (entry), **positive = debit paid** (close).

### Key files
| File | Purpose |
|------|---------|
| `lib/options_client.py` | Second-account client (contracts, chain greeks, mleg) |
| `lib/options_strategy.py` | Selection rules (pure, no I/O side effects) |
| `lib/options_risk.py` | Code-enforced caps |
| `lib/options_positions.py` | OCC parsing + spread grouping |
| `lib/options_manager.py` | Exit rules |
| `bin/options` | CLI: snapshot / scan / enter / manage / status |
| `reports/options_*.json` | All Bot #2 IPC (separate from Bot #1) |

### Critical Rules
- `ALPACA_OPTIONS_LIVE_TRADE=true` = real API orders **on the paper account only** (base URL always `paper-api`); never repoint the options base URL at live
- Entry orders priced at the freshly-quoted combo bid (re-quoted at placement, 120s fill wait); unfilled day orders expire and are retried next scan
- No entry without passing every check in `lib/options_risk.py` + Laya non-reject
- Mechanical exits always override Laya advice
- Risk/proposal freshness: same cycle only (< 5 min)
