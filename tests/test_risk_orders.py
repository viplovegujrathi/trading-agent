from trading_agent.orders import build_open_order, external_identifier, reprice
from trading_agent.risk import PortfolioState, check_post_dry_run, check_pre, in_blackout
from trading_agent.structure import Condor

C = Condor(95, 90, 110, 115, 1.00, 0.60)


def pre(cfg, c=C, st=None, **kw):
    a = dict(ticker="X", sector="Tech", short_put_delta=-0.15, short_call_delta=0.15, contracts=1)
    a.update(kw)
    return check_pre(c, a.pop("ticker"), a.pop("sector"), a.pop("short_put_delta"),
                     a.pop("short_call_delta"), a.pop("contracts"), st or PortfolioState(cash=5000), cfg)


def test_ok(cfg):
    assert pre(cfg).ok


def test_halt_blocks(cfg):
    assert not pre(cfg, st=PortfolioState(halted=True, cash=5000)).ok


def test_limits(cfg):
    assert not pre(cfg, st=PortfolioState(open_tickers=("A", "B", "C", "D"), cash=5000)).ok
    assert not pre(cfg, st=PortfolioState(open_tickers=("X",), cash=5000)).ok
    assert not pre(cfg, st=PortfolioState(open_sectors=("Tech", "Tech"), cash=5000)).ok
    assert not pre(cfg, st=PortfolioState(orders_today=6, cash=5000)).ok
    assert not pre(cfg, st=PortfolioState(realized_pnl_today=-500, cash=5000)).ok
    assert not pre(cfg, contracts=2).ok
    assert not pre(cfg, short_put_delta=-0.25).ok


def test_call_wing_wider_rejected(cfg):
    assert not pre(cfg, c=Condor(95, 90, 110, 117.5, 1.0, 0.6)).ok


def test_low_credit_rejected(cfg):
    assert not pre(cfg, c=Condor(95, 90, 110, 115, 0.5, 0.2)).ok


def test_dry_run_bp_higher_than_computed_rejected(cfg):
    st = PortfolioState(cash=5000)
    assert check_post_dry_run(C, 340, st, cfg).ok
    assert not check_post_dry_run(C, 400, st, cfg).ok


def test_cash_buffer_and_warnings(cfg):
    assert not check_post_dry_run(C, 340, PortfolioState(cash=1200), cfg).ok
    assert not check_post_dry_run(C, 340, PortfolioState(cash=5000), cfg, ("x",)).ok


def test_blackout(cfg):
    assert in_blackout(5, 300, cfg) and in_blackout(100, 10, cfg) and not in_blackout(60, 300, cfg)


def test_order_is_single_four_leg_limit_credit():
    o = build_open_order(C, "AAPL", "2026-12-04", 1, "ic-AAPL-2026-12-04-abcd1234")
    assert o["order-type"] == "Limit" and o["price-effect"] == "Credit" and o["price"] == "1.60"
    assert len(o["legs"]) == 4
    assert o["legs"][0]["symbol"] == "AAPL  261204P00095000"
    assert [l["action"] for l in o["legs"]].count("Sell to Open") == 2


def test_fallback_order_has_two_legs():
    c = Condor(95, 90, None, None, 1.1, 0.0, fallback=True)
    assert len(build_open_order(c, "X", "2026-12-04", 1, "id")["legs"]) == 2


def test_external_id_format():
    assert external_identifier("AAPL", "2026-12-04", "abcd1234") == "ic-AAPL-2026-12-04-abcd1234"


def test_reprice_respects_min_credit(cfg):
    assert reprice(Condor(95, 90, 110, 115, 1.0, 0.6), cfg) == 1.55
    assert reprice(Condor(95, 90, None, None, 1.0, 0.0, True), cfg) is None
