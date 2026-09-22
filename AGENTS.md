# Alpaca Trader Bot — Agent Orchestration

## Roles
See `agents/<role>-agent.md` for full instructions.

## Pipeline (each cycle)
```
1. bash cycle.sh         → write reports/ (account, clock, positions, open_orders)
                           → skip if market closed (use FORCE_CYCLE=1 to override)
                           → write daily snapshot to reports/history/
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

Laya is advisory — it provides a second opinion alongside indicator analysis. The LLM agent makes the final decision, but Laya's "reject with immediate urgency" overrides other signals.

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
| `reports/history/` | Daily snapshots |

## Critical Rules
- No live orders without risk approval
- Paper trading default (set ALPACA_LIVE_TRADE=true for live)
- Log every cycle
- Bracket orders mandatory when stop/target prices provided
- Risk decisions expire after 1 cycle (must be fresh)
- Sector allocation capped at 25% per sector
