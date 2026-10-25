"""S&P 500 list from data/sp500.csv. First line: '# list_date=YYYY-MM-DD'."""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "data" / "sp500.csv"


def load_universe(path: Path = PATH) -> tuple[date, dict[str, str]]:
    lines = path.read_text().splitlines()
    d = date.fromisoformat(lines[0].split("list_date=")[1].split()[0])
    rows = csv.DictReader(lines[1:])
    return d, {r["ticker"]: r["sector"] for r in rows}
