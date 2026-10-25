"""Yahoo Finance history via yfinance (unofficial, free). Used for screening only.
Every value from here is labeled 'modeled': never used for order limits, which come
from broker quotes. Any failure returns None so the screener excludes the name."""
from __future__ import annotations

import warnings
from datetime import date

from . import indicators as ind
from .screen import Snapshot

warnings.filterwarnings("ignore")


def fetch_snapshot(ticker: str, iv_rank: float | None = None) -> Snapshot | None:
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        h = t.history(period="1y", interval="1d", auto_adjust=False)
        closes, highs = h["Close"].dropna().tolist(), h["High"].dropna().tolist()
        if len(closes) < 200:
            return None
        cal = t.calendar or {}
        earn = cal.get("Earnings Date") or []
        exdiv = cal.get("Ex-Dividend Date")
        return Snapshot(
            ticker=ticker, price=closes[-1], high_52w=ind.high_52w(closes, highs),
            dma50=ind.sma(closes, 50), rsi14=ind.rsi(closes), iv_rank=iv_rank,
            next_earnings=min((d for d in earn if d >= date.today()), default=None),
            next_ex_dividend=exdiv if isinstance(exdiv, date) and exdiv >= date.today() else None,
            earnings_known=bool(earn), dividend_known=exdiv is not None or "Dividend Rate" in (t.info or {}),
        )
    except Exception:
        return None


def range_since(rows: list[tuple[date, float, float]], since: date) -> tuple[float, float] | None:
    """(low, high) over daily bars from `since` on. A daily bar holds that day's intraday
    extremes, so a touch that recovered by the close is still seen."""
    sel = [(lo, hi) for d, lo, hi in rows if d >= since]
    return (min(l for l, _ in sel), max(h for _, h in sel)) if sel else None


def fetch_range(ticker: str, since: date) -> tuple[float, float] | None:
    try:
        import yfinance as yf
        h = yf.Ticker(ticker).history(period="3mo", interval="1d", auto_adjust=False)
        rows = [(i.date(), float(r["Low"]), float(r["High"])) for i, r in h.iterrows()]
        return range_since(rows, since)
    except Exception:
        return None
