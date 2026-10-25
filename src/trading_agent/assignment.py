"""Early-assignment detection (risk.md): halt new opens and propose closing what is left."""
from __future__ import annotations

from pathlib import Path

from . import journal
from .halt import set_halt


def detect(position: dict, broker_legs: dict[str, int]) -> list[str]:
    """broker_legs: {leg name: signed quantity the broker reports}. Expected: short legs
    -contracts, long legs +contracts. Returns names of legs that differ (assigned/exercised)."""
    n = position["contracts"]
    exp = {"short_put": -n, "long_put": n}
    if position.get("short_call") is not None:
        exp |= {"short_call": -n, "long_call": n}
    return [k for k, q in exp.items() if broker_legs.get(k, 0) != q]


def handle(position: dict, broker_legs: dict[str, int], env: str,
           root: Path = journal.ROOT) -> dict | None:
    bad = detect(position, broker_legs)
    if not bad:
        return None
    set_halt(f"assignment on {position['ticker']}: {','.join(bad)}", root)
    journal.append_event("assignment", position["ticker"], position["ext"], env=env,
                         root=root, legs=bad)
    remaining = [k for k in broker_legs if broker_legs[k] != 0 and k not in bad]
    return {"action": "propose_close_remaining", "ticker": position["ticker"],
            "remaining_legs": remaining, "assigned_legs": bad}
