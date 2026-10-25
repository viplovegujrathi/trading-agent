from datetime import datetime, timedelta, timezone

import pytest

from trading_agent.approval import (
    ApprovalError, TelegramChannel, create_ticket, expire_stale, parse_reply, record_decision, render_ticket,
)
from trading_agent.journal import Journal, StateStore

NOW = datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)
ORDER = {"order-type": "Limit", "price": "1.60", "price-effect": "Credit", "external-identifier": "ic-A",
         "legs": [{"symbol": "XYZ   261127P00090000", "quantity": 1, "action": "Sell to Open"}]}


@pytest.fixture
def env(tmp_path):
    return StateStore(tmp_path / "state"), Journal(tmp_path / "journal", "sandbox")


def ticket(store, journal, ext="ic-A"):
    return create_ticket(store, journal, ext=ext, kind="open", ticker="XYZ", order={**ORDER, "external-identifier": ext},
                         summary={"expiry": "2026-11-27"}, timeout_minutes=15, now=NOW)


def test_parse_reply_is_strict():
    assert parse_reply("YES ic-A") == ("yes", "ic-A")
    assert parse_reply("yes ic-A") == ("yes", "ic-A")
    assert parse_reply("no ic-A") == ("no", "ic-A")
    assert parse_reply("YES") is None
    assert parse_reply("sure, go ahead") is None
    assert parse_reply("YES ic-A ic-B") is None


def test_yes_for_one_ticket_does_not_cover_another(env):
    store, journal = env
    ticket(store, journal, "ic-A")
    ticket(store, journal, "ic-B")
    a = record_decision(store, journal, "ic-A", "yes", "me", NOW)
    assert a is not None and a.covers({"external-identifier": "ic-A"})
    assert not a.covers({"external-identifier": "ic-B"})
    assert store.tickets()["ic-B"]["status"] == "AWAITING_APPROVAL"
    with pytest.raises(ApprovalError):
        record_decision(store, journal, "ic-A", "yes", "me", NOW)  # already used


def test_expires_after_15_minutes(env):
    store, journal = env
    ticket(store, journal)
    with pytest.raises(ApprovalError):
        record_decision(store, journal, "ic-A", "yes", "me", NOW + timedelta(minutes=16))
    assert store.tickets()["ic-A"]["status"] == "EXPIRED"


def test_expire_stale(env):
    store, journal = env
    ticket(store, journal)
    assert expire_stale(store, journal, NOW + timedelta(minutes=5)) == []
    assert expire_stale(store, journal, NOW + timedelta(minutes=20)) == ["ic-A"]


def test_decline(env):
    store, journal = env
    ticket(store, journal)
    assert record_decision(store, journal, "ic-A", "no", "me", NOW) is None
    assert store.tickets()["ic-A"]["status"] == "DECLINED"


def test_render_production_banner(env):
    store, journal = env
    t = ticket(store, journal)
    assert render_ticket(t, "production").startswith("*** PRODUCTION")
    assert "Reply YES ic-A to submit." in render_ticket(t, "sandbox")


class TgResp:
    status_code = 200

    def __init__(self, body):
        self._b = body

    def json(self):
        return self._b


class TgSession:
    def get(self, url, params=None, timeout=None):
        return TgResp({"result": [
            {"update_id": 1, "message": {"from": {"id": 999}, "text": "YES ic-A"}},  # stranger
            {"update_id": 2, "message": {"from": {"id": 42}, "text": "YES ic-A"}},
        ]})


def test_telegram_accepts_only_owner():
    ch = TelegramChannel("tok", "chat", "42", session=TgSession())
    assert ch.poll() == [("yes", "ic-A", "telegram:42")]
    assert "tok" not in repr(ch)


def test_journal_lines_have_required_fields_and_scrub_secrets(env):
    _, journal = env
    line = journal.write("x", ticker="XYZ", external_identifier="ic-A", now=NOW,
                         payload={"refresh_token": "rt", "client_secret": "cs", "ok": 1})
    for k in ("ts", "event", "ticker", "external_identifier", "env"):
        assert k in line
    assert line["ts"] == "2026-10-26T15:00:00+00:00"
    import json
    stored = json.loads((journal.dir / "2026-10-26.jsonl").read_text().splitlines()[0])
    assert stored["payload"] == {"refresh_token": "***", "client_secret": "***", "ok": 1}
