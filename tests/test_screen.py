from datetime import date
from trading_agent.screen import Snapshot, screen_name, universe_stale

EXP = date(2026, 12, 4)


def snap(**kw):
    base = dict(ticker="X", price=100, high_52w=125, dma50=105, rsi14=40, iv_rank=30,
                next_earnings=None, next_ex_dividend=None)
    base.update(kw)
    return Snapshot(**base)


def test_passes(cfg):
    assert screen_name(snap(), EXP, cfg).passed


def test_not_far_enough_off_high(cfg):
    assert not screen_name(snap(high_52w=110), EXP, cfg).passed


def test_broken_down_below_dma(cfg):
    assert not screen_name(snap(dma50=115), EXP, cfg).passed


def test_rsi_too_high(cfg):
    assert not screen_name(snap(rsi14=60), EXP, cfg).passed


def test_earnings_unknown_excluded(cfg):
    r = screen_name(snap(earnings_known=False), EXP, cfg)
    assert not r.passed and "earnings date unknown" in r.reasons


def test_earnings_before_expiry(cfg):
    assert not screen_name(snap(next_earnings=date(2026, 11, 20)), EXP, cfg).passed


def test_ex_dividend_before_expiry(cfg):
    assert not screen_name(snap(next_ex_dividend=date(2026, 11, 20)), EXP, cfg).passed


def test_missing_data_fails(cfg):
    assert not screen_name(snap(price=None), EXP, cfg).passed


def test_low_iv_rank_flagged_not_failed(cfg):
    r = screen_name(snap(iv_rank=10), EXP, cfg)
    assert r.passed and "low_iv_rank" in r.flags


def test_universe_stale(cfg):
    assert universe_stale(date(2026, 9, 1), date(2026, 10, 25), cfg)
    assert not universe_stale(date(2026, 10, 10), date(2026, 10, 25), cfg)
