# Alpaca Trader Bot — Agent Orchestration

## Roles
See `agents/<role>-agent.md` for full instructions.

## Pipeline (each cycle)
```
1. cycle.sh              → write reports/ (account, clock, positions, market_data)
2. ANALYST               → read reports/, write proposals to reports/analyst.json
3. LEADER (orchestrator) → read proposals, forward to risk
4. RISK                  → validate, write approval/rejection to reports/risk.json
5. LEADER                → read risk decision
   - REJECTED → log, skip
   - APPROVED → forward to trader
6. TRADER (via Python)   → execute approved orders
7. PORTFOLIO             → confirm fills, update snapshot
8. Sleep 60s → repeat
```

## Communication
- All IPC via `reports/*.json` files
- The orchestrator (me) reads/writes summaries as needed
- Agents never submit orders directly — trader uses Python `lib/snapshot.py` or direct Alpaca calls

## Critical Rules
- No live orders without risk approval
- Paper trading default (set ALPACA_LIVE_TRADE=true for live)
- Log every cycle
