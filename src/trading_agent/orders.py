"""Builds the single four-leg limit order (never legs in, never market)."""
from __future__ import annotations

import uuid

from .structure import Condor


def external_identifier(ticker: str, expiry: str, uid: str | None = None) -> str:
    return f"ic-{ticker}-{expiry}-{uid or uuid.uuid4().hex[:8]}"


def _occ(ticker: str, expiry: str, kind: str, strike: float) -> str:
    yymmdd = expiry.replace("-", "")[2:]
    return f"{ticker:<6}{yymmdd}{kind}{int(round(strike * 1000)):08d}"


def build_open_order(c: Condor, ticker: str, expiry: str, contracts: int,
                     ext_id: str) -> dict:
    """expiry as YYYY-MM-DD. Credit orders use price-effect Credit; limit at mid."""
    legs = [
        (c.short_put, "P", "Sell to Open"),
        (c.long_put, "P", "Buy to Open"),
    ]
    if c.short_call is not None:
        legs += [(c.short_call, "C", "Sell to Open"), (c.long_call, "C", "Buy to Open")]
    return {
        "order-type": "Limit",
        "time-in-force": "Day",
        "price": f"{c.total_credit:.2f}",
        "price-effect": "Credit",
        "external-identifier": ext_id,
        "legs": [{"instrument-type": "Equity Option",
                  "symbol": _occ(ticker, expiry, k, s),
                  "quantity": contracts, "action": a} for s, k, a in legs],
    }


def reprice(c: Condor, cfg: dict) -> float | None:
    """One step down, never below the minimum credit. None = cancel instead."""
    new = round(c.total_credit - cfg["pricing"]["reprice_step"], 2)
    return new if new * 100 >= cfg["sizing"]["min_total_credit_usd"] else None
