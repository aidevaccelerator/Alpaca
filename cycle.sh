#!/usr/bin/env bash
# Run one data-collection cycle. Called by the orchestrator (me).
set -euo pipefail

cd "$(dirname "$0")"

# Source .env if present
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

python3 lib/snapshot.py
