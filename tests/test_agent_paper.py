"""End-to-end in paper mode: screen -> propose -> dry-run -> approve -> submit -> monitor -> close."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import IN_WINDOW, TODAY

from trading_agent.agent import Agent, AgentError
from trading_agent.approval import CliChannel
from trading_agent.broker import SubmitAmbiguous
from trading_agent.config import Settings, load_strategy
from trading_agent.journal import Journal, StateStore
from trading_agent.paper import PaperBroker, PaperMarket
from trading_agent.screen import load_universe

DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture
def agent(tmp_path) -> Agent:
    settings = Settings(env="paper", halt_env=False, approval_channel="cli", state_dir=tmp_path / "state",
                        journal_dir=tmp_path / "journal", data_dir=DATA, aws_region=None)
    cfg = load_strategy(env="paper")
    market = PaperMarket(DATA / "paper" / "market.json", today=TODAY)
    broker = PaperBroker(market, settings.state_dir / "paper_broker.json")
    msgs: list[str] = []
    a = Agent(settings, cfg, broker, market, load_universe(DATA / "paper" / "universe.csv"),
              Journal(settings.journal_dir, "paper"), StateStore(settings.state_dir), CliChannel(msgs.append),
              clock=lambda: IN_WINDOW, sleep=lambda s: None, out=msgs.append)
    a.msgs = msgs  # type: ignore[attr-defined]
    return a


def open_alfa(agent: Agent) -> str:
    r = agent.propose("ALFA")
    assert r.ok, r.reasons
    ext = r.ticket["external_identifier"]
    st = agent.submit(ext, f"YES {ext}")
    assert st.status == "Filled"
    return ext


def test_screen_table(agent):
    res = {r.ticker: r for r in agent.screen()}
    assert res["ALFA"].passed and res["GOLF"].passed and res["BRVO"].passed
    assert not res["CHRL"].passed and not res["DLTA"].passed and not res["ECHO"].passed
    assert not res["FXTR"].passed  # ex-dividend unknown
    assert res["ALFA"].snapshot.price_source == "modeled"


def test_propose_runs_all_gates_and_labels_prices(agent):
    r = agent.propose("ALFA")
    assert r.ok, r.reasons
    assert r.dry_run.buying_power_effect_usd == pytest.approx(r.proposal.collateral_usd)
    assert r.proposal.price_source == "modeled"
    events = [e["event"] for e in agent.journal.events()]
    assert events.index("proposal") < events.index("dry_run") < events.index("ticket_created")
    assert "Reply YES" in agent.msgs[-1]


def test_submit_requires_exact_yes_for_that_ticket(agent):
    r = agent.propose("ALFA")
    ext = r.ticket["external_identifier"]
    for bad in ["yes", "YES", f"YES {ext}x", "ok go", "YES ic-GOLF-20261127-00000000"]:
        with pytest.raises(AgentError):
            agent.submit(ext, bad)
    assert agent.store.tickets()[ext]["status"] == "AWAITING_APPROVAL"


def test_full_round_trip(agent):
    ext = open_alfa(agent)
    pos = agent.store.open_positions()[ext]
    assert pos["status"] == "open" and len(pos["legs"]) == 4
    assert not agent.propose("ALFA").ok  # one per ticker

    # Time passes to 21 DTE: monitor proposes a close; the kill switch doesn't block closes.
    agent.halt("test")
    later = datetime(2026, 11, 6, 16, 0, tzinfo=timezone.utc)
    agent.clock = lambda: later
    agent.market._today = later.date()
    tickets = agent.monitor()
    assert len(tickets) == 1 and tickets[0]["kind"] == "close"
    assert "DTE" in tickets[0]["summary"]["reason"]
    assert agent.monitor() == []  # no duplicate close ticket
    cext = tickets[0]["external_identifier"]
    st = agent.submit(cext, f"YES {cext}")
    assert st.status == "Filled"
    closed = agent.store.positions()[ext]
    assert closed["status"] == "closed" and "realized_pnl_usd" in closed
    assert not agent.store.open_positions()


def test_halt_blocks_new_opens(agent):
    agent.halt("test")
    r = agent.propose("ALFA")
    assert not r.ok and "kill switch" in r.reasons[0]


def test_submit_outside_window_rejected(agent):
    r = agent.propose("ALFA")
    ext = r.ticket["external_identifier"]
    agent.clock = lambda: datetime(2026, 10, 26, 13, 35, tzinfo=timezone.utc)  # 9:35 ET
    with pytest.raises(AgentError, match="trading window"):
        agent.submit(ext, f"YES {ext}")


def test_unknown_order_trips_halt_and_is_not_resent(agent):
    r = agent.propose("ALFA")
    ext = r.ticket["external_identifier"]
    calls = []

    def boom(order, *, approval):
        calls.append(order)
        raise SubmitAmbiguous("timeout")

    agent.broker.submit = boom  # type: ignore[method-assign]
    st = agent.submit(ext, f"YES {ext}")
    assert st.status == "UNKNOWN" and len(calls) == 1
    assert agent.halted()
    assert agent.store.tickets()[ext]["status"] == "UNKNOWN"
    agent.resolve(ext, None)
    assert agent.store.tickets()[ext]["status"] == "NOT_PLACED"


def test_max_orders_per_day(agent):
    for _ in range(6):
        agent.journal.write("order_submitted", now=IN_WINDOW)
    r = agent.propose("ALFA")
    ext = r.ticket["external_identifier"]
    with pytest.raises(AgentError, match="orders today"):
        agent.submit(ext, f"YES {ext}")


def test_working_order_reprices_once_then_cancels(agent):
    r = agent.propose("GOLF")
    ext = r.ticket["external_identifier"]
    # Make the market worse than the limit so it rests.
    agent.market.data["names"]["GOLF"]["iv"] = 0.30
    st = agent.submit(ext, f"YES {ext}", wait=False)
    assert st.status == "Live"
    t0 = IN_WINDOW
    from datetime import timedelta
    agent.clock = lambda: t0 + timedelta(minutes=11)
    agent.manage_working(ext)
    t = agent.store.tickets()[ext]
    if t["status"] == "SUBMITTED":  # repriced (GOLF limit 1.01 can't step below $1.00: goes straight to cancel)
        agent.clock = lambda: t0 + timedelta(minutes=22)
        agent.manage_working(ext)
    assert agent.store.tickets()[ext]["status"] == "CANCELLED"
    assert not agent.store.open_positions()
