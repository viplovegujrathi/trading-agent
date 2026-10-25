"""Offline paper mode (TT_ENV=paper): a simulated market and broker.

Lets the whole pipeline (screen -> propose -> dry-run -> approve -> submit ->
monitor -> close) run with no credentials and no network. All prices here are
`modeled` (Black-Scholes on a fixture spot and IV), never `live`.

Fixture: data/paper/market.json. Edit a name's `spot` to move the market.
Broker state: state/paper_broker.json.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .broker import Approval, BrokerError
from .config import StructureCfg
from .indicators import bs_delta, bs_price
from .journal import atomic_write_json, read_json
from .marketdata import Bar, parse_metrics, snapshot_from_bars
from .models import Chain, DryRunResult, OrderStatus, OptionQuote, Snapshot
from .orders import occ_symbol, parse_occ
from .structure import choose_expiry
from .tradingcal import is_standard_expiry, is_trading_day, utcnow

OPEN_FEE_PER_CONTRACT = 1.00
CLOSE_FEE_PER_CONTRACT = 0.10
PUT_SKEW = 1.08  # puts trade richer than calls


def _resolve_relative(obj: Any, today: date) -> Any:
    """Turn fixture dates like '+70d' into ISO dates relative to today."""
    if isinstance(obj, dict):
        return {k: _resolve_relative(v, today) for k, v in obj.items()}
    if isinstance(obj, str) and obj.startswith(("+", "-")) and obj.endswith("d") and obj[1:-1].isdigit():
        return (today + timedelta(days=int(obj[:-1]))).isoformat()
    return obj


def _strike_step(spot: float) -> float:
    return 1.0 if spot < 50 else 2.5 if spot < 200 else 5.0


class PaperMarket:
    env = "paper"

    def __init__(self, fixture: Path, today: date | None = None) -> None:
        self.fixture = fixture
        self.data = json.loads(fixture.read_text())
        self._today = today

    @property
    def today(self) -> date:
        return self._today or utcnow().date()

    def name(self, ticker: str) -> dict[str, Any]:
        n = self.data["names"].get(ticker.upper())
        if n is None:
            raise KeyError(f"{ticker} not in paper fixture")
        return n

    # ---------------------------------------------------------- history

    def bars(self, ticker: str) -> list[Bar]:
        """Synthetic year of daily bars shaped by the fixture's history block."""
        n = self.name(ticker)
        h = n["history"]
        spot = n.get("history_spot", n["spot"])
        peak = spot / (1 - h["pct_off_high"] / 100)
        sma_target = spot * (1 + h["pct_below_50dma"] / 100)
        start_last50 = 2 * sma_target - spot  # linear segment whose mean is sma_target
        days: list[date] = []
        d = self.today
        while len(days) < 260:
            d -= timedelta(days=1)
            if is_trading_day(d):
                days.append(d)
        days.reverse()
        closes: list[float] = []
        rise, fall = 120, len(days) - 120 - 50
        base = peak * 0.85
        for i in range(rise):
            closes.append(base + (peak - base) * i / (rise - 1))
        for i in range(fall):
            closes.append(peak + (start_last50 - peak) * (i + 1) / fall)
        for i in range(50):
            closes.append(start_last50 + (spot - start_last50) * (i + 1) / 51)
        # Small deterministic wiggle so RSI is defined and not 0.
        closes = [c * (1 + (0.004 if i % 3 == 0 else -0.002)) for i, c in enumerate(closes)]
        return [Bar(day, c * 1.005, c * 0.995, c) for day, c in zip(days, closes)]

    # ---------------------------------------------------------- MarketData

    def underlying_quote(self, ticker: str) -> dict[str, float | None]:
        n = self.name(ticker)
        return {"price": n["spot"], "day_high": n.get("day_high", n["spot"]), "day_low": n.get("day_low", n["spot"])}

    def snapshot(self, ticker: str, sector: str | None, today: date) -> Snapshot:
        n = self.name(ticker)
        q = self.underlying_quote(ticker)
        metrics = parse_metrics(_resolve_relative(n.get("metrics", {}), today), today)
        return snapshot_from_bars(ticker, sector or n.get("sector"), self.bars(ticker), q["price"],
                                  q["day_high"], metrics, today, price_source="modeled")

    def _quote(self, root: str, expiry: date, right: str, strike: float) -> OptionQuote:
        n = self.name(root)
        spot, iv = n["spot"], n["iv"] * (PUT_SKEW if right == "P" else 1.0)
        t = max((expiry - self.today).days, 0) / 365
        theo = bs_price(spot, strike, t, iv, right)
        half = max(0.01, theo * 0.03)
        bid = max(0.0, round(theo - half, 2))
        ask = round(max(theo + half, bid + 0.01), 2)
        return OptionQuote(
            symbol=occ_symbol(root, expiry, right, strike), underlying=root, expiry=expiry, strike=strike,
            right=right, bid=bid, ask=ask, delta=round(bs_delta(spot, strike, t, iv, right), 4),  # type: ignore[arg-type]
            open_interest=n.get("open_interest", 1500), iv=iv, source="modeled", delta_source="modeled",
        )

    def expiries(self) -> list[date]:
        out, d = [], self.today
        for _ in range(70):
            d += timedelta(days=1)
            if is_standard_expiry(d):
                out.append(d)
        return out

    def chain(self, ticker: str, today: date, cfg: StructureCfg) -> Chain:
        expiry = choose_expiry(self.expiries(), today, cfg)
        if expiry is None:
            return Chain(ticker, {})
        spot = self.name(ticker)["spot"]
        step = _strike_step(spot)
        k = round((spot * 0.75) / step) * step
        strikes: dict[float, dict[str, OptionQuote]] = {}
        while k <= spot * 1.25:
            k = round(k, 2)
            strikes[k] = {r: self._quote(ticker, expiry, r, k) for r in ("P", "C")}
            k += step
        return Chain(ticker, {expiry: strikes})

    def option_quotes(self, symbols: list[str]) -> dict[str, tuple[float | None, float | None]]:
        out = {}
        for s in symbols:
            root, expiry, right, strike = parse_occ(s)
            q = self._quote(root, expiry, right, strike)
            out[s] = (q.bid, q.ask)
        return out


class PaperBroker:
    env = "paper"

    def __init__(self, market: PaperMarket, state_file: Path, starting_cash: float = 5000.0) -> None:
        self.market = market
        self.path = state_file
        self.state = read_json(state_file, None) or {
            "cash": starting_cash, "held": 0.0, "orders": {}, "positions": {}, "next_id": 1}

    def _save(self) -> None:
        atomic_write_json(self.path, self.state)

    # ---------------------------------------------------------- helpers

    @staticmethod
    def _opening(order: dict[str, Any]) -> bool:
        return all(l["action"].endswith("to Open") for l in order["legs"])

    @staticmethod
    def _collateral(order: dict[str, Any]) -> float:
        widths = {}
        for right in ("P", "C"):
            ks = [parse_occ(l["symbol"])[3] for l in order["legs"] if parse_occ(l["symbol"])[2] == right]
            widths[right] = (max(ks) - min(ks)) if len(ks) == 2 else 0.0
        qty = order["legs"][0]["quantity"]
        return max(widths.values()) * 100 * qty

    def _net_mid(self, order: dict[str, Any]) -> float:
        quotes = self.market.option_quotes([l["symbol"] for l in order["legs"]])
        total = 0.0
        for l in order["legs"]:
            bid, ask = quotes[l["symbol"]]
            mid = ((bid or 0) + (ask or 0)) / 2
            total += mid if l["action"].startswith("Sell") else -mid
        return round(total if order["price-effect"] == "Credit" else -total, 4)

    def _fees(self, order: dict[str, Any]) -> float:
        per = OPEN_FEE_PER_CONTRACT if self._opening(order) else CLOSE_FEE_PER_CONTRACT
        return round(per * sum(l["quantity"] for l in order["legs"]), 2)

    def _bp_effect(self, order: dict[str, Any]) -> float:
        qty = order["legs"][0]["quantity"]
        price = float(order["price"])
        coll = self._collateral(order)
        if self._opening(order):
            return round(coll - price * 100 * qty, 2)
        return round(-(coll - price * 100 * qty), 2)

    # ---------------------------------------------------------- Broker

    def balances(self) -> dict[str, float | None]:
        bp = self.state["cash"] - self.state["held"]
        return {"derivative_buying_power": round(float(bp), 2), "net_liquidating_value": round(float(self.state["cash"]), 2),
                "cash_balance": round(float(self.state["cash"]), 2)}

    def positions(self) -> list[dict[str, Any]]:
        return [{"symbol": s, "instrument_type": "Equity Option", "underlying": parse_occ(s)[0], "quantity": q}
                for s, q in self.state["positions"].items() if q]

    def dry_run(self, order: dict[str, Any]) -> DryRunResult:
        if order.get("order-type") != "Limit":
            return DryRunResult(False, None, None, [], ["order_type_not_allowed"], None)
        bp = self._bp_effect(order)
        after = self.balances()["derivative_buying_power"] - bp  # type: ignore[operator]
        errors = ["insufficient_buying_power"] if after < 0 else []
        return DryRunResult(not errors, bp, self._fees(order), [], errors, round(after, 2))

    def _try_fill(self, oid: str) -> None:
        o = self.state["orders"][oid]
        if o["status"] != "Live":
            return
        order = o["order"]
        mid = self._net_mid(order)
        limit = float(order["price"])
        credit = order["price-effect"] == "Credit"
        if (credit and limit <= mid + 0.005) or (not credit and limit >= mid - 0.005):
            qty = order["legs"][0]["quantity"]
            for l in order["legs"]:
                sign = 1 if l["action"].startswith("Buy") else -1
                self.state["positions"][l["symbol"]] = self.state["positions"].get(l["symbol"], 0) + sign * l["quantity"]
            cash = limit * 100 * qty * (1 if credit else -1) - self._fees(order)
            self.state["cash"] += cash
            coll = self._collateral(order)
            self.state["held"] += coll if self._opening(order) else -coll
            o.update(status="Filled", fill_price=limit, filled_at=utcnow().isoformat(timespec="seconds"))

    def submit(self, order: dict[str, Any], *, approval: Approval) -> OrderStatus:
        if not approval.covers(order):
            raise BrokerError("approval does not cover this order")
        if order.get("order-type") != "Limit":
            raise BrokerError("only limit orders are allowed")
        oid = str(self.state["next_id"])
        self.state["next_id"] += 1
        self.state["orders"][oid] = {"order": order, "status": "Live", "ext": order["external-identifier"],
                                     "created_at": utcnow().isoformat(timespec="seconds")}
        self._try_fill(oid)
        self._save()
        return self._status(oid)

    def _status(self, oid: str) -> OrderStatus:
        o = self.state["orders"][oid]
        return OrderStatus(oid, o["status"], o["ext"], o.get("fill_price"))

    def find_by_external_id(self, ext: str) -> OrderStatus | None:
        for oid, o in self.state["orders"].items():
            if o["ext"] == ext:
                return self._status(oid)
        return None

    def order_status(self, order_id: str, ext: str) -> OrderStatus:
        self._try_fill(order_id)
        self._save()
        return self._status(order_id)

    def replace(self, order_id: str, order: dict[str, Any], *, approval: Approval) -> OrderStatus:
        if not approval.covers(order):
            raise BrokerError("approval does not cover this order")
        o = self.state["orders"][order_id]
        if o["status"] != "Live":
            raise BrokerError(f"cannot replace a {o['status']} order")
        o["order"] = order
        self._try_fill(order_id)
        self._save()
        return self._status(order_id)

    def cancel(self, order_id: str) -> None:
        o = self.state["orders"][order_id]
        if o["status"] == "Live":
            o["status"] = "Cancelled"
            self._save()
