# Strategy

**Thesis:** a large-cap stock that has fallen 15%+ from its 52-week high and sits 3–8% below its 50-day average tends to range between nearby support and that average. Sell a put credit spread below support and a call credit spread at or above the 50-day average, in one iron condor.

The call side adds credit **without adding collateral**: the broker holds the wider wing minus the total credit.

## 1. Universe and base screen

A name must pass every rule. Missing data = fail.

| Rule | Value |
|---|---|
| Universe | S&P 500 constituents (list dated; warn if older than 30 days) |
| Off 52-week high | ≥ 15% |
| Below 50-day average | 3% to 8% (over ~10–12% below = broken down, exclude) |
| Earnings | None before expiry |
| Ex-dividend | None before expiry (short calls get assigned early for dividends) |
| RSI(14) | < 60 |
| Liquidity | Open interest ≥ 500 at each short strike; short-leg bid-ask ≤ 10% of mid |
| IV rank | ≥ 25 preferred; below 25 allowed only if credit rules still pass, and flagged |

## 2. Expiry

Listed Friday expiry 30–45 days out, confirmed against the chain. Default target 30–35 (matches the put agent) until the 45-DTE question in [plan.md](plan.md) is settled.

## 3. Strikes

| Leg | Rule |
|---|---|
| Short put | ≥ 3% OTM and delta ≤ 0.20 |
| Long put | $2.50–$10 below the short put |
| Short call | ≥ max(50-day average, price × 1.05) and delta ≤ 0.20 |
| Long call | $2.50–$10 above the short call; call wing ≤ put wing |

If the call side brings < $0.15/share, drop it and propose the plain put spread, labeled as a fallback.

## 4. Sizing

```
total_credit  = put_credit + call_credit            (per share)
collateral    = max(put_wing, call_wing) × 100 − total_credit × 100
max_profit    = total_credit × 100
max_loss      = collateral
breakeven_low = short_put  − total_credit
breakeven_hi  = short_call + total_credit
```

- Collateral ≤ $1,000 per condor.
- Total credit ≥ $100 per condor.
- Prefer the narrowest wings that clear $100. Never move a short strike toward the money to hit the number — skip the name.
- One contract per condor until Phase 5 scaling.

**Worked example.** $5 wings both sides, $1.00 put credit + $0.60 call credit = $1.60. Collateral $500 − $160 = $340; max profit $160. The put spread alone would hold $400 for $100.

## 5. Pricing

- Limit at the mid of the four-leg net credit.
- Not filled in 10 minutes → one step to mid − $0.05. Still not filled → cancel and journal.
- Never below the $100 minimum credit.

## 6. Exits

| Trigger | Action |
|---|---|
| 50% of max profit | Close the whole condor |
| 21 DTE | Close (or roll, with approval) |
| Underlying touches either short strike | Close, or roll the untested side toward price |
| Ex-dividend appears before expiry with short call near the money | Close the call side |
| Trading day before expiry | Close; never hold into expiration day |
| Loss reaches 2× credit received | Close |

## 7. Portfolio

- Max 4 open condors on $5,000; keep ≥ $1,000 cash.
- One condor per ticker; ≤ 2 per GICS sector.

## Evidence

- tastylive SPY condor study (45 DTE, 2007 on): win rate 53% held to expiry, 62% closing at 50% of max profit, 70% also requiring IV rank > 35. Index ETF data from a broker's own research.
- tastylive SPX: 16-delta short strikes won far more often than 40-delta; 40-delta earned more per trade.
- Cboe S&P 500 Iron Condor Index: ~19% worst drawdown vs ~51% for the S&P 500 over ~35 years, but returns lag the index.
- Overbought stocks often keep rising (SentimenTrader, 97 S&P 500 oversold-to-overbought swings since 1928) — the reason there is no standalone overbought call-spread screen.

None of these test condors on beaten-down single stocks. The journal is the real test; review after 30 closed trades.

Sources:
- https://www.tastylive.com/shows/best-practices/episodes/zero-sum-10-16-2018
- https://www.tastylive.com/shows/market-measures/episodes/backtesting-iron-condors-in-spx-06-29-2016
- https://www.cboe.com/insights/posts/benchmark-indices-series-volatility-management-with-cboes-bfly-and-cndr-indices/
- https://sentimentrader.com/blog/when-momentum-continues-despite-an-overbought-condition
