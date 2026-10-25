"""Live dashboard served from the box itself. Binds to loopback or a Tailscale address only (check_host): reach it with SSM port
forwarding or Tailscale, never an open security-group port. Read-only: it cannot place or change anything."""
from __future__ import annotations

import base64
import hmac
import ipaddress
import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import journal
from .config import load_config
from .halt import is_halted

WEB = Path(__file__).parent / "web" / "index.html"
SECRET_KEYS = ("token", "secret", "password", "key")


def _all_events(root: Path) -> list[dict]:
    out = []
    for f in sorted((root / "journal").glob("*.jsonl")):
        out += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    return out


def _scrub(e: dict) -> dict:
    """Never let anything secret-looking reach the page."""
    return {k: v for k, v in e.items() if not any(s in k.lower() for s in SECRET_KEYS)}


def snapshot(root: Path = journal.ROOT, cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    events = _all_events(root)
    closes = [e for e in events if e["event"] == "close_filled"]
    pnls = [e.get("pnl", 0.0) for e in closes]
    acct_path = root / "state" / "paper_account.json"
    acct = json.loads(acct_path.read_text()) if acct_path.exists() else None
    pos = journal.load_positions(root)
    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "env": "paper",
        "halted": is_halted(root),
        "account": None if acct is None else {
            "start": acct["start_cash"], "cash": acct["cash"], "reserved": acct["reserved"],
            "available": round(acct["cash"] - acct["reserved"], 2)},
        "positions": pos,
        "stats": {"closed": len(pnls), "wins": sum(p > 0 for p in pnls),
                  "total_pnl": round(sum(pnls), 2),
                  "graduation_target": cfg["approval"]["graduation"]["min_closed_live_condors"]},
        "limits": {"max_collateral": cfg["sizing"]["max_collateral_usd"],
                   "min_credit": cfg["sizing"]["min_total_credit_usd"],
                   "max_open": cfg["portfolio"]["max_open_condors"],
                   "max_per_sector": cfg["portfolio"]["max_per_sector"],
                   "daily_loss_halt": cfg["portfolio"]["daily_loss_halt_usd"]},
        "events": [_scrub(e) for e in events[-60:]][::-1],
    }


def check_host(host: str) -> str:
    """Only loopback or a private-network address may be served: 127.0.0.0/8, ::1, or the
    Tailscale range 100.64.0.0/10. Anything public (including 0.0.0.0) is refused."""
    ip = ipaddress.ip_address(host)
    if ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10"):
        return host
    raise ValueError(f"refusing to serve on {host}: use 127.0.0.1 or a Tailscale 100.x address")


def authorized(header: str | None, password: str | None) -> bool:
    """Optional HTTP Basic password (TA_DASH_PASSWORD) on top of the private network."""
    if not password:
        return True
    if not header or not header.startswith("Basic "):
        return False
    try:
        given = base64.b64decode(header[6:]).decode().split(":", 1)[1]
    except Exception:
        return False
    return hmac.compare_digest(given.encode(), password.encode())


class Handler(BaseHTTPRequestHandler):
    root = journal.ROOT

    def do_GET(self):                                   # read-only: GET only
        if not authorized(self.headers.get("Authorization"), os.environ.get("TA_DASH_PASSWORD")):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Condor Desk"')
            self.end_headers()
            return
        if self.path.split("?")[0] == "/api/data":
            body, ctype = json.dumps(snapshot(self.root)).encode(), "application/json"
        elif self.path.split("?")[0] in ("/", "/index.html"):
            body, ctype = WEB.read_bytes(), "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def serve(port: int = 8787, root: Path = journal.ROOT, host: str = "127.0.0.1") -> None:
    Handler.root = root
    srv = ThreadingHTTPServer((check_host(host), port), Handler)
    print(f"dashboard on http://{host}:{port} (private network only)")
    srv.serve_forever()
