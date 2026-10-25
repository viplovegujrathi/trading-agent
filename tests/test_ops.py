from datetime import date, datetime, timedelta, timezone

import pytest
from trading_agent import halt, journal
from trading_agent.approval import Ticket, process_reply
from trading_agent.broker import FakeBroker, SubmitUnknown, submit_safely
from trading_agent.monitor import OpenCondor, evaluate, update_touched

P = OpenCondor("X", date(2026, 12, 4), 95, 110, 1.60)


def ev(p=P, today=date(2026, 11, 5), cost=1.20, exdiv=False, near=False, cfg=None):
    return evaluate(p, today, cost, exdiv, near, set(), cfg)


def test_hold(cfg):
    assert ev(cfg=cfg).action == "hold"


def test_take_profit(cfg):
    assert "take_profit" in ev(cost=0.80, cfg=cfg).reasons


def test_dte_21(cfg):
    assert "dte" in ev(today=date(2026, 11, 13), cfg=cfg).reasons


def test_day_before_expiry(cfg):
    assert "day_before_expiry" in ev(today=date(2026, 12, 3), cfg=cfg).reasons


def test_stop_loss_two_x_credit(cfg):
    assert "stop_loss" in ev(cost=4.80, cfg=cfg).reasons


def test_touch_is_sticky(cfg):
    t = update_touched(P, low=94.9, high=100)
    assert t
    p2 = OpenCondor("X", P.expiry, 95, 110, 1.6, touched=t)
    assert "short_strike_touched" in ev(p=p2, cfg=cfg).reasons


def test_ex_dividend_closes_call_side(cfg):
    assert ev(exdiv=True, near=True, cfg=cfg).action == "close_call_side"


def test_halt_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv("TA_HALT", raising=False)
    assert not halt.is_halted(tmp_path)
    halt.set_halt("test", tmp_path)
    assert halt.is_halted(tmp_path)
    with pytest.raises(ValueError):
        halt.clear_halt(" ", tmp_path)
    halt.clear_halt("ok", tmp_path)
    assert not halt.is_halted(tmp_path)


def test_env_halt(tmp_path, monkeypatch):
    monkeypatch.setenv("TA_HALT", "1")
    assert halt.is_halted(tmp_path)


def test_journal_and_positions(tmp_path):
    now = datetime(2026, 10, 27, 15, tzinfo=timezone.utc)
    journal.append_event("proposal", "X", "id1", root=tmp_path, now=now, credit=1.6)
    journal.append_event("fill", "X", "id1", root=tmp_path, now=now)
    ev_ = journal.read_events("2026-10-27", tmp_path)
    assert [e["event"] for e in ev_] == ["proposal", "fill"] and ev_[0]["env"] == "sandbox"
    journal.save_positions([{"ticker": "X"}], tmp_path)
    assert journal.load_positions(tmp_path) == [{"ticker": "X"}]


T0 = datetime(2026, 10, 27, 15, tzinfo=timezone.utc)


def test_approval_exact_ticket_only():
    t = Ticket("ic-X-1", T0, "s")
    process_reply(t, "YES ic-OTHER", "me", "me", T0)
    assert t.state == "awaiting"
    process_reply(t, "YES ic-X-1", "stranger", "me", T0)
    assert t.state == "awaiting"
    process_reply(t, "yes ic-X-1", "me", "me", T0 + timedelta(minutes=5))
    assert t.state == "approved"


def test_approval_expires():
    t = Ticket("ic-X-1", T0, "s")
    process_reply(t, "YES ic-X-1", "me", "me", T0 + timedelta(minutes=16))
    assert t.state == "expired"


def test_timeout_adopts_existing_order_no_resend():
    b = FakeBroker(fail_submit_once=True)
    r = submit_safely(b, {"external-identifier": "id1"})
    assert r["status"] == "Working" and b.submit_calls == 1


def test_unknown_status_raises_not_resends():
    class Lost(FakeBroker):
        def submit(self, order):
            self.submit_calls += 1
            raise TimeoutError

    b = Lost()
    with pytest.raises(SubmitUnknown):
        submit_safely(b, {"external-identifier": "id1"})
    assert b.submit_calls == 1


def test_indicators():
    from trading_agent import indicators as i
    assert i.sma([1, 2, 3, 4], 2) == 3.5 and i.sma([1], 2) is None
    assert i.rsi([float(x) for x in range(1, 20)]) == 100.0
    assert i.rsi([1.0, 2.0]) is None
    flat_down = [float(x) for x in range(30, 10, -1)]
    assert i.rsi(flat_down) == 0.0
