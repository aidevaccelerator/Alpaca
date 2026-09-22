# Cloud Deployment Guide — Oracle Cloud Always Free

## What Deploys

Everything runs on the cloud VM. No Mac dependency.

```
Oracle Cloud (ARM VM, free forever)
┌──────────────────────────────────────┐
│ cron (every minute, M-F, 9:30-16:00)│
│ ├─ snapshot.py                       │
│ ├─ analyst (research triage/signals) │
│ ├─ risk (research assess)            │
│ └─ execute_order.py                  │
│                                      │
│ laya (CPU, PyTorch, ~33ms/decision)  │
└──────────────────────────────────────┘
```

## Step 1: Create Oracle Cloud VM

1. Go to https://cloud.oracle.com → Sign up (free, no credit card charged)
2. **Home region**: Pick closest to you (e.g., `us-ashburn-1`)
3. Create VCN:
   - Networking → Virtual Cloud Networks → Start VCN Wizard
   - "Create VCN with Internet Connectivity"
   - CIDR: `10.0.0.0/16`, Public Subnet: `10.0.0.0/24`
4. Create Compute Instance:
   - Compute → Instances → Create Instance
   - Name: `alpaca-bot`
   - Image: **Ubuntu 24.04**
   - Shape: **VM.Standard.A1.Flex** (ARM Ampere — Always Free)
   - OCPUs: **1**, RAM: **6 GB**
   - VCN: Select the one you created
   - Public IP: Assign ephemeral
   - Upload SSH key: `ssh-keygen -t ed25519` then paste the public key
5. Note the public IP address

## Step 2: Deploy

```bash
cd alpaca-trader-bot

chmod +x deploy/oracle-setup.sh
./deploy/oracle-setup.sh <VM_IP> ~/.ssh/id_ed25519
```

This syncs the project, creates a venv, installs deps, copies `.env`, and sets up a cron job.

## Step 3: Verify

```bash
ssh -i ~/.ssh/id_ed25519 ubuntu@<VM_IP>

# Test a cycle
cd ~/alpaca-trader-bot
FORCE_CYCLE=1 .venv/bin/python lib/snapshot.py

# Check cron
crontab -l

# View logs
tail -f ~/alpaca-bot.log
```

## Costs

- Oracle Cloud Always Free: **$0/month forever**
- Alpaca API: **$0** (paper trading)

## Troubleshooting

| Issue | Fix |
|-------|-----|
| VM won't start (capacity) | Try different availability domain or retry later |
| SSH connection refused | Check security list allows port 22 from your IP |
| `laya` import error | `.venv/bin/pip install laya` |
| Wrong market hours | Check timezone: `timedatectl` on VM |
| Cycle not running | `crontab -l` to verify, check `~/alpaca-bot.log` |
