"""Pure price-history indicators. Inputs are daily closes, oldest first."""
from __future__ import annotations


def sma(closes: list[float], n: int) -> float | None:
    return sum(closes[-n:]) / n if len(closes) >= n else None


def high_52w(closes: list[float], highs: list[float] | None = None) -> float | None:
    src = highs if highs is not None else closes
    return max(src[-252:]) if len(src) >= 200 else None


def rsi(closes: list[float], n: int = 14) -> float | None:
    """Wilder's RSI."""
    if len(closes) < n + 1:
        return None
    d = [b - a for a, b in zip(closes, closes[1:])]
    ag = sum(x for x in d[:n] if x > 0) / n
    al = sum(-x for x in d[:n] if x < 0) / n
    for x in d[n:]:
        ag = (ag * (n - 1) + max(x, 0)) / n
        al = (al * (n - 1) + max(-x, 0)) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
