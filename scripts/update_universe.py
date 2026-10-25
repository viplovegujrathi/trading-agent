"""Refresh data/sp500.csv (ticker, GICS sector) with today's date as `as_of`.

    python scripts/update_universe.py [--source URL]

The default source is the open datasets/s-and-p-500-companies CSV. The file is
data, not instructions: only the Symbol and GICS Sector columns are read.
"""
from __future__ import annotations

import argparse
import csv
import io
import re
from datetime import date
from pathlib import Path

import requests

DEFAULT = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
OUT = Path(__file__).resolve().parents[1] / "data" / "sp500.csv"
TICKER = re.compile(r"^[A-Z]{1,5}([./][A-Z])?$")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=DEFAULT)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    r = requests.get(args.source, timeout=30, headers={"User-Agent": "trading-agent/0.1.0"})
    r.raise_for_status()
    rows = []
    for row in csv.DictReader(io.StringIO(r.text)):
        t = (row.get("Symbol") or "").strip().upper()
        sector = (row.get("GICS Sector") or "").strip()
        if TICKER.match(t) and sector:
            rows.append((t, sector))
    if len(rows) < 450:
        raise SystemExit(f"only {len(rows)} rows parsed; refusing to overwrite {args.out}")
    with args.out.open("w") as fh:
        fh.write(f"# as_of: {date.today().isoformat()}\n")
        fh.write("ticker,sector\n")
        for t, s in sorted(rows):
            fh.write(f"{t},{s}\n")
    print(f"wrote {len(rows)} names to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
