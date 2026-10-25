"""Open and monitor cycles: the deterministic loop the timers run."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import journal, orders, positions, risk
from .autonomy import LiveRecord, approval_route
from .broker import Broker, SubmitUnknown, submit_safely
from .halt import is_halted, set_halt
from .monitor import evaluate, update_touched
from .pipeline import Outcome, open_condor
from .screen import Snapshot, screen_name
from .structure import Quote, build_condor


@dataclass(frozen=True)
class Candidate:
    snap: Snapshot
    sector: str
    expiry: date
    chain: list[Quote]


def open_cycle(cands: list[Candidate], broker: Broker, cash: float, cfg: dict, env: str,
               today: date, minutes_since_open: int, minutes_to_close: int,
               rec: LiveRecord | None = None, root: Path = journal.ROOT,
               now: datetime | None = None) -> list[tuple[str, Outcome]]:
    """Screen, structure, then open the best-credit-to-collateral condors until a limit stops it."""
    now = now or datetime.now(timezone.utc)
    results: list[tuple[str, Outcome]] = []
    if risk.in_blackout(minutes_since_open, minutes_to_close, cfg):
        journal.append_event("skipped", None, None, env=env, root=root, reason="trading window")
        return results
    built = []
    for c in cands:
        r = screen_name(c.snap, c.expiry, cfg)
        if not r.passed:
            journal.append_event("screen_fail", c.snap.ticker, None, env=env, root=root,
                                 reasons=list(r.reasons))
            continue
        condor = build_condor(c.snap.price, c.snap.dma50, c.chain, cfg)
        if condor is None:
            journal.append_event("no_structure", c.snap.ticker, None, env=env, root=root)
            continue
        built.append((condor.total_credit / max(condor.collateral, 1), c, condor))
    for _, c, condor in sorted(built, key=lambda x: -x[0]):
        day = today.isoformat()
        st = positions.portfolio_state(cash, day, is_halted(root), root)    # fresh every order
        by = {(q.kind, q.strike): q for q in c.chain}
        deltas = (by[("P", condor.short_put)].delta,
                  by[("C", condor.short_call)].delta if condor.short_call else None)
        o = open_condor(condor, c.snap.ticker, c.sector, c.expiry.isoformat(), broker, st, cfg,
                        env, rec, deltas, root)
        results.append((c.snap.ticker, o))
        if o.status == "submitted":
            rec_ = positions.to_record(condor, c.snap.ticker, c.sector, c.expiry.isoformat(),
                                       cfg["portfolio"]["contracts_per_condor"],
                                       o.external_identifier, today.isoformat())
            filled = broker.find_order(o.external_identifier)
            if filled and filled.get("status") == "Filled":
                positions.add_position(rec_, root)
                journal.append_event("fill", c.snap.ticker, o.external_identifier, env=env, root=root)
                cash = cash + condor.max_profit - condor.collateral   # cash net of reserved
            elif filled and filled.get("status") == "Working":
                w = positions.load_working(root)
                w[o.external_identifier] = {"record": rec_, "submitted_at": now.isoformat(),
                                            "repriced": False}
                positions.save_working(w, root)
        if o.status == "unknown":
            break
    return results


def close_position(p: dict, debit: float, broker: Broker, cfg: dict, env: str, today: date,
                   rec: LiveRecord | None = None, root: Path = journal.ROOT) -> Outcome:
    """Closes are allowed under HALT, in any window, and are not blocked by the daily order cap."""
    ext = orders.external_identifier(f"{p['ticker']}-close", p["expiry"])
    order = orders.build_close_order(p, debit, ext)
    log = lambda ev, **kw: journal.append_event(ev, p["ticker"], ext, env=env, root=root, **kw)
    log("close_proposal", order=order)
    if approval_route(env, cfg, rec) == "human":
        return Outcome("awaiting_approval", (), ext)
    try:
        res = submit_safely(broker, order)
    except SubmitUnknown:
        log("order_unknown")
        set_halt(f"unknown close status {ext}", root)
        return Outcome("unknown", ("close status unknown; not resent",), ext)
    log("submitted", status=res.get("status"))
    if res.get("status") == "Filled":
        pnl = positions.realized_pnl(p["credit"], debit, p["contracts"])
        positions.remove_position(p["ext"], root)
        log("close_filled", pnl=pnl)
        if pnl <= -cfg["portfolio"]["daily_loss_halt_usd"] or \
                positions.realized_today(today.isoformat(), root) <= -cfg["portfolio"]["daily_loss_halt_usd"]:
            set_halt("daily realized loss limit", root)
        return Outcome("submitted", (), ext)
    return Outcome("submitted", ("not filled",), ext)


def monitor_cycle(marks: dict[str, dict], broker: Broker, cfg: dict, env: str, today: date,
                  holidays: set[date], rec: LiveRecord | None = None,
                  root: Path = journal.ROOT) -> list[tuple[str, str, tuple[str, ...]]]:
    """marks[ext] = {cost_to_close, low, high, ex_div_before_expiry, call_near_money}."""
    out = []
    for p in journal.load_positions(root):
        m = marks.get(p["ext"])
        if m is None:
            journal.append_event("no_mark", p["ticker"], p["ext"], env=env, root=root)
            continue
        oc = positions.to_open_condor(p)
        if update_touched(oc, m["low"], m["high"]) and not p.get("touched"):
            positions.mark_touched(p["ext"], root)
            oc = positions.to_open_condor({**p, "touched": True})
        sig = evaluate(oc, today, m["cost_to_close"], m.get("ex_div_before_expiry", False),
                       m.get("call_near_money", False), holidays, cfg)
        out.append((p["ticker"], sig.action, sig.reasons))
        # close_call_side (ex-dividend risk) is handled as a full close until a call-spread-only
        # closing order exists: stricter than strategy.md, but never ignores the signal.
        if sig.action in ("close", "close_call_side"):
            journal.append_event("exit_signal", p["ticker"], p["ext"], env=env, root=root,
                                 action=sig.action, reasons=list(sig.reasons))
            close_position({**p, "touched": oc.touched}, m["cost_to_close"], broker, cfg, env, today, rec, root)
    return out


def settle_working(broker: Broker, cfg: dict, env: str, now: datetime,
                   root: Path = journal.ROOT) -> list[tuple[str, str]]:
    """strategy.md section 5: not filled in 10 min -> one step to mid - 0.05; still not filled
    another 10 min later -> cancel. Never reprices below the minimum credit."""
    wait = timedelta(minutes=cfg["pricing"]["reprice_after_minutes"])
    work, out = positions.load_working(root), []
    for ext, w in list(work.items()):
        r = w["record"]
        log = lambda ev, **kw: journal.append_event(ev, r["ticker"], ext, env=env, root=root, now=now, **kw)
        o = broker.find_order(ext)
        status = o.get("status") if o else None
        if status == "Filled":
            r = {**r, "credit": float(o["fill_price"]), "collateral": o.get("collateral", r["collateral"])}
            positions.add_position(r, root)
            log("fill", price=o["fill_price"])
            del work[ext]
            out.append((ext, "filled"))
        elif status in ("Cancelled", "Rejected", None):
            log("working_dropped", status=status)
            del work[ext]
            out.append((ext, "dropped"))
        else:
            age = now - datetime.fromisoformat(w["submitted_at"])
            if not w["repriced"] and age >= wait:
                new = orders.reprice_credit(r["credit"], cfg)
                if new is None:
                    broker.cancel(ext)
                    log("order_cancelled", reason="reprice would breach minimum credit")
                    del work[ext]
                    out.append((ext, "cancelled"))
                elif broker.replace_price(ext, new):
                    w["repriced"], w["submitted_at"] = True, now.isoformat()
                    work[ext] = {**w, "record": {**r, "credit": new}}
                    log("order_repriced", price=new)
                    out.append((ext, "repriced"))
            elif w["repriced"] and age >= wait:
                broker.cancel(ext)
                log("order_cancelled", reason="not filled after reprice")
                del work[ext]
                out.append((ext, "cancelled"))
    positions.save_working(work, root)
    return out
