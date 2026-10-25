"""Command line: status | screen | propose | submit | decline | monitor | close | halt | resolve | tickets | summary | backup.

    python -m trading_agent.cli status
    python -m trading_agent.cli screen --dry
    python -m trading_agent.cli propose AAPL
    python -m trading_agent.cli submit ic-AAPL-20261204-1a2b3c4d --confirm "YES ic-AAPL-20261204-1a2b3c4d"
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from typing import Any

from . import ops
from .agent import Agent, AgentError, make_agent
from .approval import AWAITING, ApprovalError, record_decision, render_ticket
from .auth import AuthError
from .broker import BrokerError
from .config import ConfigError, load_settings, load_strategy
from .risk import clear_halt, halt_path
from .screen import ScreenResult
from .tradingcal import in_trading_window, is_trading_day, to_display


def _fmt(x: Any, nd: int = 2) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def print_screen_table(results: list[ScreenResult]) -> None:
    hdr = f"{'ticker':<7}{'price':>9}{'50dma':>9}{'%<50d':>7}{'%offHi':>8}{'RSI':>6}{'IVR':>6}  {'earnings':<11}{'ex-div':<11}result"
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(results, key=lambda r: (not r.passed, r.ticker)):
        s = r.snapshot
        res = "PASS" + (f" ({'; '.join(r.flags)})" if r.flags else "") if r.passed else "fail: " + "; ".join(r.reasons)
        print(f"{r.ticker:<7}{_fmt(s.price if s else None):>9}{_fmt(s.sma50 if s else None):>9}"
              f"{_fmt(r.pct_below_sma50, 1):>7}{_fmt(r.pct_off_high, 1):>8}{_fmt(s.rsi14 if s else None, 0):>6}"
              f"{_fmt(s.iv_rank if s else None, 0):>6}  {_fmt(s.earnings_date if s else None):<11}"
              f"{_fmt(s.ex_dividend_date if s else None):<11}{res}")
    if results and results[0].snapshot:
        print(f"\nprices: {results[0].snapshot.price_source}")


def cmd_status(a: Agent, args: argparse.Namespace) -> int:
    if a.env == "production":
        print("*** PRODUCTION: LIVE MONEY ***")
    print(f"env: {a.env}    strategy: {a.cfg.source}")
    print(f"time: {to_display(a.now())}    trading window open: {in_trading_window(a.now(), a.cfg.pricing.avoid_first_last_minutes)}")
    try:
        b = a.broker.balances()
        tokens = getattr(a.broker, "tokens", None)
        print("token: ok    account: " + (f"...{tokens.account[-4:]}" if tokens else "paper (simulated)"))
        print(f"buying power: {_fmt(b.get('derivative_buying_power'))}   net liq: {_fmt(b.get('net_liquidating_value'))}"
              f"   cash: {_fmt(b.get('cash_balance'))}")
    except (AuthError, BrokerError) as e:
        print(f"token/account: FAILED ({e})")
    halted = a.halted()
    print(f"kill switch: {'ON' if halted else 'off'}")
    if halt_path(a.settings.state_dir).exists():
        print(halt_path(a.settings.state_dir).read_text().rstrip())
    opens = a.store.open_positions()
    print(f"open condors: {len(opens)} / {a.cfg.portfolio.max_open_condors}")
    for pid, p in opens.items():
        print(f"  {pid}  {p['ticker']} exp {p['expiry']} credit {p['entry_credit']:.2f} "
              f"status {p['status']} last debit {_fmt(p.get('last_mark_debit'))}")
    pending = [t for t in a.store.tickets().values() if t["status"] in (AWAITING, "SUBMITTED", "UNKNOWN")]
    for t in pending:
        print(f"  ticket {t['external_identifier']} {t['kind']} {t['status']}")
    if a.universe:
        print(f"universe: {len(a.universe.sectors)} names, dated {a.universe.as_of}")
    else:
        print("universe: MISSING (see data/README.md)")
    return 0


def cmd_screen(a: Agent, args: argparse.Namespace) -> int:
    tickers = [t.upper() for t in args.tickers] if args.tickers else None
    if args.dry:
        print_screen_table(a.screen(tickers))
        return 0
    if not is_trading_day(a.today()):
        print("not a trading day: no proposals")
        return 0
    results = a.run_screen_and_propose()
    ops.put_metric("RunCompleted", job="screen", region=a.settings.aws_region)
    print(f"{sum(1 for r in results if r.ok)} ticket(s) sent for approval")
    return 0


def cmd_propose(a: Agent, args: argparse.Namespace) -> int:
    r = a.propose(args.ticker, send=True)
    if not r.ok:
        print(f"{r.ticker}: no ticket")
        for reason in r.reasons:
            print(f"  - {reason}")
        return 1
    return 0


def cmd_submit(a: Agent, args: argparse.Namespace) -> int:
    confirm = args.confirm
    if confirm is None:
        if not sys.stdin.isatty():
            print(f'refusing: pass --confirm "YES {args.ext}" or run interactively')
            return 2
        t = a.store.tickets().get(args.ext)
        if t:
            print(render_ticket(t, a.env))
        confirm = input(f"Type YES {args.ext} to submit: ")
    st = a.submit(args.ext, confirm, by=args.by or f"cli:{os.environ.get('USER', '?')}", wait=not args.no_wait)
    print(f"{args.ext}: {st.status}" + (f" @ {st.fill_price:.2f}" if st.fill_price else ""))
    return 0 if st.status not in ("UNKNOWN", "Rejected") else 1


def cmd_decline(a: Agent, args: argparse.Namespace) -> int:
    record_decision(a.store, a.journal, args.ext, "no", args.by or "cli", a.now())
    print(f"{args.ext}: declined")
    return 0


def cmd_monitor(a: Agent, args: argparse.Namespace) -> int:
    tickets = a.monitor()
    print(f"{len(a.store.open_positions())} open; {len(tickets)} close ticket(s) proposed")
    return 0


def cmd_close(a: Agent, args: argparse.Namespace) -> int:
    t = a.propose_close(args.id, args.reason or "manual close")
    return 0 if t else 1


def cmd_halt(a: Agent, args: argparse.Namespace) -> int:
    if args.clear:
        if not args.note:
            print("refusing: --clear needs --note explaining why (journaled)")
            return 2
        if clear_halt(a.settings.state_dir):
            a.journal.write("halt_cleared", note=args.note)
            print("kill switch off" + (" (TA_HALT=1 is still set in the environment)" if a.settings.halt_env else ""))
        else:
            print("kill switch was not on")
        return 0
    a.halt(args.reason or "manual")
    return 0


def cmd_resolve(a: Agent, args: argparse.Namespace) -> int:
    if not args.found and not args.not_found:
        print("pass --found <order-id> or --not-found")
        return 2
    a.resolve(args.ext, args.found)
    print(f"{args.ext}: resolved. Clear the kill switch with `halt --clear --note ...` when ready.")
    return 0


def cmd_tickets(a: Agent, args: argparse.Namespace) -> int:
    for ext, t in sorted(a.store.tickets().items(), key=lambda kv: kv[1]["created_at"]):
        if args.all or t["status"] in (AWAITING, "APPROVED", "SUBMITTED", "UNKNOWN"):
            created = to_display(datetime.fromisoformat(t["created_at"]))
            print(f"{ext}  {t['kind']:<5} {t['status']:<18} limit {t['order']['price']} {created}")
    return 0


def cmd_summary(a: Agent, args: argparse.Namespace) -> int:
    today = a.today().isoformat()
    events = [e for e in a.journal.events() if e["ts"][:10] >= today]
    closed = [e for e in events if e["event"] == "position_closed"]
    print(f"[{a.env}] daily summary {today}")
    print(f"open condors: {len(a.store.open_positions())}")
    print(f"tickets today: {sum(1 for e in events if e['event'] == 'ticket_created')}, "
          f"orders submitted: {sum(1 for e in events if e['event'] == 'order_submitted')}")
    print(f"realized P&L today: ${sum(e.get('realized_pnl_usd', 0) for e in closed):,.2f}")
    for pid, p in a.store.open_positions().items():
        print(f"  {p['ticker']} exp {p['expiry']} credit {p['entry_credit']:.2f} last debit {_fmt(p.get('last_mark_debit'))}"
              f" flags {p.get('flags')}")
    if a.halted():
        print("kill switch: ON")
    return 0


def cmd_backup(a: Agent, args: argparse.Namespace) -> int:
    for c in ops.backup(a.settings.journal_dir, a.settings.state_dir, os.environ.get("TA_BACKUP_BUCKET")):
        print(c)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="trading_agent.cli", description="Iron condor agent for tastytrade")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    s = sub.add_parser("screen")
    s.add_argument("--dry", action="store_true", help="candidate table only, no proposals")
    s.add_argument("--tickers", nargs="*")
    s = sub.add_parser("propose")
    s.add_argument("ticker")
    s = sub.add_parser("submit")
    s.add_argument("ext")
    s.add_argument("--confirm", help='exactly "YES <external-identifier>"')
    s.add_argument("--by", help="who approved (journaled)")
    s.add_argument("--no-wait", action="store_true", help="don't follow a working order")
    s = sub.add_parser("decline")
    s.add_argument("ext")
    s.add_argument("--by")
    sub.add_parser("monitor")
    s = sub.add_parser("close")
    s.add_argument("id")
    s.add_argument("--reason")
    s = sub.add_parser("halt")
    s.add_argument("--clear", action="store_true")
    s.add_argument("--note")
    s.add_argument("--reason")
    s = sub.add_parser("resolve")
    s.add_argument("ext")
    s.add_argument("--found", metavar="ORDER_ID")
    s.add_argument("--not-found", action="store_true")
    s = sub.add_parser("tickets")
    s.add_argument("--all", action="store_true")
    sub.add_parser("summary")
    sub.add_parser("backup")
    return p


COMMANDS = {
    "status": cmd_status, "screen": cmd_screen, "propose": cmd_propose, "submit": cmd_submit,
    "decline": cmd_decline, "monitor": cmd_monitor, "close": cmd_close, "halt": cmd_halt,
    "resolve": cmd_resolve, "tickets": cmd_tickets, "summary": cmd_summary, "backup": cmd_backup,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
        cfg = load_strategy(env=settings.env)
        agent = make_agent(settings, cfg)
        return COMMANDS[args.cmd](agent, args)
    except (AgentError, ApprovalError, ConfigError, AuthError, BrokerError) as e:
        ops.put_metric("RunFailed", job=args.cmd)
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
