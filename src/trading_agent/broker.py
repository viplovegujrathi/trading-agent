"""tastytrade REST client and the idempotent submit wrapper.

Submitting requires an `Approval` object that matches the order's
external-identifier. The only code that creates one is approval.py, after a
human yes, so the submit endpoint cannot be reached around the gates.

Endpoint paths follow the tastytrade developer docs (architecture.md table);
verify each in Phase 0 against the sandbox.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Protocol

import requests

from .auth import TokenManager
from .models import DryRunResult, OrderStatus

LOOKUP_ATTEMPTS = 2
LOOKUP_PAUSE_S = 5.0


class BrokerError(RuntimeError):
    pass


class SubmitAmbiguous(BrokerError):
    """Timeout, connection drop or 5xx after the request may have reached the broker."""


@dataclass(frozen=True)
class Approval:
    external_identifier: str
    approved_by: str
    approved_at: str

    def covers(self, order: dict[str, Any]) -> bool:
        return order.get("external-identifier") == self.external_identifier


class Broker(Protocol):
    env: str

    def balances(self) -> dict[str, float | None]: ...
    def positions(self) -> list[dict[str, Any]]: ...
    def dry_run(self, order: dict[str, Any]) -> DryRunResult: ...
    def submit(self, order: dict[str, Any], *, approval: Approval) -> OrderStatus: ...
    def find_by_external_id(self, ext: str) -> OrderStatus | None: ...
    def order_status(self, order_id: str, ext: str) -> OrderStatus: ...
    def replace(self, order_id: str, order: dict[str, Any], *, approval: Approval) -> OrderStatus: ...
    def cancel(self, order_id: str) -> None: ...


def _f(x: Any) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_dry_run(status_code: int, body: dict[str, Any]) -> DryRunResult:
    if status_code >= 400:
        err = body.get("error", {}) if isinstance(body, dict) else {}
        msgs = [f"{e.get('code', '')}: {e.get('message', '')}".strip(": ") for e in err.get("errors", [])]
        if not msgs:
            msgs = [f"{err.get('code', 'HTTP ' + str(status_code))}: {err.get('message', '')}".strip(": ")]
        return DryRunResult(False, None, None, [], msgs, None, body)
    data = body.get("data", body)
    bpe = data.get("buying-power-effect") or {}
    change = _f(bpe.get("change-in-buying-power"))
    if change is not None and bpe.get("change-in-buying-power-effect") == "Credit":
        change = -change
    fees = data.get("fee-calculation") or {}
    warnings = [f"{w.get('code', '')}: {w.get('message', '')}".strip(": ") for w in data.get("warnings", []) or []]
    return DryRunResult(
        ok=True,
        buying_power_effect_usd=change,
        fees_usd=_f(fees.get("total-fees")),
        warnings=warnings,
        errors=[],
        cash_after_usd=_f(bpe.get("new-buying-power")),
        raw=data,
    )


def parse_order(item: dict[str, Any], ext: str | None = None) -> OrderStatus:
    price = _f(item.get("price"))
    return OrderStatus(
        order_id=str(item["id"]) if item.get("id") is not None else None,
        status=item.get("status", "UNKNOWN"),
        external_identifier=item.get("external-identifier") or ext or "",
        fill_price=price if item.get("status") == "Filled" else None,
        raw=item,
    )


class TastytradeBroker:
    def __init__(self, env: str, tokens: TokenManager, session: requests.Session | None = None) -> None:
        self.env = env
        self.tokens = tokens
        self.session = session or tokens.session

    # ------------------------------------------------------------ plumbing

    @property
    def _acct(self) -> str:
        return f"{self.tokens.base_url}/accounts/{self.tokens.account}"

    def _get(self, url: str, params: Any = None) -> dict[str, Any]:
        r = self.session.get(url, params=params, headers=self.tokens.headers(), timeout=20)
        if r.status_code >= 400:
            path = url.replace(self._acct, "/accounts/<acct>").replace(self.tokens.base_url, "")
            raise BrokerError(f"GET {path} HTTP {r.status_code}")
        return r.json()

    # ------------------------------------------------------------ account

    def balances(self) -> dict[str, float | None]:
        d = self._get(f"{self._acct}/balances").get("data", {})
        return {
            "derivative_buying_power": _f(d.get("derivative-buying-power")),
            "net_liquidating_value": _f(d.get("net-liquidating-value")),
            "cash_balance": _f(d.get("cash-balance")),
        }

    def positions(self) -> list[dict[str, Any]]:
        items = self._get(f"{self._acct}/positions").get("data", {}).get("items", [])
        out = []
        for i in items:
            qty = int(_f(i.get("quantity")) or 0)
            if i.get("quantity-direction") == "Short":
                qty = -qty
            out.append({
                "symbol": i.get("symbol"),
                "instrument_type": i.get("instrument-type"),
                "underlying": i.get("underlying-symbol"),
                "quantity": qty,
            })
        return out

    # ------------------------------------------------------------ orders

    def dry_run(self, order: dict[str, Any]) -> DryRunResult:
        r = self.session.post(f"{self._acct}/orders/dry-run", json=order, headers=self.tokens.headers(), timeout=20)
        try:
            body = r.json()
        except ValueError:
            body = {}
        return parse_dry_run(r.status_code, body)

    def submit(self, order: dict[str, Any], *, approval: Approval) -> OrderStatus:
        if not approval.covers(order):
            raise BrokerError("approval does not cover this order")
        if order.get("order-type") != "Limit":
            raise BrokerError("only limit orders are allowed")
        try:
            r = self.session.post(f"{self._acct}/orders", json=order, headers=self.tokens.headers(), timeout=20)
        except (requests.Timeout, requests.ConnectionError) as e:
            raise SubmitAmbiguous(type(e).__name__) from None
        if r.status_code >= 500:
            raise SubmitAmbiguous(f"HTTP {r.status_code}")
        body = r.json() if r.content else {}
        if r.status_code >= 400:
            err = body.get("error", {})
            return OrderStatus(None, "Rejected", order["external-identifier"],
                               raw={"code": err.get("code"), "message": err.get("message")})
        item = body.get("data", {}).get("order", {})
        return parse_order(item, order["external-identifier"])

    def find_by_external_id(self, ext: str) -> OrderStatus | None:
        for path in ("orders/live", "orders"):
            items = self._get(f"{self._acct}/{path}", params={"per-page": 100} if path == "orders" else None)
            for item in items.get("data", {}).get("items", []):
                if item.get("external-identifier") == ext:
                    return parse_order(item, ext)
        return None

    def order_status(self, order_id: str, ext: str) -> OrderStatus:
        item = self._get(f"{self._acct}/orders/{order_id}").get("data", {})
        return parse_order(item, ext)

    def replace(self, order_id: str, order: dict[str, Any], *, approval: Approval) -> OrderStatus:
        if not approval.covers(order):
            raise BrokerError("approval does not cover this order")
        r = self.session.put(f"{self._acct}/orders/{order_id}", json=order, headers=self.tokens.headers(), timeout=20)
        if r.status_code >= 400:
            raise BrokerError(f"replace HTTP {r.status_code}")
        return parse_order(r.json().get("data", {}), order["external-identifier"])

    def cancel(self, order_id: str) -> None:
        r = self.session.delete(f"{self._acct}/orders/{order_id}", headers=self.tokens.headers(), timeout=20)
        if r.status_code >= 400:
            raise BrokerError(f"cancel HTTP {r.status_code}")

    # ------------------------------------------------------------ market data

    def nested_chain(self, symbol: str) -> list[dict[str, Any]]:
        items = self._get(f"{self.tokens.base_url}/option-chains/{symbol}/nested").get("data", {}).get("items", [])
        return items[0].get("expirations", []) if items else []

    def market_data(self, equities: list[str], options: list[str]) -> list[dict[str, Any]]:
        params = [("equity", s) for s in equities] + [("equity-option", s) for s in options]
        out: list[dict[str, Any]] = []
        for i in range(0, len(params), 90):  # stay under the per-request symbol limit
            out += self._get(f"{self.tokens.base_url}/market-data/by-type", params=params[i:i + 90]).get(
                "data", {}).get("items", [])
        return out

    def market_metrics(self, symbols: list[str]) -> list[dict[str, Any]]:
        return self._get(f"{self.tokens.base_url}/market-metrics",
                         params={"symbols": ",".join(symbols)}).get("data", {}).get("items", [])


def submit_idempotent(broker: Broker, order: dict[str, Any], approval: Approval,
                      sleep: Callable[[float], None] = time.sleep) -> OrderStatus:
    """Submit once. On ambiguity, look the order up; never resend.

    Found -> adopt it. Not found after two lookups 5s apart -> UNKNOWN.
    """
    ext = order["external-identifier"]
    try:
        return broker.submit(order, approval=approval)
    except SubmitAmbiguous:
        pass
    for attempt in range(LOOKUP_ATTEMPTS):
        if attempt:
            sleep(LOOKUP_PAUSE_S)
        try:
            found = broker.find_by_external_id(ext)
        except BrokerError:
            found = None
        if found is not None:
            return found
    return OrderStatus(None, "UNKNOWN", ext)


def chain_expiry_dates(nested: list[dict[str, Any]]) -> list[date]:
    return sorted(date.fromisoformat(e["expiration-date"]) for e in nested if e.get("expiration-date"))
