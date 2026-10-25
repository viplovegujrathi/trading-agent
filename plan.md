# Plan

Build an iron condor agent that trades through tastytrade's official API, sandbox first, with a human approval on every live order.

## Goals

1. Screen beaten-down S&P 500 names and propose iron condors that fit a $5,000 account.
2. Place each condor as one four-leg order, never legged in.
3. Manage open condors to the exit rules (50% profit, 21 DTE, short strike touched, day before expiry).
4. Keep a complete journal so the strategy can be judged on its own record.

## Non-goals

- Replacing the Robinhood put-spread or DITM agents.
- Fully unattended live trading before the Phase 5 graduation test passes (enforced in `autonomy.py`).
- Undefined-risk trades (naked options, strangles), cash-secured puts, calendars.

## Fixed decisions

| Decision | Choice | Why |
|---|---|---|
| Broker | tastytrade | Official multi-leg API with dry-run, OAuth2, sandbox |
| Account size | $5,000 | User-approved |
| Per-condor collateral cap | $1,000 | Max 4 open condors with a $1,000 buffer |
| Order type | Limit, net credit, Day | Multi-leg market orders are a bad fill risk |
| Approval | Autonomous in paper/sandbox; human yes before every production submit until graduation (30+ closed live condors, 0 breaches, 0 unknown orders) | Decided 2026-10-25: agent trades on its own, starting with a $5,000 paper account |
| Runtime | AWS EC2, Python core on systemd timers; the Claude skill can also run the same CLI cycles and report | A missed close never depends on Claude. Paper first, no broker secrets needed |

## Status and review (2026-10-25)

| Phase | State |
|---|---|
| 0 Access | Not started: needs a tastytrade sandbox account and OAuth app |
| 1 Data | Partly: Yahoo prices, options and S&P 500 list work (modeled). Missing: broker quotes, IV rank, reliable earnings/dividend dates |
| 2 Screen, strikes, sizing | Done, with tests (the "20 historical dates hand check" is not done) |
| 3 Order path | Built against a paper broker (risk, dry-run, route, safe submit). Missing: tastytrade client, approval bot, reprice/cancel flow |
| 4 Monitor and journal | Built and tested; EC2 deploy kit ready; 5-day unattended run not yet done |
| 5 Live | Not started; code-enforced graduation gate in place |

Gaps found in review, none of which can lose money in paper mode but all matter before production:

1. **Reprice/cancel is not wired.** `orders.reprice()` exists, nothing calls it; paper fills instantly, so the Working/Repriced/Cancelled path is untested.
2. **Assignment detection is not wired.** `assignment.handle()` is tested but needs a broker positions feed.
3. **Touch detection is weak on Yahoo data** (latest price only, not the intraday range). The rule is tested; the data feed is not good enough.
4. **Ex-dividend exit** closes the whole condor (stricter than strategy.md's call-side-only close) until a call-spread-only closing order exists.
5. **Exit reasons are not journaled on the close line** (the `exit_signal` event holds them), so P&L by reason needs a join.
6. **Yahoo has no IV rank**, so every name is currently unflagged or missing on that rule.
7. **Open-vs-close cap:** closes are exempt from the 6-orders/day cap by design (open question below).
8. The strategy and universe are unchanged; the evidence is still index ETFs, not beaten-down single stocks.

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
- [ ] Approval bot: Slack (Socket Mode) or Telegram?
- [ ] Reuse the DITM EC2 instance or run a separate `t4g.small`? See [deploy.md](deploy.md). Separate is recommended before live money.
- [ ] Should closes count toward the daily order cap? (Currently exempt.)
- [ ] Does tastytrade's market-metrics endpoint give reliable earnings and ex-dividend dates, or is a second source needed?
- [ ] Does the account get the same spread-margin treatment in production as the dry-run shows in sandbox?

## Risks to the plan

| Risk | Mitigation |
|---|---|
| Sandbox fills don't match live fills | Phase 5 starts at one contract; compare fills to mid in the journal |
| Data gaps (missing earnings date) | Missing data = exclude the name, never assume |
| Token refresh fails mid-session | Fail closed: no orders without a valid token; alert |
| Strategy has no edge on single stocks | Journal everything; review after 30 trades before scaling |
