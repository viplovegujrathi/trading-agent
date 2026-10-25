"""One condor from candidate to submitted order:
risk -> dry-run -> risk -> approval route -> submit. Every step is journaled.
In human mode the function stops at 'awaiting_approval'; the approval bot resumes it."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import journal, orders, risk
from .autonomy import LiveRecord, approval_route
from .broker import Broker, SubmitUnknown, submit_safely
from .halt import is_halted, set_halt
from .structure import Condor


@dataclass(frozen=True)
class Outcome:
    status: str          # risk_rejected | awaiting_approval | submitted | unknown
    detail: tuple[str, ...] = ()
    external_identifier: str | None = None


def open_condor(c: Condor, ticker: str, sector: str, expiry: str, broker: Broker,
                st: risk.PortfolioState, cfg: dict, env: str, rec: LiveRecord | None = None,
                deltas: tuple[float, float | None] = (0.0, None), root: Path = journal.ROOT) -> Outcome:
    st = risk.PortfolioState(**{**st.__dict__, "halted": st.halted or is_halted(root)})
    contracts = cfg["portfolio"]["contracts_per_condor"]
    ext = orders.external_identifier(ticker, expiry)
    log = lambda ev, **kw: journal.append_event(ev, ticker, ext, env=env, root=root, **kw)

    pre = risk.check_pre(c, ticker, sector, deltas[0], deltas[1], contracts, st, cfg)
    if not pre.ok:
        log("risk_rejected", stage="pre", violations=list(pre.violations))
        return Outcome("risk_rejected", pre.violations, ext)

    order = orders.build_open_order(c, ticker, expiry, contracts, ext)
    log("proposal", order=order)                       # journaled BEFORE any submit
    dr = broker.dry_run(order)
    post = risk.check_post_dry_run(c, dr["bp_effect"], st, cfg, tuple(dr.get("warnings", ())))
    log("dry_run", bp_effect=dr["bp_effect"], ok=post.ok)
    if not post.ok:
        log("risk_rejected", stage="post", violations=list(post.violations))
        return Outcome("risk_rejected", post.violations, ext)

    route = approval_route(env, cfg, rec)
    log("approval_route", route=route)
    if route == "human":
        return Outcome("awaiting_approval", (), ext)
    return submit_approved(order, broker, env, root, ticker)


def submit_approved(order: dict, broker: Broker, env: str, root: Path, ticker: str) -> Outcome:
    ext = order["external-identifier"]
    try:
        res = submit_safely(broker, order)
    except SubmitUnknown:
        journal.append_event("order_unknown", ticker, ext, env=env, root=root)
        set_halt(f"unknown order status {ext}", root)     # auto-trip per risk.md
        return Outcome("unknown", ("status unknown; halted, not resent",), ext)
    journal.append_event("submitted", ticker, ext, env=env, root=root, status=res.get("status"))
    return Outcome("submitted", (), ext)
