"""Append-only JSONL journal, plus state files (positions, tickets).

Journal lines always carry ts (UTC ISO), event, ticker, external_identifier, env.
Secrets never go in here: callers pass only order/market data.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .tradingcal import utcnow

SECRET_KEYS = {"client_secret", "refresh_token", "access_token", "authorization", "bot_token", "token"}


def _scrub(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("***" if k.lower() in SECRET_KEYS else _scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True, default=str)
        fh.write("\n")
    os.replace(tmp, path)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


@dataclass
class Journal:
    dir: Path
    env: str
    clock: Callable[[], datetime] = field(default=utcnow, repr=False)

    def write(self, event: str, *, ticker: str | None = None, external_identifier: str | None = None,
              now: datetime | None = None, **data: Any) -> dict[str, Any]:
        ts_utc = (now or self.clock()).astimezone(timezone.utc)
        line = {
            "ts": ts_utc.isoformat(timespec="seconds"),
            "event": event,
            "ticker": ticker,
            "external_identifier": external_identifier,
            "env": self.env,
            **_scrub(data),
        }
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{ts_utc.date().isoformat()}.jsonl"
        with path.open("a") as fh:
            fh.write(json.dumps(line, sort_keys=False, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return line

    def events(self, day: str | None = None) -> Iterator[dict[str, Any]]:
        files = [self.dir / f"{day}.jsonl"] if day else sorted(self.dir.glob("*.jsonl"))
        for f in files:
            if not f.exists():
                continue
            for raw in f.read_text().splitlines():
                if raw.strip():
                    yield json.loads(raw)


@dataclass
class StateStore:
    dir: Path

    @property
    def positions_path(self) -> Path:
        return self.dir / "positions.json"

    @property
    def tickets_path(self) -> Path:
        return self.dir / "tickets.json"

    def positions(self) -> dict[str, dict[str, Any]]:
        return read_json(self.positions_path, {})

    def save_positions(self, positions: dict[str, dict[str, Any]]) -> None:
        atomic_write_json(self.positions_path, positions)

    def open_positions(self) -> dict[str, dict[str, Any]]:
        return {k: v for k, v in self.positions().items() if v.get("status") in ("open", "closing")}

    def tickets(self) -> dict[str, dict[str, Any]]:
        return read_json(self.tickets_path, {})

    def save_tickets(self, tickets: dict[str, dict[str, Any]]) -> None:
        atomic_write_json(self.tickets_path, tickets)

    def update_ticket(self, ext: str, **changes: Any) -> dict[str, Any]:
        t = self.tickets()
        if ext not in t:
            raise KeyError(ext)
        t[ext].update(changes)
        self.save_tickets(t)
        return t[ext]

    def update_position(self, pid: str, **changes: Any) -> dict[str, Any]:
        p = self.positions()
        if pid not in p:
            raise KeyError(pid)
        p[pid].update(changes)
        self.save_positions(p)
        return p[pid]
