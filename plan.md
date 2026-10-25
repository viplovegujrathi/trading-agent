# Plan

Build an iron condor agent that trades through tastytrade's official API, sandbox first, with a human approval on every live order.

## Goals

1. Screen beaten-down S&P 500 names and propose iron condors that fit a $5,000 account.
2. Place each condor as one four-leg order, never legged in.
3. Manage open condors to the exit rules (50% profit, 21 DTE, short strike touched, day before expiry).
4. Keep a complete journal so the strategy can be judged on its own record.

## Non-goals

- Replacing the Robinhood put-spread or DITM agents.
- Fully unattended live trading. Approval stays on until Phase 5 criteria are met.
- Undefined-risk trades (naked options, strangles), cash-secured puts, calendars.

## Fixed decisions

| Decision | Choice | Why |
|---|---|---|
| Broker | tastytrade | Official multi-leg API with dry-run, OAuth2, sandbox |
| Account size | $5,000 | User-approved |
| Per-condor collateral cap | $1,000 | Max 4 open condors with a $1,000 buffer |
| Order type | Limit, net credit, Day | Multi-leg market orders are a bad fill risk |
| Approval | Human yes before every live submit | No order leaves the box without it |
| Runtime | AWS EC2, Python core on systemd timers; Claude Code interactive only | Same AWS setup as the Robinhood agents; a missed close never depends on Claude |

## Status (2026-10-25)

| Phase | Code | What's left |
|---|---|---|
| 0 Accounts and access | `auth.py` (refresh-token grant, SSM or env secrets, rotation) | Your accounts and OAuth app; run `cli status` against the sandbox; verify every endpoint path and field name in [architecture.md](architecture.md) |
| 1 Data layer | `marketdata.py`, `scripts/update_universe.py`, `scripts/fetch_history.py` | Confirm `market-data/by-type` returns delta and open interest; if not, deltas come out `modeled` |
| 2 Screen, strikes, sizing | `screen.py`, `structure.py`, tests | The 20-historical-date hand check |
| 3 Order path in sandbox | `orders.py`, `risk.py`, `approval.py`, `broker.py`, `agent.py` | 10 sandbox round trips |
| 4 Monitoring and journal | `monitor.py`, `journal.py`, `deploy/systemd/` | 5 trading days from timers |
| 5 Live | Not started | Separate decision after Phase 4 exit |

`TT_ENV=paper` runs phases 1–4 offline against a simulated market so the code path can be exercised before Phase 0 is done.

## Phases

### Phase 0 — Accounts and access (week 1)

- Open and fund a tastytrade margin account; request options level that covers spreads.
- Create a sandbox account and an OAuth app in the developer portal.
- Store the refresh token in SSM Parameter Store (see [deploy.md](deploy.md)), never in git.
- **Exit:** a script fetches the sandbox account balance and an option chain with a fresh access token.

### Phase 1 — Data layer (week 1–2)

- Option chain, quotes and greeks for a symbol.
- Underlying history for 50-day average, 52-week high, RSI(14).
- Earnings and ex-dividend dates; IV rank.
- S&P 500 constituents list with a staleness date.
- **Exit:** `screen --dry` prints the candidate table for today with every field sourced and timestamped.

### Phase 2 — Screen, strikes, sizing (week 2)

- Implement the rules in [strategy.md](strategy.md) as pure functions driven by `config/strategy.yaml`.
- Unit tests for each rule, including the edge cases in [risk.md](risk.md).
- **Exit:** tests pass; on 20 historical dates the screen output matches a hand check.

### Phase 3 — Order path in sandbox (week 3)

- Build the four-leg order JSON; call `orders/dry-run`; parse buying-power effect and fees.
- Risk gate re-checks every limit against the dry-run result.
- Approval gate: notify, wait for a yes, expire after 15 minutes.
- Submit with a unique `external-identifier`; on timeout, look the order up before any resend.
- **Exit:** 10 sandbox condors opened and closed end to end with zero duplicate orders.

### Phase 4 — Monitoring and journal (week 3–4)

- Poll open positions; evaluate exit rules; build closing orders through the same dry-run, risk and approval path.
- Append every proposal, decision, fill and close to `journal/` (JSONL) and update `state/positions.json`.
- Daily summary message.
- **Exit:** a full sandbox cycle runs from cron for 5 trading days without manual fixes.

### Phase 5 — Live, small (weeks 5–12)

- Switch to production with **one** contract and **one** open condor at a time for the first 4 weeks.
- Then raise to the configured limits (4 open condors).
- **Exit to consider relaxing approval:** 30+ closed live condors, no limit breach, no unhandled error, and journal P&L reconciles to the broker statement. Relaxing approval is a separate, explicit decision.

## Milestones

| Milestone | Target | Done when |
|---|---|---|
| M0 Access | Week 1 | Sandbox auth and chain fetch work |
| M1 Screen | Week 2 | Candidate table matches hand check |
| M2 Sandbox orders | Week 3 | 10 clean round trips |
| M3 Unattended sandbox | Week 4 | 5 days from cron |
| M4 First live condor | Week 5 | One approved live fill |
| M5 Full size | Week 9 | 4 condors allowed |

## Open questions

- [ ] 30–35 DTE (matches the put agent) or 45 DTE (matches the tastylive research)? Default 30–45 window until decided.
- [ ] Approval bot: Slack (Socket Mode) or Telegram? Telegram and a CLI channel are built; Slack would be added in `approval.py`.
- [ ] Reuse the DITM EC2 instance or run a separate `t4g.small`? See [deploy.md](deploy.md).
- [ ] Does tastytrade's market-metrics endpoint give reliable earnings and ex-dividend dates, or is a second source needed?
- [ ] Does the account get the same spread-margin treatment in production as the dry-run shows in sandbox?

## Risks to the plan

| Risk | Mitigation |
|---|---|
| Sandbox fills don't match live fills | Phase 5 starts at one contract; compare fills to mid in the journal |
| Data gaps (missing earnings date) | Missing data = exclude the name, never assume |
| Token refresh fails mid-session | Fail closed: no orders without a valid token; alert |
| Strategy has no edge on single stocks | Journal everything; review after 30 trades before scaling |
