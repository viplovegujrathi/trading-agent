"""End-to-end and risk.md edge cases through the engine, on the paper broker."""
import copy
from datetime import date, datetime, timezone

import pytest
from conftest import q
from trading_agent import assignment, journal, positions
from trading_agent.broker import FakeBroker
from trading_agent.engine import Candidate, close_position, monitor_cycle, open_cycle
from trading_agent.halt import is_halted
from trading_agent.orders import build_close_order
from trading_agent.paper import PaperBroker
from trading_agent.screen import Snapshot

TODAY = date(2026, 10, 27)
EXP = date(2026, 11, 27)        # Friday, 31 days out
DAY = TODAY.isoformat()


def snap(t="AAA", **kw):
    b = dict(ticker=t, price=100, high_52w=125, dma50=105, rsi14=40, iv_rank=30,
             next_earnings=None, next_ex_dividend=None)
    b.update(kw)
    return Snapshot(**b)


def cand(t="AAA", sector="Tech", chain=None, **kw):
    return Candidate(snap(t, **kw), sector, EXP, chain)


@pytest.fixture(autouse=True)
def today_is_fixed(monkeypatch):
    # journal files are named by event timestamp; pin "now" to TODAY
    real = journal.append_event

    def fixed(*a, **k):
        k.setdefault("now", datetime(2026, 10, 27, 15, tzinfo=timezone.utc))
        return real(*a, **k)
    for m in ("trading_agent.engine", "trading_agent.pipeline", "trading_agent.halt"):
        monkeypatch.setattr(f"{m}.journal.append_event" if m != "trading_agent.halt" else f"{m}.append_event", fixed)


def cycle(cands, broker, cfg, root, env="paper", since=60, to_close=300, cash=None):
    return open_cycle(cands, broker, cash if cash is not None else broker.available_cash, cfg, env,
                      TODAY, since, to_close, None, root)


def test_full_round_trip_take_profit(cfg, chain, tmp_path):
    b = PaperBroker(root=tmp_path)
    r = cycle([cand(chain=chain)], b, cfg, tmp_path)
    assert [o.status for _, o in r] == ["submitted"]
    pos = journal.load_positions(tmp_path)
    assert len(pos) == 1 and pos[0]["credit"] == 1.15
    # mark at 50% profit -> close
    out = monitor_cycle({pos[0]["ext"]: dict(cost_to_close=0.55, low=98, high=102)},
                        b, cfg, "paper", TODAY, set(), None, tmp_path)
    assert out[0][1] == "close" and "take_profit" in out[0][2]
    assert journal.load_positions(tmp_path) == []
    assert b.acct["reserved"] == 0 and b.acct["cash"] == pytest.approx(5000 + 60)   # 115 - 55 = 60


def test_screen_fail_never_reaches_broker(cfg, chain, tmp_path):
    b = FakeBroker(bp_effect=135)
    assert cycle([cand(chain=chain, rsi14=70)], b, cfg, tmp_path, cash=5000) == []
    assert b.submit_calls == 0


def test_earnings_unknown_excluded(cfg, chain, tmp_path):
    b = FakeBroker(bp_effect=135)
    assert cycle([cand(chain=chain, earnings_known=False)], b, cfg, tmp_path, cash=5000) == []


def test_trading_window_blackout(cfg, chain, tmp_path):
    b = FakeBroker(bp_effect=135)
    assert cycle([cand(chain=chain)], b, cfg, tmp_path, since=5, cash=5000) == []
    assert cycle([cand(chain=chain)], b, cfg, tmp_path, to_close=10, cash=5000) == []
    assert b.submit_calls == 0


def test_max_open_condors_stops_cycle(cfg, chain, tmp_path):
    cfg = copy.deepcopy(cfg)
    cfg["portfolio"]["max_open_condors"] = 1
    cfg["portfolio"]["max_per_sector"] = 5
    b = PaperBroker(root=tmp_path)
    r = cycle([cand("A", "S1", chain), cand("B", "S2", chain)], b, cfg, tmp_path)
    assert [o.status for _, o in r] == ["submitted", "risk_rejected"]
    assert len(journal.load_positions(tmp_path)) == 1


def test_one_per_ticker(cfg, chain, tmp_path):
    b = PaperBroker(root=tmp_path)
    cycle([cand("A", "S1", chain)], b, cfg, tmp_path)
    r = cycle([cand("A", "S1", chain)], b, cfg, tmp_path)
    assert r[0][1].status == "risk_rejected"


def test_sector_limit_two(cfg, chain, tmp_path):
    b = PaperBroker(root=tmp_path)
    r = cycle([cand("A", "Tech", chain), cand("B", "Tech", chain), cand("C", "Tech", chain)], b, cfg, tmp_path)
    assert sorted(o.status for _, o in r) == ["risk_rejected", "submitted", "submitted"]


def test_daily_order_cap_blocks_opens_but_not_closes(cfg, chain, tmp_path):
    cfg = copy.deepcopy(cfg)
    cfg["portfolio"]["max_orders_per_day"] = 1
    b = PaperBroker(root=tmp_path)
    r = cycle([cand("A", "S1", chain), cand("B", "S2", chain)], b, cfg, tmp_path)
    assert sorted(o.status for _, o in r) == ["risk_rejected", "submitted"]
    p = journal.load_positions(tmp_path)[0]
    assert close_position(p, 0.50, b, cfg, "paper", TODAY, None, tmp_path).status == "submitted"


def test_halt_blocks_opens_allows_closes(cfg, chain, tmp_path):
    b = PaperBroker(root=tmp_path)
    cycle([cand(chain=chain)], b, cfg, tmp_path)
    (tmp_path / "state" / "HALT").write_text("manual")
    r = cycle([cand("B", "Other", chain)], b, cfg, tmp_path)
    assert r[0][1].status == "risk_rejected"
    p = journal.load_positions(tmp_path)[0]
    assert close_position(p, 0.50, b, cfg, "paper", TODAY, None, tmp_path).status == "submitted"


def test_daily_loss_trips_halt(cfg, chain, tmp_path):
    b = PaperBroker(root=tmp_path)
    cycle([cand(chain=chain)], b, cfg, tmp_path)
    p = journal.load_positions(tmp_path)[0]
    close_position(p, 6.50, b, cfg, "paper", TODAY, None, tmp_path)      # loss (1.15-6.50)*100 = -535
    assert positions.realized_today(DAY, tmp_path) == -535
    assert is_halted(tmp_path)


def test_cash_buffer_blocks_when_account_too_small(cfg, chain, tmp_path):
    b = PaperBroker(start_cash=1100, root=tmp_path)
    r = cycle([cand(chain=chain)], b, cfg, tmp_path)
    assert r[0][1].status == "risk_rejected" and journal.load_positions(tmp_path) == []


def test_unknown_submit_halts_and_does_not_resend(cfg, chain, tmp_path):
    class Lost(FakeBroker):
        def submit(self, order):
            self.submit_calls += 1
            raise TimeoutError
    b = Lost(bp_effect=135)
    r = cycle([cand("A", "S1", chain), cand("B", "S2", chain)], b, cfg, tmp_path, cash=5000)
    assert [o.status for _, o in r] == ["unknown"] and b.submit_calls == 1
    assert is_halted(tmp_path) and journal.load_positions(tmp_path) == []


def test_timeout_after_accept_is_adopted_not_duplicated(cfg, chain, tmp_path):
    b = FakeBroker(bp_effect=135, fail_submit_once=True)
    r = cycle([cand(chain=chain)], b, cfg, tmp_path, cash=5000)
    assert r[0][1].status == "submitted" and b.submit_calls == 1


def test_call_side_thin_opens_labeled_put_spread(cfg, tmp_path):
    chain = [q("P", 95, 2.0, 2.2, -0.15), q("P", 92.5, 1.0, 1.2, -0.10),
             q("C", 110, 0.10, 0.12, 0.15), q("C", 112.5, 0.05, 0.07, 0.08)]
    b = PaperBroker(root=tmp_path)
    r = cycle([cand(chain=chain)], b, cfg, tmp_path)
    assert r[0][1].status == "submitted"
    p = journal.load_positions(tmp_path)[0]
    assert p["short_call"] is None
    assert len(b.acct["orders"]) == 1


def test_short_strike_touch_then_recover_still_closes(cfg, chain, tmp_path):
    b = PaperBroker(root=tmp_path)
    cycle([cand(chain=chain)], b, cfg, tmp_path)
    p = journal.load_positions(tmp_path)[0]
    out = monitor_cycle({p["ext"]: dict(cost_to_close=1.20, low=94.0, high=101)}, b, cfg, "paper",
                        TODAY, set(), None, tmp_path)
    assert "short_strike_touched" in out[0][2] and journal.load_positions(tmp_path) == []


def test_holiday_week_closes_wednesday(cfg, chain, tmp_path):
    b = PaperBroker(root=tmp_path)
    cycle([cand(chain=chain)], b, cfg, tmp_path)
    p = journal.load_positions(tmp_path)[0]
    hol = {date(2026, 11, 26)}                                   # Thanksgiving Thursday
    out = monitor_cycle({p["ext"]: dict(cost_to_close=1.10, low=98, high=102)}, b, cfg, "paper",
                        date(2026, 11, 25), hol, None, tmp_path)
    assert "day_before_expiry" in out[0][2]


def test_missing_mark_never_closes_blindly(cfg, chain, tmp_path):
    b = PaperBroker(root=tmp_path)
    cycle([cand(chain=chain)], b, cfg, tmp_path)
    assert monitor_cycle({}, b, cfg, "paper", TODAY, set(), None, tmp_path) == []
    assert len(journal.load_positions(tmp_path)) == 1


def test_production_unproven_never_auto_closes_or_opens(cfg, chain, tmp_path):
    b = FakeBroker(bp_effect=135)
    r = cycle([cand(chain=chain)], b, cfg, tmp_path, env="production", cash=5000)
    assert r[0][1].status == "awaiting_approval" and b.submit_calls == 0


def test_close_order_is_single_multileg_debit():
    pos = dict(ticker="AAA", expiry="2026-11-27", contracts=1, short_put=95, long_put=92.5,
               short_call=110, long_call=112.5)
    o = build_close_order(pos, 0.55, "id")
    assert o["price-effect"] == "Debit" and o["order-type"] == "Limit" and len(o["legs"]) == 4
    assert sorted(l["action"] for l in o["legs"]) == ["Buy to Close"] * 2 + ["Sell to Close"] * 2


def test_assignment_halts_and_proposes_closing_rest(tmp_path):
    pos = dict(ticker="AAA", contracts=1, short_put=95, long_put=92.5, short_call=110,
               long_call=112.5, ext="e1")
    legs = {"short_put": 0, "long_put": 1, "short_call": -1, "long_call": 1}   # short put assigned
    r = assignment.handle(pos, legs, "paper", tmp_path)
    assert r["assigned_legs"] == ["short_put"] and sorted(r["remaining_legs"]) == ["long_call", "long_put", "short_call"]
    assert is_halted(tmp_path)


def test_no_assignment_no_halt(tmp_path):
    pos = dict(ticker="AAA", contracts=1, short_put=95, long_put=92.5, short_call=None, long_call=None, ext="e1")
    assert assignment.handle(pos, {"short_put": -1, "long_put": 1}, "paper", tmp_path) is None
    assert not is_halted(tmp_path)


def test_positions_add_is_idempotent(tmp_path):
    rec = dict(ext="e1", ticker="A", sector="S")
    positions.add_position(rec, tmp_path)
    positions.add_position(rec, tmp_path)
    assert len(journal.load_positions(tmp_path)) == 1
