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
    return 0


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
