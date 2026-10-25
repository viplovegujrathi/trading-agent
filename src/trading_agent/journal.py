"""Append-only JSONL journal and positions.json. Times stored in UTC."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("TA_HOME", Path(__file__).resolve().parents[2]))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def append_event(event: str, ticker: str | None, external_identifier: str | None,
                 env: str = "sandbox", root: Path = ROOT, now: datetime | None = None,
                 **fields) -> dict:
    ts = now or _now()
    rec = {"ts": ts.isoformat(), "event": event, "ticker": ticker,
           "external_identifier": external_identifier, "env": env, **fields}
    d = root / "journal"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{ts.date().isoformat()}.jsonl", "a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    return rec


def read_events(day: str, root: Path = ROOT) -> list[dict]:
    p = root / "journal" / f"{day}.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def load_positions(root: Path = ROOT) -> list[dict]:
    p = root / "state" / "positions.json"
    return json.loads(p.read_text()) if p.exists() else []


def save_positions(positions: list[dict], root: Path = ROOT) -> None:
    d = root / "state"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "positions.json.tmp"
    tmp.write_text(json.dumps(positions, indent=2, sort_keys=True))
    tmp.replace(d / "positions.json")
