"""NYSE session helpers. Holidays listed through 2027; extend yearly."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
HOLIDAYS = {date.fromisoformat(d) for d in (
    "2026-01-01 2026-01-19 2026-02-16 2026-04-03 2026-05-25 2026-06-19 2026-07-03 2026-09-07 "
    "2026-11-26 2026-12-25 2027-01-01 2027-01-18 2027-02-15 2027-03-26 2027-05-31 2027-06-18 "
    "2027-07-05 2027-09-06 2027-11-25 2027-12-24").split()}
OPEN_MIN, CLOSE_MIN = 9 * 60 + 30, 16 * 60


def session_minutes(now: datetime) -> tuple[int, int] | None:
    """(minutes since open, minutes to close) or None when the market is closed."""
    n = now.astimezone(ET)
    if n.weekday() >= 5 or n.date() in HOLIDAYS:
        return None
    m = n.hour * 60 + n.minute
    return (m - OPEN_MIN, CLOSE_MIN - m) if OPEN_MIN <= m < CLOSE_MIN else None
