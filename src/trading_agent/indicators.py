"""Pure numeric helpers: moving average, RSI, 52-week high, Black-Scholes."""
from __future__ import annotations

import math
from typing import Sequence


def sma(closes: Sequence[float], n: int) -> float | None:
    if len(closes) < n or n <= 0:
        return None
    return sum(closes[-n:]) / n


def rsi(closes: Sequence[float], n: int = 14) -> float | None:
    """Wilder's RSI over the full series. Needs at least n+1 closes."""
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for prev, cur in zip(closes[:-1], closes[1:]):
        change = cur - prev
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        avg_gain = (avg_gain * (n - 1) + g) / n
        avg_loss = (avg_loss * (n - 1) + l) / n
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def high_52w(highs: Sequence[float], bars: int = 252) -> float | None:
    if not highs:
        return None
    if len(highs) < bars * 0.9:  # not enough history to call it a 52-week high
        return None
    return max(highs[-bars:])


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_delta(spot: float, strike: float, t_years: float, iv: float, right: str, r: float = 0.04) -> float:
    """Black-Scholes delta. Puts negative. Used only when the broker gives no delta (label: modeled)."""
    if t_years <= 0 or iv <= 0:
        if right == "C":
            return 1.0 if spot > strike else 0.0
        return -1.0 if spot < strike else 0.0
    d1 = (math.log(spot / strike) + (r + iv * iv / 2) * t_years) / (iv * math.sqrt(t_years))
    return _norm_cdf(d1) if right == "C" else _norm_cdf(d1) - 1


def bs_price(spot: float, strike: float, t_years: float, iv: float, right: str, r: float = 0.04) -> float:
    if t_years <= 0 or iv <= 0:
        return max(0.0, spot - strike) if right == "C" else max(0.0, strike - spot)
    sq = iv * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + iv * iv / 2) * t_years) / sq
    d2 = d1 - sq
    disc = math.exp(-r * t_years)
    if right == "C":
        return spot * _norm_cdf(d1) - strike * disc * _norm_cdf(d2)
    return strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
