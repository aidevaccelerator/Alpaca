#!/usr/bin/env bash
# Deploy alpaca-trader-bot to Oracle Cloud Always Free VM
# Run this ONCE from your Mac after creating the VM.
#
# Usage:
#   ./deploy/oracle-setup.sh <VM_IP> <SSH_KEY>
#
# Prerequisites:
#   1. Create Oracle Cloud Always Free VM (ARM Ampere A1, Ubuntu 24.04)
#   2. Download SSH key from console
#   3. ssh ubuntu@<VM_IP> to verify connectivity
set -euo pipefail

VM_IP="${1:?Usage: $0 <VM_IP> <SSH_KEY_PATH>}"
SSH_KEY="${2:?Usage: $0 <VM_IP> <SSH_KEY_PATH>}"
REMOTE="ubuntu@${VM_IP}"
DEPLOY_DIR="/home/ubuntu/alpaca-trader-bot"

echo "=== Deploying to ${REMOTE} ==="

# 1. Install dependencies on remote
echo "[1/5] Installing Python + deps on remote..."
ssh -i "$SSH_KEY" "$REMOTE" "sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-pip python3-venv git"

# 2. Sync project
echo "[2/5] Syncing project..."
ssh -i "$SSH_KEY" "$REMOTE" "mkdir -p ${DEPLOY_DIR}"

rsync -avz --progress \
  -e "ssh -i ${SSH_KEY}" \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude 'node_modules' \
  --exclude '.DS_Store' \
  --exclude 'reports/history' \
  ./ "${REMOTE}:${DEPLOY_DIR}/"

# 3. Create venv and install deps
echo "[3/5] Setting up Python environment..."
ssh -i "$SSH_KEY" "$REMOTE" "cd ${DEPLOY_DIR} && python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt"

# 4. Create cron job: every minute, M-F, market hours (9:25-16:05 ET = 13:25-20:05 UTC)
echo "[4/5] Setting up cron schedule..."
ssh -i "$SSH_KEY" "$REMOTE" "
(crontab -l 2>/dev/null | grep -v 'alpaca-trader-bot' || true; \
 echo '* 13-20 * * 1-5 cd ${DEPLOY_DIR} && FORCE_CYCLE=1 .venv/bin/python lib/snapshot.py >> /home/ubuntu/alpaca-bot.log 2>&1') | crontab -
echo 'Cron installed'
"

# 5. Copy .env
echo "[5/5] Copying credentials..."
scp -i "$SSH_KEY" .env "${REMOTE}:${DEPLOY_DIR}/.env"

echo ""
echo "=== Deploy complete ==="
echo ""
echo "Test:  ssh -i ${SSH_KEY} ${REMOTE} 'cd ${DEPLOY_DIR} && FORCE_CYCLE=1 .venv/bin/python lib/snapshot.py'"
echo "Logs:  ssh -i ${SSH_KEY} ${REMOTE} 'tail -f ~/alpaca-bot.log'"
echo "Cron:  ssh -i ${SSH_KEY} ${REMOTE} 'crontab -l'"
