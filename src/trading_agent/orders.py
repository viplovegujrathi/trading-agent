"""Multi-leg order JSON for tastytrade, and external identifiers.

Only limit orders are ever built. Condors open and close as one order.
"""
from __future__ import annotations

import math
import re
import uuid
from datetime import date
from typing import Any

from .models import Leg, Proposal

CLOSE_ACTION = {"Buy to Open": "Sell to Close", "Sell to Open": "Buy to Close"}
_EXT_RE = re.compile(r"^(ic|cl)-[A-Z./]{1,6}-\d{8}-[0-9a-f]{8}$")


def occ_symbol(root: str, expiry: date, right: str, strike: float) -> str:
    """tastytrade OCC format: root padded to 6, YYMMDD, P/C, strike x1000 in 8 digits."""
    return f"{root.upper():<6}{expiry:%y%m%d}{right}{int(round(strike * 1000)):08d}"


def new_external_identifier(ticker: str, expiry: date, kind: str = "open", uid: str | None = None) -> str:
    prefix = "ic" if kind == "open" else "cl"
    return f"{prefix}-{ticker.upper()}-{expiry:%Y%m%d}-{uid or uuid.uuid4().hex[:8]}"


def valid_external_identifier(ext: str) -> bool:
    return bool(_EXT_RE.match(ext))


def _price_str(x: float) -> str:
    return f"{x:.2f}"


def build_order(legs: list[Leg], limit: float, effect: str, external_identifier: str) -> dict[str, Any]:
    if effect not in ("Credit", "Debit"):
        raise ValueError("price effect must be Credit or Debit")
    if limit <= 0:
        raise ValueError("limit must be positive")
    if len(legs) not in (2, 4):
        raise ValueError("multi-leg order must have 2 or 4 legs")
    return {
        "time-in-force": "Day",
        "order-type": "Limit",
        "price": _price_str(limit),
        "price-effect": effect,
        "external-identifier": external_identifier,
        "legs": [
            {
                "instrument-type": "Equity Option",
                "symbol": l.symbol,
                "quantity": l.quantity,
                "action": l.action,
            }
            for l in legs
        ],
    }


def build_open_order(p: Proposal, external_identifier: str, limit: float | None = None) -> dict[str, Any]:
    return build_order(p.legs, p.total_credit if limit is None else limit, "Credit", external_identifier)


def reprice_limit(mid_credit: float, step: float, floor_credit_per_share: float) -> float | None:
    """One step below mid, never below the minimum credit. None = don't reprice."""
    new = round(mid_credit - step, 2)
    if new + 1e-9 < floor_credit_per_share:
        return None
    return new


def closing_legs(position_legs: list[Leg]) -> list[Leg]:
    return [
        Leg(l.symbol, l.right, l.strike, CLOSE_ACTION[l.action], l.quantity)  # type: ignore[arg-type]
        for l in position_legs
    ]


def closing_debit_mid(quotes: dict[str, tuple[float | None, float | None]], position_legs: list[Leg]) -> float | None:
    """Mid cost per share to buy back the shorts and sell the longs. Rounded up to a cent."""
    total = 0.0
    for l in position_legs:
        bid, ask = quotes.get(l.symbol, (None, None))
        if bid is None or ask is None:
            return None
        mid = (bid + ask) / 2
        total += mid if l.action == "Sell to Open" else -mid
    return max(0.01, math.ceil(round(total * 100, 6)) / 100)


def parse_occ(symbol: str) -> tuple[str, date, str, float]:
    """Inverse of occ_symbol: (root, expiry, right, strike)."""
    root, rest = symbol[:6].strip(), symbol[6:]
    expiry = date(2000 + int(rest[0:2]), int(rest[2:4]), int(rest[4:6]))
    return root, expiry, rest[6], int(rest[7:15]) / 1000
