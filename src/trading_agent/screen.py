"""Universe and base screen (strategy.md §1). Pure functions; missing data = fail."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .config import ScreenCfg, StructureCfg
from .models import Snapshot


@dataclass(frozen=True)
class Universe:
    as_of: date
    sectors: dict[str, str]  # ticker -> GICS sector

    def is_stale(self, today: date, max_age_days: int) -> bool:
        return (today - self.as_of).days > max_age_days

    @property
    def tickers(self) -> list[str]:
        return sorted(self.sectors)


def load_universe(path: Path) -> Universe:
    """CSV with a first line `# as_of: YYYY-MM-DD`, then `ticker,sector` rows."""
    lines = path.read_text().splitlines()
    if not lines or not lines[0].startswith("# as_of:"):
        raise ValueError(f"{path}: first line must be '# as_of: YYYY-MM-DD'")
    raw = lines[0].split(":", 1)[1].strip()
    # "today" is only used by the paper-mode fixture.
    as_of = date.today() if raw == "today" else date.fromisoformat(raw)
    reader = csv.DictReader(l for l in lines[1:] if l.strip() and not l.startswith("#"))
    sectors = {row["ticker"].strip().upper(): row["sector"].strip() for row in reader}
    return Universe(as_of=as_of, sectors=sectors)


@dataclass
class ScreenResult:
    ticker: str
    passed: bool
    reasons: list[str] = field(default_factory=list)  # why it failed
    flags: list[str] = field(default_factory=list)  # passed, but worth noting
    pct_off_high: float | None = None
    pct_below_sma50: float | None = None
    snapshot: Snapshot | None = None


def pct_off_high(price: float, high: float) -> float:
    return (high - price) / high * 100


def pct_below(price: float, avg: float) -> float:
    return (avg - price) / avg * 100


def screen_name(s: Snapshot, cfg: ScreenCfg, horizon: date, today: date) -> ScreenResult:
    """Apply every base-screen rule. `horizon` is the latest expiry we might use."""
    r = ScreenResult(ticker=s.ticker, passed=False, snapshot=s)
    missing = [
        name
        for name, value in [
            ("price", s.price),
            ("sma50", s.sma50),
            ("high_52w", s.high_52w),
            ("rsi14", s.rsi14),
            ("sector", s.sector),
        ]
        if value is None
    ]
    if missing:
        r.reasons.append(f"missing data: {', '.join(missing)}")
        return r
    assert s.price is not None and s.sma50 is not None and s.high_52w is not None and s.rsi14 is not None

    r.pct_off_high = round(pct_off_high(s.price, s.high_52w), 2)
    r.pct_below_sma50 = round(pct_below(s.price, s.sma50), 2)

    if r.pct_off_high < cfg.min_pct_off_52w_high:
        r.reasons.append(f"only {r.pct_off_high:.1f}% off 52w high (< {cfg.min_pct_off_52w_high})")
    lo, hi = cfg.pct_below_50dma
    if not lo <= r.pct_below_sma50 <= hi:
        r.reasons.append(f"{r.pct_below_sma50:.1f}% below 50dma (outside {lo}-{hi})")
    if s.rsi14 >= cfg.max_rsi14:
        r.reasons.append(f"RSI {s.rsi14:.1f} >= {cfg.max_rsi14}")

    if cfg.exclude_earnings_before_expiry:
        if not s.earnings_known:
            r.reasons.append("earnings date unknown")
        elif s.earnings_date is not None and today <= s.earnings_date <= horizon:
            r.reasons.append(f"earnings {s.earnings_date} before expiry window end")
    if cfg.exclude_ex_dividend_before_expiry:
        if not s.dividend_known:
            r.reasons.append("ex-dividend date unknown")
        elif s.ex_dividend_date is not None and today <= s.ex_dividend_date <= horizon:
            r.reasons.append(f"ex-dividend {s.ex_dividend_date} before expiry window end")

    if s.iv_rank is None:
        r.flags.append("IV rank unknown")
    elif s.iv_rank < cfg.preferred_min_iv_rank:
        r.flags.append(f"IV rank {s.iv_rank:.0f} < {cfg.preferred_min_iv_rank:.0f}")

    r.passed = not r.reasons
    return r


def screen_horizon(today: date, structure: StructureCfg) -> date:
    return today + timedelta(days=structure.dte_window[1])
