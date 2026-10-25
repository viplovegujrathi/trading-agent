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

## Status

Design stage. See [plan.md](plan.md) for what gets built first.

> Options involve significant risk and can lose more than the credit received. Nothing here is financial advice.
