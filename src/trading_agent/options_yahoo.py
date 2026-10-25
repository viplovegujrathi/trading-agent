"""Option chains from Yahoo (yfinance). Yahoo gives no greeks, so delta is computed from
implied volatility with Black-Scholes. All values are 'modeled', not broker quotes; the
real tastytrade feed replaces this later. Missing or zero quotes make a strike illiquid,
which the structure rules then reject."""
from __future__ import annotations

import math
import warnings
from datetime import date

from .structure import Quote

warnings.filterwarnings("ignore")
RATE = 0.04


def _cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_delta(kind: str, spot: float, strike: float, dte: int, iv: float, r: float = RATE) -> float:
    if iv <= 0 or dte <= 0 or spot <= 0 or strike <= 0:
        return 1.0 if (kind == "C" and spot > strike) else (-1.0 if kind == "P" and spot < strike else 0.0)
    t = dte / 365
    d1 = (math.log(spot / strike) + (r + iv * iv / 2) * t) / (iv * math.sqrt(t))
    return _cdf(d1) if kind == "C" else _cdf(d1) - 1


def to_quotes(rows: list[dict], kind: str, spot: float, dte: int) -> list[Quote]:
    out = []
    for r in rows:
        try:
            out.append(Quote(float(r["strike"]), kind, float(r["bid"] or 0), float(r["ask"] or 0),
                             bs_delta(kind, spot, float(r["strike"]), dte, float(r["impliedVolatility"] or 0)),
                             int(r["openInterest"] or 0)))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def listed_expiries(ticker: str) -> list[date]:
    try:
        import yfinance as yf
        return [date.fromisoformat(e) for e in yf.Ticker(ticker).options]
    except Exception:
        return []


def fetch_chain(ticker: str, expiry: date, spot: float, today: date) -> list[Quote]:
    try:
        import yfinance as yf
        oc = yf.Ticker(ticker).option_chain(expiry.isoformat())
        dte = (expiry - today).days
        return (to_quotes(oc.puts.to_dict("records"), "P", spot, dte)
                + to_quotes(oc.calls.to_dict("records"), "C", spot, dte))
    except Exception:
        return []


def cost_to_close(pos: dict, chain: list[Quote]) -> float | None:
    """Per-share debit to close the whole condor at mid; None if any leg has no quote."""
    by = {(q.kind, q.strike): q for q in chain}
    legs = [("P", pos["short_put"], 1), ("P", pos["long_put"], -1)]
    if pos.get("short_call") is not None:
        legs += [("C", pos["short_call"], 1), ("C", pos["long_call"], -1)]
    total = 0.0
    for kind, strike, sign in legs:
        q = by.get((kind, strike))
        if q is None or q.bid <= 0 and q.ask <= 0:
            return None
        total += sign * q.mid
    return round(max(total, 0.0), 2)
