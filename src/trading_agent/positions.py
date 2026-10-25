"""Open-condor bookkeeping on top of state/positions.json, plus realized P&L."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from . import journal
from .monitor import OpenCondor
from .risk import PortfolioState
from .structure import Condor


def to_record(c: Condor, ticker: str, sector: str, expiry: str, contracts: int, ext: str,
              opened: str | None = None) -> dict:
    return {"opened": opened or date.today().isoformat(), "ticker": ticker, "sector": sector, "expiry": expiry, "contracts": contracts,
            "short_put": c.short_put, "long_put": c.long_put,
            "short_call": c.short_call, "long_call": c.long_call,
            "credit": c.total_credit, "collateral": c.collateral, "ext": ext, "touched": False}


def add_position(rec: dict, root: Path = journal.ROOT) -> None:
    ps = journal.load_positions(root)
    if any(p["ext"] == rec["ext"] for p in ps):        # idempotent on the order id
        return
    journal.save_positions(ps + [rec], root)


def remove_position(ext: str, root: Path = journal.ROOT) -> dict | None:
    ps = journal.load_positions(root)
    gone = next((p for p in ps if p["ext"] == ext), None)
    journal.save_positions([p for p in ps if p["ext"] != ext], root)
    return gone


def mark_touched(ext: str, root: Path = journal.ROOT) -> None:
    journal.save_positions([{**p, "touched": True} if p["ext"] == ext else p
                            for p in journal.load_positions(root)], root)


def to_open_condor(p: dict) -> OpenCondor:
    return OpenCondor(p["ticker"], date.fromisoformat(p["expiry"]), p["short_put"],
                      p["short_call"], p["credit"], p.get("touched", False))


def realized_pnl(credit: float, debit: float, contracts: int) -> float:
    return round((credit - debit) * 100 * contracts, 2)


def realized_today(day: str, root: Path = journal.ROOT) -> float:
    return round(sum(e.get("pnl", 0.0) for e in journal.read_events(day, root)
                     if e["event"] == "close_filled"), 2)


def orders_today(day: str, root: Path = journal.ROOT) -> int:
    return sum(e["event"] == "submitted" for e in journal.read_events(day, root))


def portfolio_state(cash: float, day: str, halted: bool = False,
                    root: Path = journal.ROOT) -> PortfolioState:
    ps = journal.load_positions(root) + [w["record"] for w in load_working(root).values()]
    held = sum(w["record"]["collateral"] for w in load_working(root).values())   # working orders count
    return PortfolioState(open_tickers=tuple(p["ticker"] for p in ps),
                          open_sectors=tuple(p["sector"] for p in ps),
                          orders_today=orders_today(day, root), cash=cash - held,
                          realized_pnl_today=realized_today(day, root), halted=halted)


# ---- working (unfilled) orders: state/working.json -----------------------------------
def load_working(root: Path = journal.ROOT) -> dict:
    p = root / "state" / "working.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_working(w: dict, root: Path = journal.ROOT) -> None:
    (root / "state").mkdir(parents=True, exist_ok=True)
    (root / "state" / "working.json").write_text(json.dumps(w, indent=2, sort_keys=True))
