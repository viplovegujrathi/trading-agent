# trading-agent

An iron condor agent for beaten-down S&P 500 names, trading through tastytrade's official API.

It screens for stocks 15%+ off their 52-week high and 3–8% below their 50-day average, builds a four-leg iron condor (put side at support, call side capped at the 50-day average), sizes it to a $1,000 collateral cap, dry-runs it at the broker, and submits it as **one** multi-leg order only after you approve.

![Architecture](docs/architecture.svg)

## Why tastytrade, not Robinhood

| | Robinhood | tastytrade |
|---|---|---|
| Agent can place a 4-leg condor | No. The agentic MCP takes single-leg options only | Yes. One order, up to 4 legs, one net-credit limit |
| Pre-trade check | Single-leg review only | `orders/dry-run` returns buying-power effect, fees and warnings |
| Auth | Password-based unofficial APIs breach the customer agreement | OAuth2, 15-minute access tokens |
| Practice account | None | Sandbox |

The existing Robinhood agents (put credit spread, DITM calls) keep running there. This agent is separate.

## Documents

| File | What's in it |
|---|---|
| [plan.md](plan.md) | Phased build plan, milestones and exit criteria |
| [architecture.md](architecture.md) | Components, data flow, order lifecycle, failure handling |
| [strategy.md](strategy.md) | Screen, strike, sizing and exit rules, with the research behind them |
| [risk.md](risk.md) | Hard limits, kill switch, what the agent must never do |
| [setup.md](setup.md) | Accounts, OAuth app, environment, first sandbox run |
| [deploy.md](deploy.md) | AWS deployment: EC2, systemd timers, SSM secrets, S3 backup, CloudWatch alerts |
| [CLAUDE.md](CLAUDE.md) | Instructions Claude Code loads when it works in this repo |
| [skills/iron-condor-tastytrade/SKILL.md](skills/iron-condor-tastytrade/SKILL.md) | The agent skill itself |
| [config/strategy.example.yaml](config/strategy.example.yaml) | All tunable numbers in one place |
| [runbook.md](runbook.md) | Daily operation, incidents, recovery |
| [data/README.md](data/README.md) | Universe, price history and paper-mode fixture files |

## Status

Phases 1–4 are built in code (`src/trading_agent/`, 87 tests). Phase 0 (accounts, OAuth app, verifying each endpoint against the sandbox) is next and needs you. See the status table in [plan.md](plan.md).

## Quick start (no account needed)

Paper mode runs the whole pipeline offline against a simulated market of fictional tickers. Every price it shows is `modeled`.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -q
export TT_ENV=paper
python -m trading_agent.cli status
python -m trading_agent.cli screen --dry           # candidate table
python -m trading_agent.cli propose ALFA           # build -> risk -> dry-run -> risk -> ticket
python -m trading_agent.cli submit <ext> --confirm "YES <ext>"
python -m trading_agent.cli monitor                # exit rules on open condors
```

Submits are refused outside the trading window, in paper mode too. To try one on a weekend, or to fast-forward to an exit, simulate the clock (paper only):

```bash
export TA_PAPER_CLOCK=2026-10-26T15:00:00+00:00    # 11:00 ET on a Monday
```

For the tastytrade sandbox, follow [setup.md](setup.md).

## Code

| Module | Role |
|---|---|
| `config.py` | Settings from env/.env; limits from `config/strategy.yaml` |
| `auth.py` | OAuth2 refresh-token grant; secrets from env or SSM; fails closed |
| `broker.py` | tastytrade REST client; idempotent submit (look up by `external-identifier`, never resend) |
| `marketdata.py` | Chains, quotes, market metrics; daily history from `data/history/` |
| `screen.py` / `structure.py` | Base screen; expiry, strikes, wings, sizing (pure) |
| `risk.py` | Every hard limit in [risk.md](risk.md); kill switch |
| `orders.py` | Multi-leg limit-order JSON, OCC symbols, external identifiers |
| `approval.py` | Tickets, `YES <ext>` replies, 15-minute expiry; CLI and Telegram channels |
| `monitor.py` | Exit rules and assignment detection (pure) |
| `agent.py` | Wires the order path: risk → dry-run → risk → approval → risk → submit |
| `journal.py` | Append-only JSONL journal, `positions.json`, `tickets.json` |
| `paper.py` | Offline simulated market and broker for `TT_ENV=paper` |
| `ops.py` | S3 backup and CloudWatch metrics |
| `cli.py` | `status`, `screen`, `propose`, `submit`, `decline`, `monitor`, `close`, `halt`, `resolve`, `tickets`, `summary`, `backup` |

> Options involve significant risk and can lose more than the credit received. Nothing here is financial advice.
