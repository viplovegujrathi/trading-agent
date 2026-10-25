"""Decides whether an order needs a human yes. Fails closed.

auto in sandbox or paper: always (no real money). auto in production: only after the plan.md Phase 5 graduation
criteria are met in the journal. Anything else (unknown env, missing stats): human."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LiveRecord:
    closed_live_condors: int = 0
    limit_breaches: int = 0
    unknown_orders: int = 0


def approval_route(env: str, cfg: dict, rec: LiveRecord | None = None) -> str:
    a = cfg["approval"]
    if a.get("mode") != "auto":
        return "human"
    if env in ("sandbox", "paper"):
        return "auto"
    if env == "production" and rec is not None:
        g = a["graduation"]
        if (rec.closed_live_condors >= g["min_closed_live_condors"]
                and rec.limit_breaches <= g["max_limit_breaches"]
                and rec.unknown_orders <= g["max_unknown_orders"]):
            return "auto"
    return "human"


def live_record_from_journal(events: list[dict]) -> LiveRecord:
    live = [e for e in events if e.get("env") == "production"]
    return LiveRecord(
        closed_live_condors=sum(e["event"] == "close_filled" for e in live),
        limit_breaches=sum(e["event"] == "limit_breach" for e in live),
        unknown_orders=sum(e["event"] == "order_unknown" for e in live),
    )
