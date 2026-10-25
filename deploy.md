# Deploy on AWS

> **Start here (paper mode, no broker secrets):** create the instance and an S3 bucket, attach a role with [deploy/iam-policy.json](deploy/iam-policy.json) plus `AmazonSSMManagedInstanceCore`, open a Session Manager shell, then run `sudo BUCKET=<bucket> bash deploy/bootstrap.sh`. It installs the app, runs the tests and enables the timers in [deploy/systemd/](deploy/systemd/) (`run-open` 09:45 ET, `run-monitor` 12:30 and 15:15 ET, backup every 15 min). Sections 2, 5 and the SSM parts of 1 apply only once you move to tastytrade/production.

The agent runs on an EC2 instance, next to the Robinhood agents. The Python core runs from **systemd timers**; it screens, sizes, dry-runs, asks for your approval and submits on its own. Claude Code is for interactive work (explaining candidates, changing code, reviewing the journal) and is **not** in the order path, so a missed close never depends on Claude being reachable.

## Topology

| Piece | AWS service | Notes |
|---|---|---|
| Compute | EC2 (existing DITM instance, or a new `t4g.small`) | Always on; Amazon Linux 2023 or Ubuntu 22.04+ |
| Schedule | systemd timers on the instance | Restart on failure, logs to journald |
| Secrets | SSM Parameter Store `SecureString` | Refresh token, client secret, approval-bot token |
| Backup | S3 bucket, versioned, SSE-S3 | `journal/` and `state/` synced every 15 min |
| Monitoring | CloudWatch Logs + heartbeat metric + alarm → SNS | Alarm if a scheduled run doesn't report |
| Access | SSM Session Manager | No SSH, no inbound security-group rules |

All traffic is outbound: tastytrade API, the approval bot, AWS APIs.

## Viewing the dashboard

The dashboard (`cli dashboard`, enabled by `bootstrap.sh` as `trading-agent-dashboard.service`) is read-only and listens on **127.0.0.1:8787 only**. No security-group port is opened. From your Mac, forward it through Session Manager (needs the AWS CLI and the Session Manager plugin):

```bash
aws ssm start-session --target <instance-id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8787"],"localPortNumber":["8787"]}'
```

Then open http://localhost:8787. It shows paper cash, open condors, realized P&L, the kill switch, recent journal events and the limits, and refreshes every 30 seconds.

### From your phone, laptop or other devices (Tailscale)

Tailscale is a private network between your own devices. Nothing is exposed to the internet and no security-group port opens.

1. Make a free Tailscale account (sign in with a method you control and turn on 2FA). Install the app on each device you want to use.
2. On the instance (Session Manager shell): `curl -fsSL https://tailscale.com/install.sh | sudo sh`, then `sudo tailscale up`, and open the login link it prints to add the instance to your network. Keep the node key expiry on (default).
3. Get its address: `tailscale ip -4` (a `100.x.y.z` address).
4. Set it in `/opt/trading-agent/app/.env`: `DASH_HOST=100.x.y.z`. Optionally add `TA_DASH_PASSWORD=<long random password>` for a second layer, which makes the browser ask for a login (any username).
5. `sudo systemctl restart trading-agent-dashboard`.
6. On any of your devices (Tailscale running) open `http://100.x.y.z:8787`.

The server refuses to start on any address that is not loopback or in Tailscale's `100.64.0.0/10` range, so a typo cannot publish it to the internet. In the Tailscale admin console, keep access rules to your own devices only. Traffic is end-to-end encrypted by Tailscale, so plain `http://` inside the tailnet is fine. Without Tailscale installed on a device it cannot reach the page, which is the point.

## Same instance or a new one?

Use the **existing instance** if it is always on and has headroom (the agent needs < 300 MB RAM and a few CPU-seconds per run). Use a **separate instance** if you want a condor-agent bug or a bad dependency upgrade to be unable to touch the Robinhood agents. Separate costs about $12/month for a `t4g.small`; it's the cleaner choice once live money is involved.

## 1. IAM role for the instance

Attach a role with only:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["ssm:GetParameter", "ssm:GetParameters"],
     "Resource": "arn:aws:ssm:REGION:ACCOUNT:parameter/trading-agent/*"},
    {"Effect": "Allow", "Action": ["ssm:PutParameter"],
     "Resource": "arn:aws:ssm:REGION:ACCOUNT:parameter/trading-agent/*/refresh_token"},
    {"Effect": "Allow", "Action": ["kms:Decrypt"], "Resource": "KMS_KEY_ARN"},
    {"Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject", "s3:ListBucket"],
     "Resource": ["arn:aws:s3:::BUCKET", "arn:aws:s3:::BUCKET/*"]},
    {"Effect": "Allow", "Action": ["cloudwatch:PutMetricData"], "Resource": "*",
     "Condition": {"StringEquals": {"cloudwatch:namespace": "TradingAgent"}}},
    {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
     "Resource": "arn:aws:logs:REGION:ACCOUNT:log-group:/trading-agent/*"}
  ]
}
```

Plus the managed policy `AmazonSSMManagedInstanceCore` for Session Manager. `PutParameter` is limited to the refresh token so the agent can save a rotated one.

## 2. Secrets in Parameter Store

```bash
aws ssm put-parameter --type SecureString --name /trading-agent/sandbox/client_id     --value '...'
aws ssm put-parameter --type SecureString --name /trading-agent/sandbox/client_secret --value '...'
aws ssm put-parameter --type SecureString --name /trading-agent/sandbox/refresh_token --value '...'
aws ssm put-parameter --type SecureString --name /trading-agent/sandbox/account       --value '...'
aws ssm put-parameter --type SecureString --name /trading-agent/approval_bot_token    --value '...'
# repeat under /trading-agent/production/ for Phase 5
```

`.env` on the instance then holds only non-secret settings (`TT_ENV`, `TA_APPROVAL_CHANNEL`, `TA_HALT`, `AWS_REGION`). `auth.py` reads secrets from SSM at start-up and keeps them in memory only.

## 3. Install

```bash
sudo useradd --system --create-home --home-dir /opt/trading-agent tagent
sudo -iu tagent
git clone https://github.com/viplovegujrathi/trading-agent.git app
cd app && python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env
cp config/strategy.example.yaml config/strategy.yaml
mkdir -p state journal
```

Runs as its own unprivileged user, separate from the Robinhood agents.

## 4. systemd timers

`/etc/systemd/system/trading-agent@.service`

```ini
[Unit]
Description=Iron condor agent: %i
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=tagent
WorkingDirectory=/opt/trading-agent/app
EnvironmentFile=/opt/trading-agent/app/.env
ExecStart=/opt/trading-agent/app/.venv/bin/python -m trading_agent.cli %i   # %i = run-open | run-monitor | backup
TimeoutStartSec=30min
Restart=on-failure
RestartSec=60
```

`/etc/systemd/system/trading-agent-screen.timer`

```ini
[Timer]
OnCalendar=Mon..Fri 09:45 America/New_York
Persistent=false
Unit=trading-agent@screen.service

[Install]
WantedBy=timers.target
```

`/etc/systemd/system/trading-agent-monitor.timer`

```ini
[Timer]
OnCalendar=Mon..Fri 12:30 America/New_York
OnCalendar=Mon..Fri 15:15 America/New_York
Persistent=false
Unit=trading-agent@monitor.service

[Install]
WantedBy=timers.target
```

`/etc/systemd/system/trading-agent-backup.timer` runs `trading-agent@backup.service` every 15 minutes (`OnCalendar=*:0/15`).

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now trading-agent-screen.timer trading-agent-monitor.timer trading-agent-backup.timer
systemctl list-timers 'trading-agent*'
```

`Persistent=false` on purpose: if the instance was down at 9:45, it should not fire a late screen at 14:00. The CLI also refuses opens outside the trading window and on market holidays.

## 5. Approval channel

The approval gate lives in the Python core (`approval.py`) and needs two-way messaging with outbound connections only:

| Channel | How | Notes |
|---|---|---|
| Slack (recommended if you use Slack) | Slack app in **Socket Mode** | Outbound websocket; YES/NO buttons |
| Telegram | Bot with long polling | Simplest setup; restrict to your chat ID |

The bot accepts a reply only from your user ID, and only `YES <external-identifier>` for an open, unexpired ticket.

## 6. Backups

`cli.py backup` runs `aws s3 sync journal/ s3://BUCKET/journal/` and `state/` the same way. The bucket has versioning on, public access blocked, and a lifecycle rule moving objects older than 90 days to Glacier. Restore on a new instance with `aws s3 sync` in the other direction before enabling timers.

## 7. Monitoring and alerts

| Signal | Source | Alarm |
|---|---|---|
| Heartbeat | Each run puts `TradingAgent/RunCompleted` (dimension: `job`) | Missing for screen by 10:15 ET, or for monitor by 15:45 ET → SNS |
| Errors | `TradingAgent/RunFailed`, `UnknownOrder`, `HaltTripped` | Any value ≥ 1 → SNS |
| Logs | journald → CloudWatch agent → `/trading-agent/runs` | 30-day retention |
| Instance | EC2 status check failed | Auto-recover action + SNS |

SNS sends to your email and phone. Alerts are separate from the approval bot so a broken bot still reaches you.

## 8. Updates

```bash
sudo -iu tagent && cd app
touch state/HALT                       # no opens during the update
git pull && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest -q
.venv/bin/python -m trading_agent.cli status
rm state/HALT
```

Never update between 9:30 and 16:00 ET on a day with open condors near an exit trigger.

## 9. Cost (rough, us-east-1)

| Item | Monthly |
|---|---|
| `t4g.small` (if separate) | ~$12 |
| EBS 20 GB gp3 | ~$1.60 |
| S3, CloudWatch, SNS, Parameter Store standard | < $2 |

Reusing the existing instance makes it about $2/month.

## Checklist before Phase 5 (live)

- [ ] Instance role has only the permissions above
- [ ] No inbound security-group rules; Session Manager works
- [ ] Production secrets in `/trading-agent/production/*`, sandbox secrets untouched
- [ ] Heartbeat alarm tested by stopping a timer for a day
- [ ] Restore from S3 tested on a scratch instance
- [ ] Approval bot rejects replies from any other user
- [ ] `max_open_condors: 1` set for the first 4 weeks
