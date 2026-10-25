"""NYSE trading calendar, session windows and time display.

Times are stored in UTC and displayed in America/Chicago. Session logic uses
America/New_York because that is how the exchange defines it.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
CHICAGO = ZoneInfo("America/Chicago")

# Full-day NYSE closures. Extend before each new year (runbook weekly review).
HOLIDAYS: frozenset[date] = frozenset(
    date.fromisoformat(d)
    for d in [
        # 2026
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
        "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
        # 2027
        "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
        "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
    ]
)

# 13:00 ET early closes.
EARLY_CLOSES: frozenset[date] = frozenset(
    date.fromisoformat(d) for d in ["2026-11-27", "2026-12-24", "2027-11-26"]
)

OPEN = time(9, 30)
CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_display(ts: datetime) -> str:
    return ts.astimezone(CHICAGO).strftime("%Y-%m-%d %H:%M %Z")


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def previous_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def trading_days_between(start: date, end: date) -> int:
    """Trading days in (start, end]."""
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if is_trading_day(d):
            n += 1
    return n


def session_bounds(d: date) -> tuple[datetime, datetime] | None:
    if not is_trading_day(d):
        return None
    close = EARLY_CLOSE if d in EARLY_CLOSES else CLOSE
    return datetime.combine(d, OPEN, NY), datetime.combine(d, close, NY)


def in_trading_window(now: datetime, buffer_minutes: int) -> bool:
    """True if the market is open and we are not in the first/last buffer minutes."""
    local = now.astimezone(NY)
    bounds = session_bounds(local.date())
    if bounds is None:
        return False
    start, end = bounds
    return start + timedelta(minutes=buffer_minutes) <= local <= end - timedelta(minutes=buffer_minutes)


def market_open(now: datetime) -> bool:
    return in_trading_window(now, 0)


def is_standard_expiry(d: date) -> bool:
    """A Friday expiry, or a Thursday when that Friday is an exchange holiday."""
    if d.weekday() == 4:
        return is_trading_day(d)
    if d.weekday() == 3:
        return (d + timedelta(days=1)) in HOLIDAYS and is_trading_day(d)
    return False


def last_close_day(expiry: date) -> date:
    """The trading day before expiry: we never hold into expiration day."""
    return previous_trading_day(expiry)
