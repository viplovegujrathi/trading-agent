# data/

| Path | What | How it gets here |
|---|---|---|
| `sp500.csv` | Universe: `ticker,sector` (GICS), first line `# as_of: YYYY-MM-DD` | `python scripts/update_universe.py`. The screen warns when it is older than `screen.universe_max_age_days` |
| `history/<TICKER>.csv` | Daily bars `date,high,low,close`, oldest first, ~300 rows | `python scripts/fetch_history.py` (gitignored). Needed for the 50-day average, 52-week high and RSI(14) |
| `paper/market.json` | Paper-mode fixture: fictional tickers, spot, IV, history shape, metrics | Hand-edited. Change `spot` to move the simulated market |
| `paper/universe.csv` | Paper-mode universe | Hand-edited |

Missing history or metrics for a name means the screen excludes it; nothing is assumed.

tastytrade spells share-class tickers with a slash (`BRK/B`); the universe file uses a dot (`BRK.B`). Those names are excluded until the mapping is verified in Phase 0.
