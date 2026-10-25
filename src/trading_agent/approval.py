"""Approval gate: send a ticket, accept `YES <external-identifier>` only, expire after 15 min.

This module is the only place a broker.Approval is created. An approval covers
exactly one ticket; a yes for one ticket never covers another.

Channels:
  cli       - the ticket is printed and journaled; you approve with
              `cli submit <ext> --confirm "YES <ext>"` (or interactively).
  telegram  - bot with long polling, replies accepted only from TA_TELEGRAM_USER_ID.
  slack     - not built yet; selecting it fails closed (tickets expire unsent).
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Protocol

import requests

from .broker import Approval
from .journal import Journal, StateStore
from .tradingcal import to_display, utcnow

AWAITING = "AWAITING_APPROVAL"
_REPLY_RE = re.compile(r"^\s*(YES|NO)\s+(\S+)\s*$", re.IGNORECASE)


class ApprovalError(RuntimeError):
    pass


def parse_reply(text: str) -> tuple[str, str] | None:
    """`YES ic-AAPL-...` -> ("yes", ext). Anything else -> None."""
    m = _REPLY_RE.match(text or "")
    if not m:
        return None
    return m.group(1).lower(), m.group(2)


def create_ticket(store: StateStore, journal: Journal, *, ext: str, kind: str, ticker: str,
                  order: dict[str, Any], summary: dict[str, Any], timeout_minutes: int,
                  now: datetime | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    now = now or utcnow()
    tickets = store.tickets()
    if ext in tickets:
        raise ApprovalError(f"ticket {ext} already exists")
    t = {
        "external_identifier": ext,
        "kind": kind,
        "ticker": ticker,
        "status": AWAITING,
        "created_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(minutes=timeout_minutes)).isoformat(timespec="seconds"),
        "order": order,
        "summary": summary,
        **(extra or {}),
    }
    tickets[ext] = t
    store.save_tickets(tickets)
    journal.write("ticket_created", ticker=ticker, external_identifier=ext, kind=kind,
                  expires_at=t["expires_at"], summary=summary)
    return t


def is_expired(t: dict[str, Any], now: datetime | None = None) -> bool:
    return (now or utcnow()) > datetime.fromisoformat(t["expires_at"])


def record_decision(store: StateStore, journal: Journal, ext: str, decision: str, by: str,
                    now: datetime | None = None) -> Approval | None:
    """Apply a yes/no to one open ticket. Returns an Approval only for a valid yes."""
    now = now or utcnow()
    tickets = store.tickets()
    t = tickets.get(ext)
    if t is None:
        raise ApprovalError(f"no ticket {ext}")
    if t["status"] != AWAITING:
        raise ApprovalError(f"ticket {ext} is {t['status']}, not awaiting approval")
    if is_expired(t, now):
        t["status"] = "EXPIRED"
        store.save_tickets(tickets)
        journal.write("approval_expired", ticker=t["ticker"], external_identifier=ext)
        raise ApprovalError(f"ticket {ext} expired at {to_display(datetime.fromisoformat(t['expires_at']))}")
    if decision == "no":
        t.update(status="DECLINED", decided_at=now.isoformat(timespec="seconds"), decided_by=by)
        store.save_tickets(tickets)
        journal.write("approval_declined", ticker=t["ticker"], external_identifier=ext, by=by)
        return None
    if decision != "yes":
        raise ApprovalError(f"unknown decision {decision!r}")
    t.update(status="APPROVED", decided_at=now.isoformat(timespec="seconds"), decided_by=by)
    store.save_tickets(tickets)
    journal.write("approval_granted", ticker=t["ticker"], external_identifier=ext, by=by)
    return Approval(external_identifier=ext, approved_by=by, approved_at=t["decided_at"])


def approval_for_approved_ticket(t: dict[str, Any]) -> Approval:
    """Rebuild the Approval for a ticket already APPROVED (e.g. a reprice of the same ticket)."""
    if t.get("status") not in ("APPROVED", "SUBMITTED"):
        raise ApprovalError(f"ticket {t['external_identifier']} is not approved")
    return Approval(t["external_identifier"], t.get("decided_by", "?"), t.get("decided_at", "?"))


def expire_stale(store: StateStore, journal: Journal, now: datetime | None = None) -> list[str]:
    tickets = store.tickets()
    expired = []
    for ext, t in tickets.items():
        if t["status"] == AWAITING and is_expired(t, now):
            t["status"] = "EXPIRED"
            expired.append(ext)
            journal.write("approval_expired", ticker=t["ticker"], external_identifier=ext)
    if expired:
        store.save_tickets(tickets)
    return expired


# ---------------------------------------------------------------- rendering


def _money(x: float | None) -> str:
    return "n/a" if x is None else f"${x:,.2f}"


def render_ticket(t: dict[str, Any], env: str) -> str:
    s = t["summary"]
    o = t["order"]
    lines = []
    if env == "production":
        lines.append("*** PRODUCTION: LIVE MONEY ***")
    title = "OPEN" if t["kind"] == "open" else "CLOSE"
    lines.append(f"[{env}] {title} {t['ticker']} {s.get('structure', '')} exp {s.get('expiry')}")
    for l in o["legs"]:
        lines.append(f"  {l['action']:<13} {l['quantity']} x {l['symbol']}")
    lines.append(f"  Limit: {o['price']} net {o['price-effect'].lower()} (Day)  "
                 f"fallback: {s.get('fallback_limit', 'n/a')}  prices: {s.get('price_source', '?')}")
    if t["kind"] == "open":
        lines.append(f"  Collateral {_money(s.get('collateral_usd'))} (dry-run BP {_money(s.get('dry_run_bp_usd'))}, "
                     f"fees {_money(s.get('fees_usd'))})")
        lines.append(f"  Max profit {_money(s.get('max_profit_usd'))}  max loss {_money(s.get('max_loss_usd'))}")
        lines.append(f"  Breakevens {s.get('breakeven_low')} / {s.get('breakeven_high') or '-'}  "
                     f"short deltas {s.get('short_put_delta')} / {s.get('short_call_delta') or '-'} ({s.get('delta_source')})")
        lines.append("  Exits: 50% max profit, 21 DTE, short strike touched, 2x credit loss, day before expiry")
    else:
        lines.append(f"  Reason: {s.get('reason')}")
    for f in s.get("flags", []):
        lines.append(f"  Flag: {f}")
    lines.append(f"  Expires {to_display(datetime.fromisoformat(t['expires_at']))}")
    lines.append(f"Reply YES {t['external_identifier']} to submit.")
    return "\n".join(lines)


# ---------------------------------------------------------------- channels


class Channel(Protocol):
    name: str

    def send(self, text: str) -> None: ...
    def wait(self, exts: list[str], deadline: datetime) -> dict[str, tuple[str, str]]: ...


class CliChannel:
    """Prints the ticket. Approval arrives later via `cli submit <ext> --confirm`."""

    name = "cli"

    def __init__(self, out: Callable[[str], None] = print) -> None:
        self.out = out

    def send(self, text: str) -> None:
        self.out(text)

    def wait(self, exts: list[str], deadline: datetime) -> dict[str, tuple[str, str]]:
        return {}


class TelegramChannel:
    name = "telegram"

    def __init__(self, token: str, chat_id: str, user_id: str, session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._token = token
        self.chat_id = chat_id
        self.user_id = str(user_id)
        self.session = session or requests.Session()
        self.sleep = sleep
        self._offset: int | None = None

    def __repr__(self) -> str:
        return f"TelegramChannel(chat_id={self.chat_id})"

    def _url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self._token}/{method}"

    def send(self, text: str) -> None:
        r = self.session.post(self._url("sendMessage"), json={"chat_id": self.chat_id, "text": text}, timeout=15)
        if r.status_code != 200:
            raise ApprovalError(f"telegram send failed: HTTP {r.status_code}")

    def poll(self) -> list[tuple[str, str, str]]:
        params: dict[str, Any] = {"timeout": 20}
        if self._offset is not None:
            params["offset"] = self._offset
        r = self.session.get(self._url("getUpdates"), params=params, timeout=30)
        if r.status_code != 200:
            return []
        out = []
        for u in r.json().get("result", []):
            self._offset = u["update_id"] + 1
            msg = u.get("message") or {}
            sender = str((msg.get("from") or {}).get("id", ""))
            if sender != self.user_id:  # only you can approve
                continue
            parsed = parse_reply(msg.get("text", ""))
            if parsed:
                out.append((parsed[0], parsed[1], f"telegram:{sender}"))
        return out

    def wait(self, exts: list[str], deadline: datetime) -> dict[str, tuple[str, str]]:
        pending, decided = set(exts), {}
        while pending and utcnow() < deadline:
            for decision, ext, by in self.poll():
                if ext in pending:
                    decided[ext] = (decision, by)
                    pending.discard(ext)
            if pending:
                self.sleep(2)
        return decided


def make_channel(name: str, secret_getter: Callable[[str], str | None] | None = None) -> Channel:
    if name == "cli":
        return CliChannel()
    if name == "telegram":
        get = secret_getter or (lambda k: os.environ.get(k))
        token, chat, user = get("TA_TELEGRAM_BOT_TOKEN"), get("TA_TELEGRAM_CHAT_ID"), get("TA_TELEGRAM_USER_ID")
        if not (token and chat and user):
            raise ApprovalError("telegram needs TA_TELEGRAM_BOT_TOKEN, TA_TELEGRAM_CHAT_ID, TA_TELEGRAM_USER_ID")
        return TelegramChannel(token, chat, user)
    raise ApprovalError(f"approval channel {name!r} is not available (use cli or telegram)")
