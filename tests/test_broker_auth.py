from typing import Any

import pytest

from trading_agent.auth import AuthError, Credentials, Secret, TokenManager
from trading_agent.broker import (
    Approval, BrokerError, SubmitAmbiguous, TastytradeBroker, parse_dry_run, submit_idempotent,
)
from trading_agent.models import OrderStatus

ORDER = {"order-type": "Limit", "external-identifier": "ic-XYZ-20261127-deadbeef", "price": "1.60",
         "price-effect": "Credit", "legs": []}
APPROVAL = Approval("ic-XYZ-20261127-deadbeef", "test", "now")


class FakeBroker:
    env = "test"

    def __init__(self, submit_exc=None, found=None):
        self.submit_calls = 0
        self.lookups = 0
        self.submit_exc = submit_exc
        self.found = found

    def submit(self, order, *, approval):
        self.submit_calls += 1
        if self.submit_exc:
            raise self.submit_exc
        return OrderStatus("1", "Live", order["external-identifier"])

    def find_by_external_id(self, ext):
        self.lookups += 1
        return self.found


def test_submit_ok_once():
    b = FakeBroker()
    assert submit_idempotent(b, ORDER, APPROVAL, sleep=lambda s: None).status == "Live"
    assert b.submit_calls == 1


def test_ambiguous_submit_adopts_found_order():
    found = OrderStatus("9", "Live", ORDER["external-identifier"])
    b = FakeBroker(SubmitAmbiguous("timeout"), found)
    assert submit_idempotent(b, ORDER, APPROVAL, sleep=lambda s: None).order_id == "9"
    assert b.submit_calls == 1


def test_ambiguous_submit_not_found_is_unknown_and_never_resent():
    sleeps = []
    b = FakeBroker(SubmitAmbiguous("HTTP 502"), None)
    st = submit_idempotent(b, ORDER, APPROVAL, sleep=sleeps.append)
    assert st.status == "UNKNOWN"
    assert b.submit_calls == 1 and b.lookups == 2 and sleeps == [5.0]


def test_parse_dry_run_success_and_error():
    ok = parse_dry_run(200, {"data": {
        "buying-power-effect": {"change-in-buying-power": "340.0", "change-in-buying-power-effect": "Debit",
                                "new-buying-power": "4660.0"},
        "fee-calculation": {"total-fees": "4.12"}, "warnings": []}})
    assert ok.ok and ok.buying_power_effect_usd == 340.0 and ok.cash_after_usd == 4660.0 and ok.fees_usd == 4.12
    bad = parse_dry_run(422, {"error": {"code": "validation_error", "errors": [
        {"code": "invalid_price", "message": "price too far"}]}})
    assert not bad.ok and bad.errors == ["invalid_price: price too far"]


class Resp:
    def __init__(self, code: int, body: dict[str, Any]):
        self.status_code = code
        self._b = body
        self.content = b"x"

    def json(self):
        return self._b


class Sess:
    def __init__(self, responses):
        self.responses = list(responses)
        self.posts = []

    def post(self, url, **kw):
        self.posts.append((url, kw))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def creds(saved=None):
    return Credentials(Secret("cid"), Secret("csecret"), Secret("rt-1"), Secret("5WX12345"),
                       save_refresh_token=(saved.append if saved is not None else None))


def test_secret_never_prints():
    s = Secret("hunter2")
    assert "hunter2" not in repr(s) and "hunter2" not in str(s) and "hunter2" not in repr(creds())


def test_token_refresh_and_rotation():
    saved: list[str] = []
    sess = Sess([Resp(200, {"access_token": "at-1", "expires_in": 900, "refresh_token": "rt-2"})])
    tm = TokenManager("sandbox", creds(saved), session=sess, clock=lambda: 1000.0)
    assert tm.access_token() == "at-1"
    assert saved == ["rt-2"]
    assert sess.posts[0][0] == "https://api.cert.tastyworks.com/oauth/token"
    assert tm.access_token() == "at-1" and len(sess.posts) == 1  # cached


def test_token_failure_fails_closed():
    tm = TokenManager("sandbox", creds(), session=Sess([Resp(401, {})]), clock=lambda: 0.0)
    with pytest.raises(AuthError) as e:
        tm.access_token()
    assert "csecret" not in str(e.value) and "rt-1" not in str(e.value)


def test_no_api_for_paper_env():
    with pytest.raises(AuthError):
        TokenManager("paper", creds())


def test_broker_submit_requires_matching_approval():
    tm = TokenManager("sandbox", creds(), session=Sess([]), clock=lambda: 0.0)
    b = TastytradeBroker("sandbox", tm, session=Sess([]))
    with pytest.raises(BrokerError):
        b.submit(ORDER, approval=Approval("ic-OTHER-20261127-00000000", "x", "y"))
    with pytest.raises(BrokerError):
        b.submit({**ORDER, "order-type": "Market"}, approval=APPROVAL)
