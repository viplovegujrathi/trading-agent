"""Download daily bars into data/history/<TICKER>.csv (date,high,low,close).

    python scripts/fetch_history.py              # every name in data/sp500.csv
    python scripts/fetch_history.py AAPL MSFT

Source: Stooq's free daily CSV. Any source works as long as the output columns
match; marketdata.CsvHistory reads the files. Run it after the close each day
(or before the 9:45 screen) so the 50-day average and RSI are current.
"""
from __future__ import annotations

import csv
import io
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "history"
URL = "https://stooq.com/q/d/l/?s={sym}.us&i=d"


def tickers_from_universe() -> list[str]:
    lines = (ROOT / "data" / "sp500.csv").read_text().splitlines()[1:]
    return [r["ticker"] for r in csv.DictReader(lines)]


def fetch(ticker: str, session: requests.Session) -> int:
    r = session.get(URL.format(sym=ticker.lower().replace(".", "-")), timeout=30)
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.text)))
    rows = [x for x in rows if x.get("Date") and x.get("Close")][-300:]
    if not rows:
        return 0
    with (OUT / f"{ticker.upper()}.csv").open("w") as fh:
        fh.write("date,high,low,close\n")
        for x in rows:
            fh.write(f"{x['Date']},{x['High']},{x['Low']},{x['Close']}\n")
    return len(rows)


def main(argv: list[str]) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    names = [a.upper() for a in argv] or tickers_from_universe()
    s = requests.Session()
    s.headers["User-Agent"] = "trading-agent/0.1.0"
    failed = []
    for t in names:
        try:
            n = fetch(t, s)
            if n == 0:
                failed.append(t)
        except requests.RequestException:
            failed.append(t)
        time.sleep(0.2)
    print(f"{len(names) - len(failed)} ok, {len(failed)} failed{': ' + ' '.join(failed[:20]) if failed else ''}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
