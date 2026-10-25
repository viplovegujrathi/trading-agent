# TODO

Status as of 2026-10-25: offline core, paper broker, Yahoo feed and EC2 deploy kit are built and pushed. 99 tests pass. Nothing has run on EC2 yet.

## 1. Run on EC2 (paper mode) — you

- [ ] Create an S3 bucket for backups.
- [ ] Create an Amazon Linux 2023 instance (a separate `t4g.small` is the cleaner choice; reusing the DITM instance also works).
- [ ] Attach a role with [deploy/iam-policy.json](deploy/iam-policy.json) (replace `BUCKET`) plus `AmazonSSMManagedInstanceCore`. No SSH, no inbound security-group rules.
- [ ] In a Session Manager shell: `sudo BUCKET=<bucket> bash deploy/bootstrap.sh`.
- [ ] Check timers: `systemctl list-timers 'trading-agent*'`.
- [ ] Open the dashboard through SSM port forwarding (deploy.md, "Viewing the dashboard").
- [ ] Smoke test: `sudo -iu tagent`, then `cd app && .venv/bin/python -m trading_agent.cli run-open --force`.
- [ ] Create CloudWatch alarms: no `RunCompleted` for `run-open` by 10:15 ET or `run-monitor` by 15:45 ET; any `RunFailed`. Send to an SNS topic (email and phone).
- [ ] Optional: give Claude a configured AWS CLI on the Mac and it can provision with you watching.

## 2. Paper-trading phase

- [ ] Let the timers run for 5 trading days with no manual fixes (Plan M3).
- [ ] Review `journal/` daily: screens, proposals, fills, closes, any halt.
- [ ] Check the Yahoo data caveats: touch detection uses the latest price only (no intraday high/low); zero bid/ask quotes are treated as illiquid.
- [ ] Refresh `data/sp500.csv` monthly (stale after 30 days; the screen warns).
- [ ] Extend `HOLIDAYS` in `calendar_us.py` before 2028.

## 3. Decisions needed

- [ ] DTE target: 30–35 or 45 (default window 30–45, target 33).
- [ ] Slack or Telegram for production approvals.
- [ ] Should closes count toward the 6-orders-per-day cap? Currently they do not, so a stuck position can always be closed.
- [ ] Should production auto-close (stop-loss) wait for your yes before graduation? Currently yes, same as opens.
- [ ] Optional: a Claude desktop scheduled task that runs the skill on weekdays (creates a standing job; needs your OK).

## 4. Before production (Phase 5)

- [ ] tastytrade: sandbox account, OAuth app, refresh token. Store secrets in SSM `/trading-agent/<env>/`, never in git or `.env`.
- [ ] Build `auth.py`, `broker.py` (REST client: dry-run, submit, lookup, positions, balances) and `marketdata.py` (chains, greeks, IV rank, earnings and ex-dividend dates). Verify the endpoints in architecture.md against the sandbox.
- [ ] Replace Yahoo with broker quotes for pricing; keep the live/modeled labels.
- [ ] Add IV rank (Yahoo does not provide it).
- [ ] Build the approval bot transport (`approval.py` has the decision logic only).
- [ ] 10 sandbox condors opened and closed end to end with zero duplicate orders (Plan M2).
- [ ] Production secrets under `/trading-agent/production/`; sandbox untouched.
- [ ] Heartbeat alarm tested by stopping a timer for a day.
- [ ] Restore from S3 tested on a scratch instance.
- [ ] Approval bot rejects replies from any other user.
- [ ] Set `max_open_condors: 1` for the first 4 live weeks.
- [ ] Production auto-trading stays off until the journal shows 30 closed live condors, 0 limit breaches and 0 unknown orders (enforced in `autonomy.py`). Relaxing this is your decision.
- [ ] Keep a cron timer for `run-monitor` as a safety net even if Claude drives opens, so a Claude outage never leaves a position unmanaged.

## 5. Gaps from the plan/architecture review

- [x] Reprice after 10 minutes, cancel after another 10, never below the minimum credit (done, tested with a non-filling paper broker). The tastytrade client still needs the same cancel/replace calls.
- [x] Touch detection now uses daily high/low since entry on Yahoo (done); switch to broker data later.
- [ ] Feed real broker positions into `assignment.handle()`.
- [ ] Call-spread-only closing order for the ex-dividend exit (currently closes the whole condor).
- [ ] Put the exit reason on the `close_filled` journal line.
- [ ] 20-date hand check of the screen (Plan Phase 2 exit).

## 6. Possible later work

- [ ] Dashboard: option B is built (`cli dashboard`, loopback only, started by `bootstrap.sh`). After deploy, test the SSM port-forward command in deploy.md. The claude.ai Condor Desk artifact is an old snapshot; delete or keep it as you prefer.
- [ ] Other devices: set up Tailscale on the instance and your devices, set `DASH_HOST` and optionally `TA_DASH_PASSWORD` in `.env` (steps in deploy.md, "From your phone, laptop or other devices").
- [ ] Optional: show the latest screen table and exit reasons on the dashboard (journal the close reason first).
- [ ] Review the strategy after 30 closed trades; it is untested on single stocks.
