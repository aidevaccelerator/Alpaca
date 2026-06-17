# Alpaca Trader Bot — Agent Orchestration

## Roles
See `agents/<role>-agent.md` for full instructions.

## Pipeline (each cycle)
```
1. bash cycle.sh         → write reports/ (account, clock, positions)
2. ANALYST               → read reports/, write proposals to reports/analyst.json
3. ORCHESTRATOR (me)     → read proposals (exits + entries), forward both to risk
4. RISK                  → validate ALL (exits + entries), write to reports/risk.json
5. ORCHESTRATOR          → read risk decision
   - REJECTED → log, skip
   - APPROVED exits → trader sells first
   - APPROVED entries → trader buys after exits
6. TRADER (via Python)   → execute approved exits, then entries
7. PORTFOLIO             → confirm fills, update snapshot
8. Sleep 60s → repeat
```

## Communication
- All IPC via `reports/*.json` files
- The orchestrator (me) reads/writes summaries as needed
- Agents never submit orders directly — trader calls `python3 lib/execute_order.py`

## Critical Rules
- No live orders without risk approval
- Paper trading default (set ALPACA_LIVE_TRADE=true for live)
- Log every cycle
