import copy
from trading_agent import journal
from trading_agent.autonomy import LiveRecord, approval_route, live_record_from_journal
from trading_agent.broker import FakeBroker
from trading_agent.pipeline import open_condor
from trading_agent.risk import PortfolioState
from trading_agent.structure import Condor

C = Condor(95, 90, 110, 115, 1.00, 0.60)


def human(cfg):
    c = copy.deepcopy(cfg)
    c["approval"]["mode"] = "human"
    return c


def auto(cfg):
    c = copy.deepcopy(cfg)
    c["approval"]["mode"] = "auto"
    return c


def run(cfg, env="sandbox", broker=None, tmp=None, rec=None):
    return open_condor(C, "X", "Tech", "2026-12-04", broker or FakeBroker(bp_effect=340),
                       PortfolioState(cash=5000), cfg, env, rec, (-0.15, 0.15), tmp)


def test_default_is_human(cfg):
    assert approval_route("sandbox", human(cfg)) == "human"


def test_sandbox_auto(cfg):
    assert approval_route("sandbox", auto(cfg)) == "auto"


def test_production_auto_fails_closed(cfg, monkeypatch):
    a = auto(cfg)
    assert approval_route("production", a) == "human"
    assert approval_route("production", a, LiveRecord(29, 0, 0)) == "human"
    assert approval_route("production", a, LiveRecord(30, 1, 0)) == "human"
    assert approval_route("production", a, LiveRecord(30, 0, 1)) == "human"
    assert approval_route("production", a, LiveRecord(30, 0, 0)) == "auto"
    assert approval_route("weird", a, LiveRecord(99, 0, 0)) == "human"


def test_pipeline_auto_submits_in_sandbox(cfg, tmp_path):
    b = FakeBroker(bp_effect=340)
    o = run(auto(cfg), broker=b, tmp=tmp_path)
    assert o.status == "submitted" and b.submit_calls == 1
    ev = [e["event"] for e in journal.read_events(__import__("datetime").date.today().isoformat(), tmp_path)]
    assert ev == ["proposal", "dry_run", "approval_route", "submitted"]


def test_pipeline_human_stops_before_submit(cfg, tmp_path):
    b = FakeBroker(bp_effect=340)
    assert run(human(cfg), broker=b, tmp=tmp_path).status == "awaiting_approval" and b.submit_calls == 0


def test_pipeline_production_unproven_never_auto(cfg, tmp_path):
    b = FakeBroker(bp_effect=340)
    assert run(auto(cfg), "production", b, tmp_path).status == "awaiting_approval" and b.submit_calls == 0


def test_dry_run_over_collateral_blocks_submit(cfg, tmp_path):
    b = FakeBroker(bp_effect=900)
    o = run(auto(cfg), broker=b, tmp=tmp_path)
    assert o.status == "risk_rejected" and b.submit_calls == 0


def test_halt_blocks_auto_open(cfg, tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "HALT").write_text("x")
    b = FakeBroker(bp_effect=340)
    assert run(auto(cfg), broker=b, tmp=tmp_path).status == "risk_rejected" and b.submit_calls == 0


def test_unknown_order_halts_and_does_not_resend(cfg, tmp_path):
    class Lost(FakeBroker):
        def submit(self, order):
            self.submit_calls += 1
            raise TimeoutError
    b = Lost(bp_effect=340)
    o = run(auto(cfg), broker=b, tmp=tmp_path)
    assert o.status == "unknown" and b.submit_calls == 1
    assert (tmp_path / "state" / "HALT").exists()


def test_live_record_from_journal():
    ev = [{"env": "production", "event": "close_filled"}] * 3 + [
        {"env": "sandbox", "event": "close_filled"}, {"env": "production", "event": "order_unknown"}]
    assert live_record_from_journal(ev) == LiveRecord(3, 0, 1)
