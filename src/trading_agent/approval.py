"""Approval gate: a YES counts only for the exact ticket, from the owner, within the window.
Transport (Slack/Telegram) is plugged in later; this is the decision logic."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class Ticket:
    external_identifier: str
    created: datetime
    summary: str
    state: str = "awaiting"       # awaiting | approved | declined | expired


def process_reply(t: Ticket, text: str, sender: str, owner: str, now: datetime,
                  timeout_minutes: int = 15) -> Ticket:
    if t.state != "awaiting":
        return t
    if now - t.created > timedelta(minutes=timeout_minutes):
        t.state = "expired"
        return t
    if sender != owner:
        return t
    parts = text.strip().split()
    if len(parts) == 2 and parts[1] == t.external_identifier:
        if parts[0].upper() == "YES":
            t.state = "approved"
        elif parts[0].upper() == "NO":
            t.state = "declined"
    return t


def expire_if_due(t: Ticket, now: datetime, timeout_minutes: int = 15) -> Ticket:
    if t.state == "awaiting" and now - t.created > timedelta(minutes=timeout_minutes):
        t.state = "expired"
    return t
