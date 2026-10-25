# Setup

## 1. Accounts

1. Open a tastytrade **margin** account and fund it ($5,000 target).
2. Request options trading at a level that covers spreads (vertical spreads and iron condors).
3. Open a **sandbox** account from the developer portal (https://developer.tastytrade.com/docs/sandbox/). All development and Phase 3–4 testing happens there.

## 2. OAuth app

1. In the tastytrade developer portal, create an OAuth application.
2. Note the client ID and client secret.
3. Complete the authorization flow once to get a **refresh token** (see https://developer.tastytrade.com/docs/authentication/oauth2/).
4. Repeat for sandbox and production; they are separate.

Access tokens last 15 minutes. The agent only stores the refresh token and mints access tokens as needed.

## 3. Box

For local development. Production runs on AWS EC2 — follow [deploy.md](deploy.md).

```bash
git clone https://github.com/viplovegujrathi/trading-agent.git
cd trading-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # added in Phase 1
cp .env.example .env && chmod 600 .env
cp config/strategy.example.yaml config/strategy.yaml
mkdir -p state journal
```

Fill `.env`:

```
TT_ENV=sandbox                 # sandbox | production
TT_CLIENT_ID=...
TT_CLIENT_SECRET=...
TT_REFRESH_TOKEN=...
TT_ACCOUNT=...                 # account number
TA_APPROVAL_CHANNEL=push       # push | slack | email
TA_HALT=0
```

## 4. Claude Code

Claude Code loads [CLAUDE.md](CLAUDE.md) automatically when it works in this repo. Install the skill so it can be invoked from anywhere:

```bash
mkdir -p ~/.claude/skills
ln -s "$PWD/skills/iron-condor-tastytrade" ~/.claude/skills/iron-condor-tastytrade
```

Optional: tastytrade publishes an MCP server (https://developer.tastytrade.com/docs/sdks-and-tools/mcp-server/). It is useful for reading data, but **orders still go through `broker.py`** so the risk gate and approval gate can't be skipped.

## 5. Schedule (Phase 4+)

Production uses systemd timers on EC2 ([deploy.md](deploy.md) §4). For a local test box, cron works:

```cron
CRON_TZ=America/New_York
45 9  * * 1-5  cd ~/trading-agent && .venv/bin/python -m trading_agent.cli screen   >> journal/cron.log 2>&1
30 12 * * 1-5  cd ~/trading-agent && .venv/bin/python -m trading_agent.cli monitor  >> journal/cron.log 2>&1
15 15 * * 1-5  cd ~/trading-agent && .venv/bin/python -m trading_agent.cli monitor  >> journal/cron.log 2>&1
```

## 6. First sandbox run

```bash
python -m trading_agent.cli status            # token, account, balances
python -m trading_agent.cli screen --dry      # candidate table, no orders
python -m trading_agent.cli propose AAPL      # builds + dry-runs one condor
```

## 7. Going live (Phase 5)

1. `TT_ENV=production`, production refresh token and account.
2. Set `max_open_condors: 1` in `config/strategy.yaml` for the first 4 weeks.
3. Confirm the kill switch: `touch state/HALT`, run `screen`, check nothing is proposed for submit; then clear it.
