from datetime import date, datetime, timezone

import pytest

from trading_agent.config import ConfigError, StrategyConfig, load_settings, load_strategy
from trading_agent.indicators import bs_delta, high_52w, rsi, sma
from trading_agent.tradingcal import (
    in_trading_window, is_standard_expiry, is_trading_day, last_close_day, to_display,
)


def test_example_config_matches_risk_md():
    c = load_strategy(env="sandbox")
    assert c.sizing.max_collateral_usd == 1000
    assert c.sizing.min_total_credit_usd == 100
    assert c.portfolio.max_open_condors == 4
    assert c.portfolio.max_orders_per_day == 6
    assert c.structure.max_short_delta == 0.20
    assert c.approval.timeout_minutes == 15


def test_unknown_config_key_rejected():
    with pytest.raises(ConfigError):
        StrategyConfig.from_dict({"sizing": {"max_colateral_usd": 5000}})


def test_approval_cannot_be_disabled():
    with pytest.raises(ConfigError):
        StrategyConfig.from_dict({"approval": {"required": False}})


def test_production_requires_real_strategy_file(tmp_path):
    with pytest.raises(ConfigError):
        load_strategy(tmp_path / "missing.yaml", env="production")


def test_settings_default_to_sandbox():
    s = load_settings({})
    assert s.env == "sandbox" and not s.is_live and not s.halt_env


def test_settings_reject_bad_env():
    with pytest.raises(ConfigError):
        load_settings({"TT_ENV": "live"})


def test_holiday_shortened_thursday_expiry_closes_wednesday():
    # Good Friday 2027-03-26: that week's expiry is Thursday 03-25; close on Wednesday 03-24.
    assert not is_trading_day(date(2027, 3, 26))
    assert is_standard_expiry(date(2027, 3, 25))
    assert not is_standard_expiry(date(2027, 3, 26))
    assert last_close_day(date(2027, 3, 25)) == date(2027, 3, 24)


def test_regular_friday_expiry_closes_thursday():
    assert is_standard_expiry(date(2026, 11, 20))
    assert last_close_day(date(2026, 11, 20)) == date(2026, 11, 19)
    assert not is_standard_expiry(date(2026, 11, 19))  # a regular Thursday


def test_trading_window_excludes_first_and_last_15_minutes():
    day = date(2026, 10, 26)
    et = lambda h, m: datetime(day.year, day.month, day.day, h, m, tzinfo=timezone.utc).replace(hour=h + 4)  # EDT
    assert not in_trading_window(et(9, 40), 15)
    assert in_trading_window(et(9, 45), 15)
    assert in_trading_window(et(15, 45), 15)
    assert not in_trading_window(et(15, 50), 15)
    assert not in_trading_window(datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc), 15)  # Sunday


def test_display_is_chicago():
    assert to_display(datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)).endswith("CDT")


def test_sma_and_52w_high():
    assert sma([1, 2, 3, 4], 2) == 3.5
    assert sma([1], 2) is None
    assert high_52w([1.0] * 100) is None  # not enough history
    assert high_52w(list(range(300))) == 299


def test_rsi_bounds():
    up = [float(i) for i in range(30)]
    assert rsi(up) == 100.0
    down = [float(30 - i) for i in range(30)]
    assert rsi(down) == pytest.approx(0.0)
    assert rsi([1.0] * 5) is None


def test_bs_delta_sign_and_parity():
    c = bs_delta(100, 100, 0.25, 0.3, "C", r=0)
    p = bs_delta(100, 100, 0.25, 0.3, "P", r=0)
    assert 0 < c < 1 and -1 < p < 0
    assert c - p == pytest.approx(1.0)
