from datetime import date, datetime, timezone

from conftest import IN_WINDOW, TODAY, proposal

from trading_agent.models import DryRunResult, Leg
from trading_agent.orders import occ_symbol
from trading_agent.risk import (
    OpenCondor, PortfolioState, check_close, check_post_dry_run, check_pre_dry_run, check_pre_submit,
    clear_halt, daily_loss_breached, defined_risk_violations, is_halted, trip_halt,
)

EMPTY = PortfolioState([], 0, 0.0, 5000.0)


def dr(bp=340.0, cash_after=4660.0, warnings=(), errors=(), ok=True):
    return DryRunResult(ok, bp, 4.0, list(warnings), list(errors), cash_after)


def test_clean_proposal_passes(cfg):
    d = check_pre_dry_run(proposal(), EMPTY, cfg, TODAY, halted=False)
    assert d.ok, d.violations
    assert check_post_dry_run(proposal(), dr(), EMPTY, cfg).ok


def test_halt_blocks_opens(cfg):
    assert not check_pre_dry_run(proposal(), EMPTY, cfg, TODAY, halted=True).ok


def test_collateral_and_credit_limits(cfg):
    assert not check_pre_dry_run(proposal(collateral_usd=1000.01), EMPTY, cfg, TODAY, False).ok
    assert not check_pre_dry_run(proposal(max_profit_usd=99.0), EMPTY, cfg, TODAY, False).ok


def test_portfolio_limits(cfg):
    four = PortfolioState([OpenCondor(t, s, 300) for t, s in
                           [("A", "x"), ("B", "y"), ("C", "z"), ("D", "w")]], 0, 0, 5000)
    assert not check_pre_dry_run(proposal(), four, cfg, TODAY, False).ok
    same_ticker = PortfolioState([OpenCondor("XYZ", "Energy", 300)], 0, 0, 5000)
    assert not check_pre_dry_run(proposal(), same_ticker, cfg, TODAY, False).ok
    two_sector = PortfolioState([OpenCondor("A", "Health Care", 300), OpenCondor("B", "Health Care", 300)], 0, 0, 5000)
    d = check_pre_dry_run(proposal(), two_sector, cfg, TODAY, False)
    assert not d.ok and any("Health Care" in v for v in d.violations)


def test_delta_cap(cfg):
    assert not check_pre_dry_run(proposal(short_put_delta=0.21), EMPTY, cfg, TODAY, False).ok
    assert not check_pre_dry_run(proposal(short_call_delta=0.25), EMPTY, cfg, TODAY, False).ok


def test_call_wing_wider_than_put_wing_rejected(cfg):
    d = check_pre_dry_run(proposal(call_wing=7.5), EMPTY, cfg, TODAY, False)
    assert not d.ok and any("wider" in v for v in d.violations)


def test_contracts_per_condor(cfg):
    assert not check_pre_dry_run(proposal(quantity=2), EMPTY, cfg, TODAY, False).ok


def test_earnings_and_ex_div_before_expiry_rejected(cfg):
    assert not check_pre_dry_run(proposal(earnings_date=date(2026, 11, 10)), EMPTY, cfg, TODAY, False).ok
    assert not check_pre_dry_run(proposal(ex_dividend_date=date(2026, 11, 10)), EMPTY, cfg, TODAY, False).ok


def test_naked_and_strangle_rejected():
    e = date(2026, 11, 27)
    naked = [Leg(occ_symbol("XYZ", e, "P", 90), "P", 90, "Sell to Open", 1),
             Leg(occ_symbol("XYZ", e, "C", 110), "C", 110, "Sell to Open", 1)]
    assert defined_risk_violations(naked, opening=True)
    inverted = [Leg(occ_symbol("XYZ", e, "P", 95), "P", 95, "Buy to Open", 1),
                Leg(occ_symbol("XYZ", e, "P", 90), "P", 90, "Sell to Open", 1)]
    assert defined_risk_violations(inverted, opening=True)


def test_dry_run_bp_higher_than_computed_collateral_rejected(cfg):
    d = check_post_dry_run(proposal(), dr(bp=420.0), EMPTY, cfg)
    assert not d.ok and any("computed collateral" in v for v in d.violations)


def test_dry_run_bp_over_cap_rejected(cfg):
    assert not check_post_dry_run(proposal(collateral_usd=1200), dr(bp=1100), EMPTY, cfg).ok


def test_dry_run_warnings_and_errors_drop_ticket(cfg):
    assert not check_post_dry_run(proposal(), dr(warnings=["x: y"]), EMPTY, cfg).ok
    assert not check_post_dry_run(proposal(), dr(ok=False, errors=["422"]), EMPTY, cfg).ok
    assert not check_post_dry_run(proposal(), dr(bp=None), EMPTY, cfg).ok


def test_cash_buffer_after_trade(cfg):
    assert not check_post_dry_run(proposal(), dr(cash_after=999.0), EMPTY, cfg).ok


def test_pre_submit_gates(cfg):
    ok = dict(pf=EMPTY, cfg=cfg, now=IN_WINDOW, halted=False, approved=True, expired=False)
    assert check_pre_submit(kind="open", **ok).ok
    assert not check_pre_submit(kind="open", **{**ok, "approved": False}).ok
    assert not check_pre_submit(kind="open", **{**ok, "expired": True}).ok
    assert not check_pre_submit(kind="open", **{**ok, "halted": True}).ok
    assert check_pre_submit(kind="close", **{**ok, "halted": True}).ok  # closes allowed under halt
    six = PortfolioState([], 6, 0, 5000)
    assert not check_pre_submit(kind="close", **{**ok, "pf": six}).ok
    first_minutes = datetime(2026, 10, 26, 13, 35, tzinfo=timezone.utc)  # 9:35 ET
    assert not check_pre_submit(kind="open", **{**ok, "now": first_minutes}).ok
    assert check_pre_submit(kind="close", **{**ok, "now": first_minutes}).ok


def test_close_must_match_position(cfg):
    p = proposal()
    from trading_agent.orders import closing_legs
    legs = closing_legs(p.legs)
    assert check_close(legs, p.legs, 0.80).ok
    assert not check_close(legs[:3], p.legs, 0.80).ok
    assert not check_close(legs, p.legs, 0).ok


def test_daily_loss(cfg):
    assert daily_loss_breached(PortfolioState([], 0, -501, None), cfg)
    assert not daily_loss_breached(PortfolioState([], 0, -499, None), cfg)


def test_kill_switch_file(tmp_path):
    assert not is_halted(tmp_path, False)
    assert is_halted(tmp_path, True)
    trip_halt(tmp_path, "test", "2026-10-26T15:00:00+00:00")
    assert is_halted(tmp_path, False)
    assert clear_halt(tmp_path) and not is_halted(tmp_path, False)
