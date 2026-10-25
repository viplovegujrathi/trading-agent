from datetime import date

from conftest import TODAY, snap

from trading_agent.screen import load_universe, screen_horizon, screen_name

H = date(2026, 12, 10)


def run(cfg, **kw):
    return screen_name(snap(**kw), cfg.screen, H, TODAY)


def test_passing_name(cfg):
    r = run(cfg)
    assert r.passed, r.reasons
    assert r.pct_off_high == 20.0
    assert r.pct_below_sma50 == 4.76


def test_not_far_enough_off_high(cfg):
    assert not run(cfg, high_52w=110.0).passed


def test_below_50dma_band(cfg):
    assert not run(cfg, sma50=101.0).passed          # under 3% below
    assert not run(cfg, sma50=112.0).passed          # ~10.7% below: broken down
    assert run(cfg, sma50=103.2).passed              # ~3.1% below


def test_rsi_cap(cfg):
    assert not run(cfg, rsi14=60.0).passed
    assert run(cfg, rsi14=59.9).passed


def test_missing_data_fails(cfg):
    r = run(cfg, sma50=None)
    assert not r.passed and "missing data" in r.reasons[0]
    assert not run(cfg, sector=None).passed


def test_earnings_unknown_excluded(cfg):
    r = run(cfg, earnings_known=False, earnings_date=None)
    assert not r.passed and "earnings date unknown" in r.reasons


def test_earnings_before_expiry_excluded(cfg):
    assert not run(cfg, earnings_date=date(2026, 11, 15)).passed


def test_ex_dividend_rules(cfg):
    assert not run(cfg, dividend_known=False).passed
    assert not run(cfg, ex_dividend_date=date(2026, 11, 2)).passed
    assert run(cfg, ex_dividend_date=date(2027, 2, 1)).passed


def test_low_iv_rank_flagged_not_failed(cfg):
    r = run(cfg, iv_rank=18.0)
    assert r.passed and any("IV rank" in f for f in r.flags)


def test_universe_file(tmp_path, cfg):
    p = tmp_path / "u.csv"
    p.write_text("# as_of: 2026-09-01\nticker,sector\naapl,Information Technology\n")
    u = load_universe(p)
    assert u.sectors == {"AAPL": "Information Technology"}
    assert u.is_stale(TODAY, cfg.screen.universe_max_age_days)
    assert screen_horizon(TODAY, cfg.structure) == date(2026, 12, 10)
