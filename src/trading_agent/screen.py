"""Universe and base screen (strategy.md section 1). Missing data = fail."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Snapshot:
    ticker: str
    price: float | None
    high_52w: float | None
    dma50: float | None
    rsi14: float | None
    iv_rank: float | None
    next_earnings: date | None
    next_ex_dividend: date | None
    earnings_known: bool = True      # False = the data source had no answer
    dividend_known: bool = True


@dataclass(frozen=True)
class ScreenResult:
    ticker: str
    passed: bool
    reasons: tuple[str, ...]
    flags: tuple[str, ...] = ()


def universe_stale(list_date: date, today: date, cfg: dict) -> bool:
    return (today - list_date).days > cfg["screen"]["universe_max_age_days"]


def screen_name(s: Snapshot, expiry: date, cfg: dict) -> ScreenResult:
    sc = cfg["screen"]
    why: list[str] = []
    flags: list[str] = []

    if None in (s.price, s.high_52w, s.dma50, s.rsi14):
        return ScreenResult(s.ticker, False, ("missing price/52w/50dma/rsi data",))

    off_high = (s.high_52w - s.price) / s.high_52w * 100
    if off_high < sc["min_pct_off_52w_high"]:
        why.append(f"only {off_high:.1f}% off 52w high")

    below = (s.dma50 - s.price) / s.dma50 * 100
    lo, hi = sc["pct_below_50dma"]
    if not lo <= below <= hi:
        why.append(f"{below:.1f}% below 50dma, need {lo}-{hi}")

    if s.rsi14 >= sc["max_rsi14"]:
        why.append(f"RSI {s.rsi14:.0f} >= {sc['max_rsi14']}")

    if sc["exclude_earnings_before_expiry"]:
        if not s.earnings_known:
            why.append("earnings date unknown")
        elif s.next_earnings is not None and s.next_earnings <= expiry:
            why.append(f"earnings {s.next_earnings} before expiry")

    if sc["exclude_ex_dividend_before_expiry"]:
        if not s.dividend_known:
            why.append("ex-dividend date unknown")
        elif s.next_ex_dividend is not None and s.next_ex_dividend <= expiry:
            why.append(f"ex-dividend {s.next_ex_dividend} before expiry")

    if s.iv_rank is None:
        why.append("IV rank missing")
    elif s.iv_rank < sc["preferred_min_iv_rank"]:
        flags.append("low_iv_rank")

    return ScreenResult(s.ticker, not why, tuple(why), tuple(flags))
