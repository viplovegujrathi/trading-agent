"""Reprice-after-10-min then cancel (strategy.md section 5), plus touch data from daily ranges."""
from datetime import date, datetime, timedelta, timezone

import pytest
from conftest import q
from trading_agent import journal, positions
from trading_agent.engine import Candidate, monitor_cycle, open_cycle, settle_working
from trading_agent.orders import reprice_credit
from trading_agent.paper import PaperBroker
from trading_agent.prices_yahoo import range_since
from trading_agent.screen import Snapshot

T0 = datetime(2026, 10, 27, 14, 0, tzinfo=timezone.utc)
TODAY = T0.date()
EXP = date(2026, 11, 27)


def cand(chain, t="AAA", sector="Tech"):
    s = Snapshot(t, 100, 125, 105, 40, 30, None, None)
    return Candidate(s, sector, EXP, chain)


def go(chain, cfg, tmp, **bk):
    b = PaperBroker(root=tmp, fill_on_submit=False, **bk)
    r = open_cycle([cand(chain)], b, b.available_cash, cfg, "paper", TODAY, 60, 300, None, tmp, now=T0)
    return b, r


def mins(m):
    return T0 + timedelta(minutes=m)


def test_unfilled_order_is_working_not_a_position(cfg, chain, tmp_path):
    b, r = go(chain, cfg, tmp_path)
    assert r[0][1].status == "submitted"
    assert journal.load_positions(tmp_path) == [] and len(positions.load_working(tmp_path)) == 1
    assert settle_working(b, cfg, "paper", mins(5), tmp_path) == []        # too early


def test_working_order_counts_against_limits_and_cash(cfg, chain, tmp_path):
    b, _ = go(chain, cfg, tmp_path)
    st = positions.portfolio_state(b.available_cash, TODAY.isoformat(), root=tmp_path)
    assert st.open_tickers == ("AAA",) and st.cash == 5000 - 135
    r = open_cycle([cand(chain)], b, b.available_cash, cfg, "paper", TODAY, 60, 300, None, tmp_path, now=T0)
    assert r[0][1].status == "risk_rejected"                              # one per ticker holds


def test_reprice_after_ten_minutes_then_fill(cfg, chain, tmp_path):
    b, _ = go(chain, cfg, tmp_path)
    ext = next(iter(positions.load_working(tmp_path)))
    assert settle_working(b, cfg, "paper", mins(11), tmp_path) == [(ext, "repriced")]
    assert b.acct["orders"][ext]["order"]["price"] == "1.10"             # 1.15 - 0.05
    b.fill(ext)
    assert settle_working(b, cfg, "paper", mins(12), tmp_path) == [(ext, "filled")]
    pos = journal.load_positions(tmp_path)[0]
    assert pos["credit"] == 1.10 and positions.load_working(tmp_path) == {}
    assert b.acct["cash"] == pytest.approx(5110)


def test_cancel_if_still_unfilled_after_reprice(cfg, chain, tmp_path):
    b, _ = go(chain, cfg, tmp_path)
    ext = next(iter(positions.load_working(tmp_path)))
    settle_working(b, cfg, "paper", mins(11), tmp_path)
    assert settle_working(b, cfg, "paper", mins(15), tmp_path) == []      # waits another full period
    assert settle_working(b, cfg, "paper", mins(22), tmp_path) == [(ext, "cancelled")]
    assert b.acct["orders"][ext]["status"] == "Cancelled"
    assert journal.load_positions(tmp_path) == [] and b.acct["cash"] == 5000


def test_never_reprices_below_minimum_credit(cfg, tmp_path):
    chain = [q("P", 95, 1.45, 1.55, -.15), q("P", 92.5, 0.45, 0.55, -.1)]   # put spread, credit 1.00
    b, _ = go(chain, cfg, tmp_path)
    ext = next(iter(positions.load_working(tmp_path)))
    assert settle_working(b, cfg, "paper", mins(11), tmp_path) == [(ext, "cancelled")]
    assert reprice_credit(1.00, cfg) is None and reprice_credit(1.20, cfg) == 1.15


def test_filled_between_polls_is_adopted_without_reprice(cfg, chain, tmp_path):
    b, _ = go(chain, cfg, tmp_path)
    ext = next(iter(positions.load_working(tmp_path)))
    b.fill(ext)
    assert settle_working(b, cfg, "paper", mins(30), tmp_path) == [(ext, "filled")]
    assert len(journal.load_positions(tmp_path)) == 1


def test_external_cancel_drops_working_order(cfg, chain, tmp_path):
    b, _ = go(chain, cfg, tmp_path)
    ext = next(iter(positions.load_working(tmp_path)))
    b.cancel(ext)
    assert settle_working(b, cfg, "paper", mins(1), tmp_path) == [(ext, "dropped")]


def test_settle_is_idempotent(cfg, chain, tmp_path):
    b, _ = go(chain, cfg, tmp_path)
    ext = next(iter(positions.load_working(tmp_path)))
    b.fill(ext)
    settle_working(b, cfg, "paper", mins(1), tmp_path)
    assert settle_working(b, cfg, "paper", mins(1), tmp_path) == []
    assert len(journal.load_positions(tmp_path)) == 1


def test_range_since_sees_intraday_touch_that_recovered():
    rows = [(date(2026, 10, 26), 97.0, 101.0), (date(2026, 10, 27), 94.2, 100.5), (date(2026, 10, 28), 99.0, 102.0)]
    assert range_since(rows, date(2026, 10, 27)) == (94.2, 102.0)
    assert range_since(rows, date(2026, 11, 1)) is None
    assert range_since(rows, date(2026, 10, 1))[0] == 94.2


def test_touch_from_range_closes_position(cfg, chain, tmp_path):
    b = PaperBroker(root=tmp_path)
    open_cycle([cand(chain)], b, b.available_cash, cfg, "paper", TODAY, 60, 300, None, tmp_path, now=T0)
    p = journal.load_positions(tmp_path)[0]
    low, high = range_since([(TODAY, 94.5, 100.2)], date.fromisoformat(p["opened"]))
    out = monitor_cycle({p["ext"]: dict(cost_to_close=1.0, low=low, high=high)}, b, cfg, "paper",
                        TODAY, set(), None, tmp_path)
    assert "short_strike_touched" in out[0][2]
