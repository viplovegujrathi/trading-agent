"""Exit rules on open condors (strategy.md section 6). Pure: caller supplies marks."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .structure import close_by_date


@dataclass(frozen=True)
class OpenCondor:
    ticker: str
    expiry: date
    short_put: float
    short_call: float | None
    credit: float                 # per share received at entry
    touched: bool = False         # a short strike traded through at any time (sticky)


@dataclass(frozen=True)
class ExitSignal:
    action: str                   # "hold" | "close" | "close_call_side"
    reasons: tuple[str, ...]


def update_touched(p: OpenCondor, low: float, high: float) -> bool:
    """Intraday touch counts even if price recovers."""
    return p.touched or low <= p.short_put or (p.short_call is not None and high >= p.short_call)


def evaluate(p: OpenCondor, today: date, cost_to_close: float, ex_div_before_expiry: bool,
             call_near_money: bool, holidays: set[date], cfg: dict) -> ExitSignal:
    """cost_to_close: per-share debit to close now (live mark)."""
    ex, r = cfg["exits"], []
    profit = round(p.credit - cost_to_close, 4)
    if profit >= p.credit * ex["take_profit_pct_of_max"] / 100:
        r.append("take_profit")
    if (p.expiry - today).days <= ex["close_at_dte"]:
        r.append("dte")
    if ex["close_on_short_strike_touch"] and p.touched:
        r.append("short_strike_touched")
    if ex["close_day_before_expiry"] and today >= close_by_date(p.expiry, holidays):
        r.append("day_before_expiry")
    if -profit >= round(p.credit * ex["stop_loss_multiple_of_credit"], 4):
        r.append("stop_loss")
    if r:
        return ExitSignal("close", tuple(r))
    if ex_div_before_expiry and call_near_money and p.short_call is not None:
        return ExitSignal("close_call_side", ("ex_dividend",))
    return ExitSignal("hold", ())
