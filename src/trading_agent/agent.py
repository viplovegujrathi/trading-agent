"""Orchestration: wires the pure modules to the broker, journal and approval gate.

The order path is always: risk (pre) -> dry-run -> risk (post) -> approval ->
risk (pre-submit) -> submit. There is no other route to broker.submit.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

from . import ops
from .approval import (
    AWAITING, ApprovalError, Channel, approval_for_approved_ticket, create_ticket, expire_stale,
    is_expired, make_channel, parse_reply, record_decision, render_ticket,
)
from .auth import AuthError
from .broker import Approval, Broker, BrokerError, submit_idempotent
from .config import Settings, StrategyConfig
from .journal import Journal, StateStore
from .marketdata import MarketData
from .models import DryRunResult, Leg, OrderStatus, Proposal
from .monitor import detect_assignment, evaluate_exits
from .orders import (
    build_open_order, build_order, closing_debit_mid, closing_legs, new_external_identifier, reprice_limit,
)
from .risk import (
    OpenCondor, PortfolioState, check_close, check_post_dry_run, check_pre_dry_run, check_pre_submit,
    daily_loss_breached, is_halted, trip_halt,
)
from .screen import ScreenResult, Universe, screen_horizon, screen_name
from .structure import build_proposal
from .tradingcal import NY, utcnow


class AgentError(RuntimeError):
    pass


@dataclass
class ProposeResult:
    ticker: str
    ok: bool
    reasons: list[str] = field(default_factory=list)
    proposal: Proposal | None = None
    dry_run: DryRunResult | None = None
    ticket: dict[str, Any] | None = None


@dataclass
class Agent:
    settings: Settings
    cfg: StrategyConfig
    broker: Broker
    market: MarketData
    universe: Universe | None
    journal: Journal
    store: StateStore
    channel: Channel
    clock: Callable[[], datetime] = utcnow
    sleep: Callable[[float], None] = time.sleep
    out: Callable[[str], None] = print

    def __post_init__(self) -> None:
        # Journal timestamps follow the agent's clock (UTC), so "today" is consistent.
        self.journal.clock = lambda: self.clock()

    # ------------------------------------------------------------ helpers

    @property
    def env(self) -> str:
        return self.settings.env

    def now(self) -> datetime:
        return self.clock()

    def today(self) -> date:
        return self.now().astimezone(NY).date()

    def halted(self) -> bool:
        return is_halted(self.settings.state_dir, self.settings.halt_env)

    def halt(self, reason: str) -> None:
        trip_halt(self.settings.state_dir, reason, self.now().isoformat(timespec="seconds"))
        self.journal.write("halt_tripped", reason=reason)
        ops.put_metric("HaltTripped", region=self.settings.aws_region)
        self.out(f"KILL SWITCH ON: {reason}")

    def _events_today(self, *names: str) -> list[dict[str, Any]]:
        today = self.today()
        out = []
        for day in {self.now().date(), self.now().date() - timedelta(days=1), self.now().date() + timedelta(days=1)}:
            for e in self.journal.events(day.isoformat()):
                ts = datetime.fromisoformat(e["ts"]).astimezone(NY).date()
                if ts == today and e["event"] in names:
                    out.append(e)
        return out

    def portfolio(self) -> PortfolioState:
        opens = [OpenCondor(p["ticker"], p.get("sector"), p.get("collateral_usd", 0.0))
                 for p in self.store.open_positions().values()]
        submitted = len(self._events_today("order_submitted"))
        realized = sum(e.get("realized_pnl_usd", 0.0) for e in self._events_today("position_closed"))
        try:
            bp = self.broker.balances().get("derivative_buying_power")
        except (BrokerError, AuthError):
            bp = None
        return PortfolioState(opens, submitted, realized, bp)

    # ------------------------------------------------------------ screen

    def screen(self, tickers: list[str] | None = None) -> list[ScreenResult]:
        if self.universe is None:
            raise AgentError("no universe file loaded")
        today = self.today()
        if self.universe.is_stale(today, self.cfg.screen.universe_max_age_days):
            msg = f"universe list dated {self.universe.as_of} is older than {self.cfg.screen.universe_max_age_days} days"
            self.out(f"WARNING: {msg}")
            self.journal.write("universe_stale", as_of=self.universe.as_of.isoformat())
        horizon = screen_horizon(today, self.cfg.structure)
        results = []
        for t in tickers or self.universe.tickers:
            sector = self.universe.sectors.get(t)
            try:
                snap = self.market.snapshot(t, sector, today)
                r = screen_name(snap, self.cfg.screen, horizon, today)
            except (BrokerError, KeyError, ValueError) as e:
                r = ScreenResult(ticker=t, passed=False, reasons=[f"data error: {e}"])
            self.journal.write("screen", ticker=t, passed=r.passed, reasons=r.reasons, flags=r.flags,
                               pct_off_high=r.pct_off_high, pct_below_sma50=r.pct_below_sma50,
                               price_source=r.snapshot.price_source if r.snapshot else None)
            results.append(r)
        return results

    # ------------------------------------------------------------ open

    def propose(self, ticker: str, send: bool = True) -> ProposeResult:
        ticker = ticker.upper()
        res = ProposeResult(ticker=ticker, ok=False)
        if self.halted():
            res.reasons.append("kill switch on: no new opens")
            return res
        if self.universe is None or ticker not in self.universe.sectors:
            res.reasons.append(f"{ticker} is not in the universe")
            return res
        today = self.today()
        sector = self.universe.sectors[ticker]
        snap = self.market.snapshot(ticker, sector, today)
        sr = screen_name(snap, self.cfg.screen, screen_horizon(today, self.cfg.structure), today)
        if not sr.passed:
            res.reasons = ["screen: " + r for r in sr.reasons]
            self.journal.write("proposal_rejected", ticker=ticker, stage="screen", reasons=res.reasons)
            return res
        chain = self.market.chain(ticker, today, self.cfg.structure)
        st = build_proposal(snap, chain, today, self.cfg)
        if st.proposal is None:
            res.reasons = ["structure: " + r for r in st.reasons]
            self.journal.write("proposal_rejected", ticker=ticker, stage="structure", reasons=res.reasons)
            return res
        p = res.proposal = st.proposal
        p.flags.extend(sr.flags)

        pf = self.portfolio()
        pre = check_pre_dry_run(p, pf, self.cfg, today, self.halted())
        if not pre.ok:
            res.reasons = ["risk: " + v for v in pre.violations]
            self.journal.write("risk_rejected", ticker=ticker, stage=pre.stage, violations=pre.violations)
            return res

        ext = new_external_identifier(ticker, p.expiry, "open")
        order = build_open_order(p, ext)
        self.journal.write("proposal", ticker=ticker, external_identifier=ext, proposal=p.to_dict(), order=order)

        dr = res.dry_run = self.broker.dry_run(order)
        self.journal.write("dry_run", ticker=ticker, external_identifier=ext, ok=dr.ok,
                           buying_power_effect_usd=dr.buying_power_effect_usd, fees_usd=dr.fees_usd,
                           cash_after_usd=dr.cash_after_usd, warnings=dr.warnings, errors=dr.errors)
        post = check_post_dry_run(p, dr, pf, self.cfg)
        if not post.ok:
            res.reasons = ["risk: " + v for v in post.violations]
            self.journal.write("risk_rejected", ticker=ticker, external_identifier=ext, stage=post.stage,
                               violations=post.violations)
            return res

        floor = self.cfg.sizing.min_total_credit_usd / 100 / p.quantity
        fallback = reprice_limit(p.total_credit, self.cfg.pricing.reprice_step, floor)
        summary = {
            "structure": "iron condor" if p.kind == "iron_condor" else "put spread (fallback)",
            "expiry": p.expiry.isoformat(), "dte": p.dte, "underlying_price": p.underlying_price,
            "collateral_usd": p.collateral_usd, "dry_run_bp_usd": dr.buying_power_effect_usd,
            "fees_usd": dr.fees_usd, "max_profit_usd": p.max_profit_usd, "max_loss_usd": p.max_loss_usd,
            "breakeven_low": p.breakeven_low, "breakeven_high": p.breakeven_high,
            "short_put_delta": p.short_put_delta, "short_call_delta": p.short_call_delta,
            "put_credit": p.put_credit, "call_credit": p.call_credit, "total_credit": p.total_credit,
            "fallback_limit": f"{fallback:.2f}" if fallback is not None else "none (would breach min credit)",
            "price_source": p.price_source, "delta_source": p.delta_source, "flags": p.flags,
        }
        res.ticket = create_ticket(
            self.store, self.journal, ext=ext, kind="open", ticker=ticker, order=order, summary=summary,
            timeout_minutes=self.cfg.approval.timeout_minutes, now=self.now(),
            extra={"proposal": p.to_dict(), "fallback_limit": fallback})
        res.ok = True
        if send:
            self.channel.send(render_ticket(res.ticket, self.env))
        return res

    def run_screen_and_propose(self) -> list[ProposeResult]:
        """The 9:45 job: screen, propose every passing name, wait for replies if the channel can."""
        results = []
        for r in self.screen():
            if r.passed and not self.halted():
                try:
                    pr = self.propose(r.ticker)
                except (BrokerError, AuthError, KeyError, ValueError) as e:
                    pr = ProposeResult(r.ticker, False, [f"error: {e}"])
                    self.journal.write("proposal_error", ticker=r.ticker, error=str(e))
                results.append(pr)
                if not pr.ok:
                    self.out(f"{r.ticker}: not proposed: {'; '.join(pr.reasons)}")
        exts = [pr.ticket["external_identifier"] for pr in results if pr.ticket]
        self.collect_replies(exts)
        return results

    def collect_replies(self, exts: list[str]) -> None:
        if not exts:
            return
        deadline = self.now() + timedelta(minutes=self.cfg.approval.timeout_minutes)
        for ext, (decision, by) in self.channel.wait(exts, deadline).items():
            try:
                if decision == "yes":
                    self.submit(ext, f"YES {ext}", by=by)
                else:
                    record_decision(self.store, self.journal, ext, "no", by, self.now())
            except (ApprovalError, AgentError) as e:
                self.out(f"{ext}: {e}")
        expire_stale(self.store, self.journal, self.now())

    # ------------------------------------------------------------ submit

    def submit(self, ext: str, confirm: str, by: str = "cli", wait: bool = True) -> OrderStatus:
        """Submit one approved ticket. `confirm` must be exactly `YES <ext>`."""
        parsed = parse_reply(confirm)
        if parsed is None or parsed != ("yes", ext):
            raise AgentError(f"approval must be exactly: YES {ext}")
        t = self.store.tickets().get(ext)
        if t is None:
            raise AgentError(f"no ticket {ext}")
        if t["status"] != AWAITING:
            raise AgentError(f"ticket {ext} is {t['status']}")
        pf = self.portfolio()
        if t["kind"] == "open":
            # Re-run the open limits: the portfolio may have changed since the ticket was built.
            p = Proposal.from_dict(t["proposal"])
            pre = check_pre_dry_run(p, pf, self.cfg, self.today(), self.halted())
            if not pre.ok:
                raise AgentError("risk: " + "; ".join(pre.violations))
        gate = check_pre_submit(kind=t["kind"], pf=pf, cfg=self.cfg, now=self.now(), halted=self.halted(),
                                approved=True, expired=is_expired(t, self.now()))
        if not gate.ok:
            raise AgentError("risk: " + "; ".join(gate.violations))
        approval = record_decision(self.store, self.journal, ext, "yes", by, self.now())
        assert approval is not None
        return self._send(t, approval, wait)

    def _send(self, t: dict[str, Any], approval: Approval, wait: bool) -> OrderStatus:
        ext = t["external_identifier"]
        self.journal.write("order_submitting", ticker=t["ticker"], external_identifier=ext, order=t["order"])
        try:
            st = submit_idempotent(self.broker, t["order"], approval, sleep=self.sleep)
        except AuthError as e:
            self.halt(f"token failure during submit of {ext}")
            raise AgentError(str(e)) from None
        self.journal.write("order_submitted", ticker=t["ticker"], external_identifier=ext, status=st.status,
                           order_id=st.order_id, fill_price=st.fill_price)
        self.store.update_ticket(ext, status="SUBMITTED", order_id=st.order_id, order_status=st.status,
                                 submitted_at=self.now().isoformat(timespec="seconds"), reprices=0)
        if st.status == "UNKNOWN":
            self.store.update_ticket(ext, status="UNKNOWN")
            ops.put_metric("UnknownOrder", region=self.settings.aws_region)
            self.halt(f"order {ext} status UNKNOWN; resolve with `cli resolve`")
        elif st.status == "Rejected":
            self.store.update_ticket(ext, status="REJECTED")
            self.journal.write("order_rejected", ticker=t["ticker"], external_identifier=ext, detail=st.raw)
        elif st.is_filled:
            self._on_fill(ext, st)
        elif wait and st.is_working:
            return self.follow_order(ext)
        return st

    # ------------------------------------------------------------ working orders

    def follow_order(self, ext: str, poll_s: float = 30.0) -> OrderStatus:
        """Wait for a fill; one reprice after N minutes; cancel if still unfilled (strategy.md §5)."""
        while True:
            st, done = self.manage_working(ext)
            if done:
                return st
            self.sleep(poll_s)

    def manage_working(self, ext: str) -> tuple[OrderStatus, bool]:
        t = self.store.tickets()[ext]
        st = self.broker.order_status(t["order_id"], ext)
        if st.is_filled:
            self._on_fill(ext, st)
            return st, True
        if st.is_terminal:
            self.store.update_ticket(ext, status=st.status.upper(), order_status=st.status)
            self.journal.write("order_terminal", ticker=t["ticker"], external_identifier=ext, status=st.status)
            return st, True
        age = self.now() - datetime.fromisoformat(t.get("repriced_at") or t["submitted_at"])
        if age < timedelta(minutes=self.cfg.pricing.reprice_after_minutes):
            return st, False
        if t.get("reprices", 0) < self.cfg.pricing.max_reprices:
            order = dict(t["order"])
            limit = float(order["price"])
            step = self.cfg.pricing.reprice_step
            if order["price-effect"] == "Credit":
                qty = order["legs"][0]["quantity"]
                new = reprice_limit(limit, step, self.cfg.sizing.min_total_credit_usd / 100 / qty)
            else:
                new = round(limit + step, 2)
            if new is not None:
                order["price"] = f"{new:.2f}"
                st = self.broker.replace(t["order_id"], order, approval=approval_for_approved_ticket(t))
                self.store.update_ticket(ext, order=order, reprices=t.get("reprices", 0) + 1,
                                         repriced_at=self.now().isoformat(timespec="seconds"),
                                         order_id=st.order_id or t["order_id"])
                self.journal.write("order_repriced", ticker=t["ticker"], external_identifier=ext,
                                   old_limit=limit, new_limit=new)
                if st.is_filled:
                    self._on_fill(ext, st)
                    return st, True
                return st, False
        self.broker.cancel(t["order_id"])
        self.store.update_ticket(ext, status="CANCELLED", order_status="Cancelled")
        self.journal.write("order_cancelled", ticker=t["ticker"], external_identifier=ext,
                           reason="not filled after reprice")
        return OrderStatus(t["order_id"], "Cancelled", ext), True

    # ------------------------------------------------------------ fills

    def _on_fill(self, ext: str, st: OrderStatus) -> None:
        t = self.store.tickets()[ext]
        fill = st.fill_price if st.fill_price is not None else float(t["order"]["price"])
        self.store.update_ticket(ext, status="FILLED", order_status="Filled", fill_price=fill)
        if t["kind"] == "open":
            p = Proposal.from_dict(t["proposal"])
            mid = p.total_credit
            positions = self.store.positions()
            positions[ext] = {
                "id": ext, "ticker": p.ticker, "sector": p.sector, "expiry": p.expiry.isoformat(),
                "kind": p.kind, "quantity": p.quantity,
                "legs": [{"symbol": l.symbol, "right": l.right, "strike": l.strike, "action": l.action,
                          "quantity": l.quantity} for l in p.legs],
                "entry_credit": fill, "collateral_usd": round((max(p.put_wing, p.call_wing) - fill) * 100 * p.quantity, 2),
                "opened_at": self.now().isoformat(timespec="seconds"), "order_id": st.order_id,
                "status": "open", "flags": {}, "ex_dividend_date": t["proposal"].get("ex_dividend_date"),
            }
            self.store.save_positions(positions)
            self.journal.write("fill", ticker=p.ticker, external_identifier=ext, side="open", fill_price=fill,
                               mid_at_proposal=mid, slippage=round(mid - fill, 2))
        else:
            self._close_filled(t, fill)

    def _close_filled(self, t: dict[str, Any], fill_debit: float) -> None:
        pid = t["position_id"]
        pos = self.store.positions()[pid]
        qty = pos["quantity"]
        closed_syms = {l["symbol"] for l in t["order"]["legs"]}
        remaining = [l for l in pos["legs"] if l["symbol"] not in closed_syms]
        if t.get("side") == "C" and remaining:
            # Call side closed for dividend risk: the put spread stays open.
            self.store.update_position(pid, legs=remaining, status="open",
                                       call_side_close_debit=fill_debit, kind="put_spread_after_call_close")
            self.journal.write("fill", ticker=pos["ticker"], external_identifier=t["external_identifier"],
                               side="close_call_side", fill_price=fill_debit)
            return
        prior = pos.get("call_side_close_debit", 0.0)
        pnl = round((pos["entry_credit"] - fill_debit - prior) * 100 * qty, 2)
        self.store.update_position(pid, status="closed", closed_at=self.now().isoformat(timespec="seconds"),
                                   close_debit=fill_debit, realized_pnl_usd=pnl)
        self.journal.write("position_closed", ticker=pos["ticker"], external_identifier=t["external_identifier"],
                           position_id=pid, close_debit=fill_debit, realized_pnl_usd=pnl, reason=t["summary"].get("reason"))
        if daily_loss_breached(self.portfolio(), self.cfg):
            self.halt(f"daily realized loss over ${self.cfg.portfolio.daily_loss_halt_usd:.0f}")

    # ------------------------------------------------------------ close

    def propose_close(self, pid: str, reason: str, side: str = "all", send: bool = True) -> dict[str, Any] | None:
        positions = self.store.positions()
        pos = positions.get(pid)
        if pos is None or pos["status"] not in ("open", "closing"):
            raise AgentError(f"no open position {pid}")
        for t in self.store.tickets().values():
            if t.get("position_id") == pid and t["status"] in (AWAITING, "APPROVED", "SUBMITTED"):
                if t["status"] != AWAITING or not is_expired(t, self.now()):
                    self.out(f"{pid}: close already pending ({t['external_identifier']})")
                    return None
        pos_legs = [Leg(l["symbol"], l["right"], l["strike"], l["action"], l["quantity"]) for l in pos["legs"]]
        if side == "C":
            pos_legs = [l for l in pos_legs if l.right == "C"]
        legs = closing_legs(pos_legs)
        quotes = self.market.option_quotes([l.symbol for l in legs])
        debit = closing_debit_mid(quotes, pos_legs)
        if debit is None:
            self.journal.write("close_blocked", ticker=pos["ticker"], external_identifier=pid, reason="no quotes")
            raise AgentError(f"{pid}: cannot price the close (missing quotes)")
        gate = check_close(legs, pos_legs, debit)
        if not gate.ok:
            raise AgentError("risk: " + "; ".join(gate.violations))
        ext = new_external_identifier(pos["ticker"], date.fromisoformat(pos["expiry"]), "close")
        order = build_order(legs, debit, "Debit", ext)
        dr = self.broker.dry_run(order)
        self.journal.write("dry_run", ticker=pos["ticker"], external_identifier=ext, ok=dr.ok,
                           buying_power_effect_usd=dr.buying_power_effect_usd, fees_usd=dr.fees_usd,
                           warnings=dr.warnings, errors=dr.errors, position_id=pid)
        if not dr.ok or dr.errors:
            self.journal.write("risk_rejected", ticker=pos["ticker"], external_identifier=ext, stage="close_dry_run",
                               violations=dr.errors)
            raise AgentError(f"{pid}: close dry-run rejected: {'; '.join(dr.errors)}")
        summary = {"structure": "close" + (" call side" if side == "C" else ""), "expiry": pos["expiry"],
                   "reason": reason, "close_debit_mid": debit, "entry_credit": pos["entry_credit"],
                   "fees_usd": dr.fees_usd, "fallback_limit": f"{debit + self.cfg.pricing.reprice_step:.2f}",
                   "price_source": "modeled" if self.env == "paper" else "live", "flags": dr.warnings}
        t = create_ticket(self.store, self.journal, ext=ext, kind="close", ticker=pos["ticker"], order=order,
                          summary=summary, timeout_minutes=self.cfg.approval.timeout_minutes, now=self.now(),
                          extra={"position_id": pid, "side": side})
        self.store.update_position(pid, status="closing")
        if send:
            self.channel.send(render_ticket(t, self.env))
        return t

    # ------------------------------------------------------------ monitor

    def monitor(self) -> list[dict[str, Any]]:
        expire_stale(self.store, self.journal, self.now())
        # Closing tickets that expired unanswered put the position back to "open".
        tickets = self.store.tickets()
        for pid, pos in self.store.open_positions().items():
            if pos["status"] == "closing" and not any(
                    t.get("position_id") == pid and t["status"] in (AWAITING, "APPROVED", "SUBMITTED")
                    for t in tickets.values()):
                self.store.update_position(pid, status="open")

        for ext, t in tickets.items():
            if t["status"] == "SUBMITTED" and t.get("order_id"):
                self.manage_working(ext)

        try:
            broker_positions = self.broker.positions()
        except (BrokerError, AuthError) as e:
            self.out(f"positions unavailable: {e}")
            broker_positions = None

        today = self.today()
        new_tickets = []
        for pid, pos in self.store.open_positions().items():
            if broker_positions is not None:
                issues = detect_assignment(pos, broker_positions)
                if issues:
                    self.journal.write("assignment_detected", ticker=pos["ticker"], external_identifier=pid,
                                       issues=issues)
                    if not self.halted():
                        self.halt(f"possible early assignment on {pid}")
                    remaining = [l for l in pos["legs"] if not any(
                        i.startswith(f"short leg {l['symbol']}") for i in issues)]
                    self.out(f"{pid}: {'; '.join(issues)}. Close the remaining legs and any stock today "
                             f"({len(remaining)} option legs left). See runbook.md 'Early assignment'.")
                    continue
            pos_legs = [Leg(l["symbol"], l["right"], l["strike"], l["action"], l["quantity"]) for l in pos["legs"]]
            try:
                q = self.market.underlying_quote(pos["ticker"])
                debit = closing_debit_mid(self.market.option_quotes([l.symbol for l in pos_legs]), pos_legs)
            except (BrokerError, AuthError, KeyError) as e:
                # Can't mark it: still run the date-based rules so a day-before-expiry close isn't missed.
                self.out(f"{pid}: quotes unavailable ({e})")
                q, debit = {"price": None, "day_low": None, "day_high": None}, None
            exd = date.fromisoformat(pos["ex_dividend_date"]) if pos.get("ex_dividend_date") else None
            d = evaluate_exits(pos, today, q["price"], q["day_low"], q["day_high"], debit, self.cfg.exits, exd)
            flags = dict(pos.get("flags", {}), short_put_touched=d.touched_put, short_call_touched=d.touched_call)
            self.store.update_position(pid, flags=flags, last_mark_debit=debit,
                                       last_checked=self.now().isoformat(timespec="seconds"))
            self.journal.write("monitor", ticker=pos["ticker"], external_identifier=pid, spot=q["price"],
                               close_debit_mid=debit, pnl_per_share=d.pnl_per_share, triggers=d.triggers)
            if d.should_close and pos["status"] == "open":
                try:
                    t = self.propose_close(pid, "; ".join(d.triggers), d.close_side)
                except AgentError as e:
                    self.out(str(e))
                    continue
                if t:
                    new_tickets.append(t)
        self.collect_replies([t["external_identifier"] for t in new_tickets])
        ops.put_metric("RunCompleted", job="monitor", region=self.settings.aws_region)
        return new_tickets

    # ------------------------------------------------------------ resolve UNKNOWN

    def resolve(self, ext: str, found_order_id: str | None) -> None:
        t = self.store.tickets().get(ext)
        if t is None or t["status"] != "UNKNOWN":
            raise AgentError(f"{ext} is not an UNKNOWN ticket")
        if found_order_id:
            st = self.broker.order_status(found_order_id, ext)
            self.store.update_ticket(ext, status="SUBMITTED", order_id=found_order_id, order_status=st.status)
            self.journal.write("unknown_resolved", ticker=t["ticker"], external_identifier=ext, found=True,
                               order_id=found_order_id, status=st.status)
            if st.is_filled:
                self._on_fill(ext, st)
        else:
            self.store.update_ticket(ext, status="NOT_PLACED")
            self.journal.write("unknown_resolved", ticker=t["ticker"], external_identifier=ext, found=False)


def make_agent(settings: Settings, cfg: StrategyConfig, out: Callable[[str], None] = print) -> Agent:
    """Wire the agent for TT_ENV: paper (offline), sandbox or production (tastytrade API)."""
    from .screen import load_universe

    import os

    journal = Journal(settings.journal_dir, settings.env)
    store = StateStore(settings.state_dir)
    clock: Callable[[], datetime] = utcnow
    if settings.env == "paper":
        from .paper import PaperBroker, PaperMarket

        # TA_PAPER_CLOCK=2026-10-26T15:00:00+00:00 simulates "now" (paper only), e.g. to
        # try a submit on a weekend or fast-forward to an exit. Gates are unchanged.
        fake_now = os.environ.get("TA_PAPER_CLOCK")
        if fake_now:
            fixed = datetime.fromisoformat(fake_now)
            clock = lambda: fixed  # noqa: E731
        market: Any = PaperMarket(settings.data_dir / "paper" / "market.json",
                                  today=clock().astimezone(NY).date() if fake_now else None)
        broker: Any = PaperBroker(market, settings.state_dir / "paper_broker.json", cfg.account.size_usd)
        universe_path = settings.data_dir / "paper" / "universe.csv"
    else:
        from .auth import TokenManager, load_credentials
        from .broker import TastytradeBroker
        from .marketdata import CsvHistory, TastytradeMarketData

        tokens = TokenManager(settings.env, load_credentials(settings.env, region=settings.aws_region))
        broker = TastytradeBroker(settings.env, tokens)
        market = TastytradeMarketData(broker, CsvHistory(settings.data_dir / "history"))
        universe_path = settings.data_dir / "sp500.csv"
    universe = load_universe(universe_path) if universe_path.exists() else None
    channel = make_channel(settings.approval_channel)
    return Agent(settings, cfg, broker, market, universe, journal, store, channel, clock=clock, out=out)

