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
