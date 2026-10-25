from trading_agent.autonomy import approval_route
from trading_agent.orders import build_open_order
from trading_agent.paper import PaperBroker
from trading_agent.pipeline import open_condor
from trading_agent.risk import PortfolioState
from trading_agent.structure import Condor

C = Condor(95, 90, 110, 115, 1.00, 0.60)


def test_collateral_matches_sizing(tmp_path):
    b = PaperBroker(root=tmp_path)
    o = build_open_order(C, "X", "2026-12-04", 1, "id1")
    assert b.dry_run(o)["bp_effect"] == C.collateral == 340


def test_fill_updates_cash_and_is_idempotent(tmp_path):
    b = PaperBroker(root=tmp_path)
    o = build_open_order(C, "X", "2026-12-04", 1, "id1")
    assert b.submit(o)["status"] == "Filled"
    assert b.submit(o)["status"] == "Filled"
    assert b.acct["cash"] == 5160 and b.acct["reserved"] == 340 and b.available_cash == 4820


def test_state_persists(tmp_path):
    b = PaperBroker(root=tmp_path)
    b.submit(build_open_order(C, "X", "2026-12-04", 1, "id1"))
    assert PaperBroker(root=tmp_path).available_cash == 4820


def test_insufficient_funds_rejected(tmp_path):
    b = PaperBroker(start_cash=100, root=tmp_path)
    o = build_open_order(C, "X", "2026-12-04", 1, "id1")
    assert b.dry_run(o)["warnings"] and b.submit(o)["status"] == "Rejected"


def test_paper_auto_trades_end_to_end(cfg, tmp_path):
    b = PaperBroker(root=tmp_path)
    st = PortfolioState(cash=b.available_cash)
    o = open_condor(C, "X", "Tech", "2026-12-04", b, st, cfg, "paper", None, (-0.15, 0.15), tmp_path)
    assert o.status == "submitted" and b.available_cash == 4820


def test_cash_buffer_stops_paper_trade(cfg, tmp_path):
    b = PaperBroker(start_cash=1200, root=tmp_path)
    o = open_condor(C, "X", "Tech", "2026-12-04", b, PortfolioState(cash=b.available_cash), cfg,
                    "paper", None, (-0.15, 0.15), tmp_path)
    assert o.status == "risk_rejected" and not b.acct["orders"]


def test_production_still_gated_with_auto_mode(cfg):
    assert approval_route("paper", cfg) == "auto" and approval_route("production", cfg) == "human"
