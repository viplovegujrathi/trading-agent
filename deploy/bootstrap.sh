#!/usr/bin/env bash
# One-time setup on a fresh Amazon Linux 2023 / Ubuntu instance, run as root (e.g. via SSM Session Manager).
# Paper mode needs no broker secrets. Usage: sudo BUCKET=my-bucket bash bootstrap.sh
set -euo pipefail
BUCKET="${BUCKET:?set BUCKET=<s3 bucket for backups>}"
REPO=https://github.com/viplovegujrathi/trading-agent.git

id tagent >/dev/null 2>&1 || useradd --system --create-home --home-dir /opt/trading-agent tagent
if command -v dnf >/dev/null; then dnf install -y git python3.11 python3.11-pip awscli
else apt-get update && apt-get install -y git python3.11 python3.11-venv awscli; fi

sudo -iu tagent bash -s <<INNER
set -euo pipefail
[ -d app ] || git clone $REPO app
cd app && git pull --ff-only
python3.11 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
[ -f config/strategy.yaml ] || cp config/strategy.example.yaml config/strategy.yaml
cat > .env <<ENV
TT_ENV=paper
TA_HALT=0
TA_METRICS=1
TA_BACKUP_BUCKET=$BUCKET
DASH_HOST=127.0.0.1
ENV
chmod 600 .env
mkdir -p state journal
.venv/bin/pytest -q
INNER

cp /opt/trading-agent/app/deploy/systemd/* /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now trading-agent-open.timer trading-agent-monitor-midday.timer \
  trading-agent-monitor-close.timer trading-agent-backup.timer
systemctl enable --now trading-agent-dashboard.service
systemctl list-timers 'trading-agent*'
