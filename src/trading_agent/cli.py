"""CLI skeleton. Commands needing the broker are added once connections exist."""
from __future__ import annotations

import argparse

from .halt import clear_halt, is_halted, set_halt
from .journal import load_positions


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="trading_agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("halt")
    h.add_argument("--clear", metavar="NOTE")
    h.add_argument("--reason", default="manual")
    sub.add_parser("status")
    sub.add_parser("backup")
    d = sub.add_parser("dashboard", help="serve the read-only dashboard on 127.0.0.1")
    d.add_argument("--port", type=int, default=8787)
    d.add_argument("--host", default="127.0.0.1", help="127.0.0.1 or this box's Tailscale 100.x address")
    for name in ("run-open", "run-monitor"):
        r = sub.add_parser(name, help="paper-trading cycle (Yahoo data, simulated account)")
        r.add_argument("--force", action="store_true", help="ignore market hours (testing)")
        if name == "run-open":
            r.add_argument("tickers", nargs="*")
    sc = sub.add_parser("screen")
    sc.add_argument("--dry", action="store_true", help="print table, place nothing")
    sc.add_argument("tickers", nargs="*")
    a = ap.parse_args(argv)
    if a.cmd == "halt":
        if a.clear:
            clear_halt(a.clear)
            print("halt cleared")
        else:
            set_halt(a.reason)
            print("halt set")
    elif a.cmd == "status":
        print(f"halted: {is_halted()}  open condors: {len(load_positions())}")
    elif a.cmd == "screen":
        _screen(a.tickers)
    elif a.cmd == "dashboard":
        from .dashboard import serve
        serve(a.port, host=a.host)
    elif a.cmd == "backup":
        from .ops import backup
        ok = backup()
        print("backup ok" if ok else "backup skipped or failed (see TA_BACKUP_BUCKET and aws CLI)")
        return 0 if ok else 1
    elif a.cmd in ("run-open", "run-monitor"):
        from .ops import metric
        try:
            _run(a.cmd, getattr(a, "tickers", []), a.force)
        except BaseException:
            metric("RunFailed", a.cmd)
            raise
        metric("RunCompleted", a.cmd)
    return 0


def _run(cmd: str, tickers: list[str], force: bool) -> None:
    import os
    from datetime import datetime, timezone
    from . import journal, options_yahoo as oy, engine
    from .calendar_us import HOLIDAYS, session_minutes
    from .config import load_config
    from .paper import PaperBroker
    from .prices_yahoo import fetch_snapshot
    from .screen import screen_name
    from .structure import pick_expiry
    from .universe import load_universe

    env = os.environ.get("TT_ENV", "paper")
    if env not in ("paper", "sandbox"):
        raise SystemExit("run-open/run-monitor use Yahoo data and the paper broker: TT_ENV must be paper")
    cfg, now = load_config(), datetime.now(timezone.utc)
    today = now.date()
    sm = session_minutes(now)
    if sm is None and not force:
        print("market closed; use --force to run anyway")
        return
    sm = sm or (60, 300)
    broker = PaperBroker(cfg["account"]["size_usd"])
    if cmd == "run-open":
        _, uni = load_universe()
        from concurrent.futures import ThreadPoolExecutor
        names = tickers or list(uni)
        with ThreadPoolExecutor(8) as ex:                  # Yahoo history is the slow part
            snaps = list(ex.map(fetch_snapshot, names))
        cands, no_data = [], 0
        for t, snap in zip(names, snaps):
            if snap is None:
                no_data += 1
                continue
            exp = pick_expiry(oy.listed_expiries(t), today, cfg)
            if exp is None or not screen_name(snap, exp, cfg).passed:
                continue
            cands.append(engine.Candidate(snap, uni.get(t, "Unknown"), exp,
                                          oy.fetch_chain(t, exp, snap.price, today)))
        print(f"screened {len(names)} names, {no_data} without data")
        res = engine.open_cycle(cands, broker, broker.available_cash, cfg, env, today, sm[0], sm[1])
        print(f"candidates passing screen: {len(cands)}")
        for t, o in res:
            print(f"{t:6} {o.status} {'; '.join(o.detail)}")
    else:
        marks = {}
        for p in journal.load_positions():
            snap = fetch_snapshot(p["ticker"])
            ch = oy.fetch_chain(p["ticker"], __import__("datetime").date.fromisoformat(p["expiry"]),
                                snap.price if snap else 0, today) if snap else []
            c = oy.cost_to_close(p, ch)
            if c is None or snap is None:
                continue
            marks[p["ext"]] = dict(cost_to_close=c, low=snap.price, high=snap.price,
                                   ex_div_before_expiry=snap.next_ex_dividend is not None
                                   and snap.next_ex_dividend.isoformat() <= p["expiry"],
                                   call_near_money=p["short_call"] is not None
                                   and snap.price >= 0.97 * p["short_call"])
        for t, act, why in engine.monitor_cycle(marks, broker, cfg, env, today, HOLIDAYS):
            print(f"{t:6} {act} {', '.join(why)}")
        print(f"paper cash {broker.acct['cash']:.2f}  reserved {broker.acct['reserved']:.2f}")


def _screen(tickers: list[str]) -> None:
    from datetime import date, timedelta
    from .config import load_config
    from .prices_yahoo import fetch_snapshot
    from .screen import screen_name
    from .universe import load_universe
    from .screen import universe_stale
    cfg = load_config()
    listed, uni = load_universe()
    if universe_stale(listed, date.today(), cfg):
        print(f"WARNING: universe list dated {listed} is stale")
    expiry = date.today() + timedelta(days=cfg["structure"]["dte_target"])
    print("prices: modeled (Yahoo, screening only)")
    for t in tickers or uni:
        snap = fetch_snapshot(t)
        if snap is None:
            print(f"{t:6} EXCLUDED  no data")
            continue
        r = screen_name(snap, expiry, cfg)
        print(f"{t:6} {'PASS' if r.passed else 'FAIL'}  px={snap.price:.2f} "
              f"52wH={snap.high_52w:.2f} 50dma={snap.dma50:.2f} rsi={snap.rsi14:.0f} "
              f"{'; '.join(r.reasons + r.flags)}")


if __name__ == "__main__":
    raise SystemExit(main())
