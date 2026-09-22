#!/usr/bin/env bash
# Run one data-collection cycle. Called by the orchestrator (me).
set -euo pipefail

cd "$(dirname "$0")"

# Use project venv if available
if [ -f .venv/bin/python ]; then
  PYTHON=.venv/bin/python
else
  PYTHON=python3
fi

# Source .env if present
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

$PYTHON lib/snapshot.py

# Check if market is closed — skip if so (unless forced)
if [ "${FORCE_CYCLE:-}" != "1" ]; then
  CLOCK_FILE="reports/clock.json"
  if [ -f "$CLOCK_FILE" ]; then
    IS_OPEN=$($PYTHON -c "import json,sys; d=json.load(open('$CLOCK_FILE')); print(d.get('is_open', False))" 2>/dev/null || echo "False")
    if [ "$IS_OPEN" = "False" ]; then
      NEXT_OPEN=$($PYTHON -c "import json; d=json.load(open('$CLOCK_FILE')); print(d.get('next_open', 'unknown'))" 2>/dev/null || echo "unknown")
      echo "MARKET CLOSED — next open: $NEXT_OPEN (use FORCE_CYCLE=1 to override)"
      exit 0
    fi
  fi
fi
