# Runbook

## Daily

| Time (ET) | What happens | What you do |
|---|---|---|
| 9:45 | Screen runs; tickets that pass all gates are sent for approval | Reply yes/no within 15 min |
| 12:30 | Monitor checks exits | Approve any proposed closes |
| 15:15 | Monitor checks exits; day-before-expiry closes | Approve closes |
| After close | Daily summary: open condors, P&L, flags | Read it |

## Commands

```bash
python -m trading_agent.cli status          # token, balances, open condors, halt state
python -m trading_agent.cli screen --dry    # candidates only
python -m trading_agent.cli monitor         # evaluate exits now
python -m trading_agent.cli close <id>      # propose a close (still needs approval)
python -m trading_agent.cli halt            # kill switch on
python -m trading_agent.cli halt --clear    # kill switch off (journal note required)
```

## Incidents

### Order status `UNKNOWN`
1. Kill switch is already on.
2. Check the tastytrade app for an order with the journal's `external-identifier`.
3. Record what you find: `cli.py resolve <external-identifier> --found <order-id>` or `--not-found`.
4. Clear the kill switch.

### Early assignment
1. Kill switch is already on.
2. Check the account for shares created by the assignment.
3. Approve the proposed close of the remaining legs and the stock position the same day.
4. Journal the cost; clear the kill switch.

### Token failure
1. Run `cli.py status`. If the refresh token is rejected, redo the OAuth flow ([setup.md](setup.md) §2).
2. Update `.env`; run `status` again.

### Fill far from mid
Journal records fill vs mid. If slippage averages over $0.10 across 5 trades, raise the liquidity thresholds in `strategy.yaml`.

## Weekly review
- Closed trades: win rate, average credit, average loss, return on collateral.
- Any limit breaches or kill-switch trips.
- After 30 closed live trades: decide whether to scale, adjust, or stop.
