import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from trading_agent import dashboard, journal
from trading_agent.paper import PaperBroker


def test_snapshot_empty(cfg, tmp_path):
    s = dashboard.snapshot(tmp_path, cfg)
    assert s["positions"] == [] and s["account"] is None and s["stats"]["closed"] == 0 and not s["halted"]


def test_snapshot_reflects_state_and_pnl(cfg, tmp_path):
    PaperBroker(root=tmp_path)
    journal.save_positions([{"ticker": "X", "ext": "e"}], tmp_path)
    journal.append_event("close_filled", "X", "e", root=tmp_path, pnl=60.0)
    journal.append_event("close_filled", "Y", "f", root=tmp_path, pnl=-20.0)
    s = dashboard.snapshot(tmp_path, cfg)
    assert s["account"]["available"] == 5000 and len(s["positions"]) == 1
    assert s["stats"] == {"closed": 2, "wins": 1, "total_pnl": 40.0, "graduation_target": 30}
    assert s["events"][0]["event"] == "close_filled"


def test_secret_looking_fields_never_leave(cfg, tmp_path):
    journal.append_event("x", "T", None, root=tmp_path, refresh_token="abc", api_key="k", note="ok")
    ev = dashboard.snapshot(tmp_path, cfg)["events"][0]
    assert "refresh_token" not in ev and "api_key" not in ev and ev["note"] == "ok"


def test_server_is_loopback_get_only_and_serves_data(cfg, tmp_path, monkeypatch):
    dashboard.Handler.root = tmp_path
    srv = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert srv.server_address[0] == "127.0.0.1"
        assert json.load(urllib.request.urlopen(base + "/api/data"))["env"] == "paper"
        assert b"Condor Desk" in urllib.request.urlopen(base + "/").read()
        for method in ("POST", "PUT", "DELETE"):
            req = urllib.request.Request(base + "/api/data", method=method, data=b"x")
            try:
                urllib.request.urlopen(req)
                raise AssertionError("write method accepted")
            except urllib.error.HTTPError as e:
                assert e.code == 501
        try:
            urllib.request.urlopen(base + "/../../etc/passwd")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.shutdown()


import base64

import pytest


@pytest.mark.parametrize("h", ["0.0.0.0", "8.8.8.8", "10.0.0.5", "172.31.4.4", "192.168.1.2", "::"])
def test_public_or_lan_hosts_refused(h):
    with pytest.raises(ValueError):
        dashboard.check_host(h)


@pytest.mark.parametrize("h", ["127.0.0.1", "::1", "100.64.0.1", "100.101.102.103"])
def test_loopback_and_tailscale_allowed(h):
    assert dashboard.check_host(h) == h


def test_basic_password():
    ok = "Basic " + base64.b64encode(b"me:s3cret").decode()
    bad = "Basic " + base64.b64encode(b"me:nope").decode()
    assert dashboard.authorized(None, None)                 # no password configured
    assert dashboard.authorized(ok, "s3cret")
    assert not dashboard.authorized(bad, "s3cret")
    assert not dashboard.authorized(None, "s3cret")
    assert not dashboard.authorized("Basic !!!", "s3cret")
    assert not dashboard.authorized("Bearer x", "s3cret")


def test_server_demands_password_when_set(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("TA_DASH_PASSWORD", "s3cret")
    dashboard.Handler.root = tmp_path
    srv = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/api/data"
    try:
        try:
            urllib.request.urlopen(url)
            raise AssertionError("served without password")
        except urllib.error.HTTPError as e:
            assert e.code == 401
        req = urllib.request.Request(url, headers={"Authorization": "Basic " + base64.b64encode(b"u:s3cret").decode()})
        assert urllib.request.urlopen(req).status == 200
    finally:
        srv.shutdown()
