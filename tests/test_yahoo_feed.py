from datetime import datetime, timezone

import pytest
from trading_agent.calendar_us import session_minutes
from trading_agent.options_yahoo import bs_delta, cost_to_close, to_quotes
from conftest import q


def test_delta_sanity():
    assert bs_delta("C", 100, 100, 30, 0.3) == pytest.approx(0.53, abs=0.03)
    assert bs_delta("P", 100, 100, 30, 0.3) == pytest.approx(-0.47, abs=0.03)
    assert abs(bs_delta("P", 100, 80, 30, 0.3)) < 0.01       # far OTM put
    assert bs_delta("C", 100, 70, 30, 0.3) > 0.99            # deep ITM call
    assert -1 < bs_delta("P", 100, 100, 30, 0.2) < 0


def test_delta_degenerate_inputs_do_not_crash():
    assert bs_delta("C", 100, 110, 30, 0.0) == 0.0
    assert bs_delta("P", 100, 110, 0, 0.5) == -1.0


def test_to_quotes_skips_bad_rows_and_zero_quotes_are_illiquid():
    rows = [{"strike": 95, "bid": 1.0, "ask": 1.1, "openInterest": 900, "impliedVolatility": 0.3},
            {"strike": 90, "bid": None, "ask": None, "openInterest": None, "impliedVolatility": 0.3},
            {"bad": 1}]
    qs = to_quotes(rows, "P", 100, 30)
    assert len(qs) == 2 and qs[1].spread_pct_of_mid > 1e6 and qs[1].open_interest == 0


def test_cost_to_close_and_missing_leg():
    chain = [q("P", 95, 1.45, 1.55, -.15), q("P", 92.5, 0.70, 0.80, -.1),
             q("C", 110, 0.68, 0.72, .15), q("C", 112.5, 0.25, 0.35, .08)]
    pos = dict(short_put=95, long_put=92.5, short_call=110, long_call=112.5)
    assert cost_to_close(pos, chain) == 1.15
    assert cost_to_close(pos, chain[:3]) is None
    assert cost_to_close(dict(short_put=95, long_put=92.5, short_call=None, long_call=None), chain) == 0.75


def test_session_minutes():
    # Tue 2026-10-27 10:00 ET = 14:00 UTC (EDT)
    assert session_minutes(datetime(2026, 10, 27, 14, 0, tzinfo=timezone.utc)) == (30, 360)
    assert session_minutes(datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)) is None   # pre-open
    assert session_minutes(datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc)) is None   # Saturday
    assert session_minutes(datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc)) is None   # Thanksgiving
