from datetime import date
from trading_agent.structure import (Condor, build_condor, close_by_date, pick_expiry)
from conftest import q


def test_sizing_matches_worked_example():
    # strategy.md: $5 wings, $1.00 + $0.60 credit -> collateral 340, profit 160
    c = Condor(95, 90, 110, 115, 1.00, 0.60)
    assert c.total_credit == 1.60 and c.collateral == 340 and c.max_profit == 160
    assert c.breakeven_low == 93.40 and c.breakeven_high == 111.60


def test_build_condor(cfg, chain):
    c = build_condor(100, 105, chain, cfg)
    assert c and not c.fallback
    assert (c.short_put, c.long_put, c.short_call, c.long_call) == (95, 92.5, 110, 112.5)  # narrowest wings
    assert c.total_credit == 1.15 and c.collateral == 135
    assert c.collateral <= 1000 and c.max_profit >= 100


def test_call_side_too_thin_falls_back_to_put_spread(cfg):
    chain = [q("P", 95, 2.0, 2.2, -0.15), q("P", 90, 0.45, 0.55, -0.07),
             q("C", 110, 0.10, 0.12, 0.15), q("C", 115, 0.05, 0.07, 0.04)]
    c = build_condor(100, 105, chain, cfg)
    assert c and c.fallback and c.short_call is None and c.call_credit == 0


def test_short_strike_not_moved_toward_money(cfg):
    # only strike in range has delta .30 -> skip the name
    chain = [q("P", 97, 3.0, 3.2, -0.30), q("P", 92, 1.0, 1.2, -0.12)]
    assert build_condor(100, 105, chain, cfg) is None   # 97 is within 3% OTM -> rejected


def test_illiquid_short_leg_rejected(cfg, chain):
    chain = [q("P", 95, 1.0, 2.0, -0.15) if x.strike == 95 and x.kind == "P" else x
             for x in chain]
    c = build_condor(100, 105, chain, cfg)
    assert c is None or c.short_put != 95


def test_call_wing_never_wider_than_put_wing(cfg, chain):
    c = build_condor(100, 105, chain, cfg)
    assert c.call_wing <= c.put_wing


def test_pick_expiry_friday_only(cfg):
    today = date(2026, 10, 25)
    listed = [date(2026, 11, 26), date(2026, 11, 27), date(2026, 12, 4), date(2026, 12, 18)]
    assert pick_expiry(listed, today, cfg) == date(2026, 11, 27)   # Thursday excluded


def test_close_by_skips_holiday_shortened_thursday():
    # expiry Fri 2026-11-27 -> close Wed 11-25 if Thanksgiving Thu 11-26 is a holiday
    assert close_by_date(date(2026, 11, 27), {date(2026, 11, 26)}) == date(2026, 11, 25)
