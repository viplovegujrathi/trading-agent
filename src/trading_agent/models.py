"""Plain data types shared across modules. No I/O here."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Literal

PriceSource = Literal["live", "modeled"]


@dataclass(frozen=True)
class OptionQuote:
    symbol: str  # OCC symbol, tastytrade spacing
    underlying: str
    expiry: date
    strike: float
    right: Literal["P", "C"]
    bid: float | None
    ask: float | None
    delta: float | None  # signed: puts negative
    open_interest: int | None
    iv: float | None = None
    source: PriceSource = "live"
    delta_source: PriceSource = "live"

    @property
    def mid(self) -> float | None:
        if self.bid is None or self.ask is None or self.ask < self.bid:
            return None
        return round((self.bid + self.ask) / 2, 4)

    @property
    def spread_pct_of_mid(self) -> float | None:
        m = self.mid
        if m is None or m <= 0 or self.bid is None or self.ask is None:
            return None
        return (self.ask - self.bid) / m * 100


@dataclass(frozen=True)
class Chain:
    underlying: str
    # expiry -> strike -> {"P": OptionQuote, "C": OptionQuote}
    expirations: dict[date, dict[float, dict[str, OptionQuote]]]

    def quote(self, expiry: date, strike: float, right: str) -> OptionQuote | None:
        return self.expirations.get(expiry, {}).get(strike, {}).get(right)

    def strikes(self, expiry: date) -> list[float]:
        return sorted(self.expirations.get(expiry, {}))


@dataclass(frozen=True)
class Snapshot:
    """Everything the screen needs for one name. None = unknown = fail."""

    ticker: str
    sector: str | None
    price: float | None
    sma50: float | None
    high_52w: float | None
    rsi14: float | None
    iv_rank: float | None  # 0-100
    earnings_date: date | None
    earnings_known: bool  # False = we could not determine it
    ex_dividend_date: date | None
    dividend_known: bool  # True with ex_dividend_date None = no dividend scheduled
    as_of: datetime | None = None
    price_source: PriceSource = "live"


@dataclass(frozen=True)
class Leg:
    symbol: str
    right: Literal["P", "C"]
    strike: float
    action: Literal["Buy to Open", "Sell to Open", "Buy to Close", "Sell to Close"]
    quantity: int
    bid: float | None = None
    ask: float | None = None
    delta: float | None = None

    @property
    def mid(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return round((self.bid + self.ask) / 2, 4)


@dataclass
class Proposal:
    """A built condor (or put-spread fallback), before any broker call."""

    ticker: str
    sector: str | None
    expiry: date
    dte: int
    underlying_price: float
    sma50: float
    legs: list[Leg]  # order: buy put, sell put, sell call, buy call
    put_wing: float
    call_wing: float  # 0 for put-spread fallback
    put_credit: float  # per share
    call_credit: float  # per share
    total_credit: float  # per share
    collateral_usd: float
    max_profit_usd: float
    max_loss_usd: float
    breakeven_low: float
    breakeven_high: float | None
    short_put_delta: float
    short_call_delta: float | None
    quantity: int
    kind: Literal["iron_condor", "put_spread_fallback"]
    price_source: PriceSource
    delta_source: PriceSource
    iv_rank: float | None
    flags: list[str] = field(default_factory=list)
    earnings_date: date | None = None
    ex_dividend_date: date | None = None

    @property
    def short_put(self) -> float:
        return next(l.strike for l in self.legs if l.right == "P" and l.action == "Sell to Open")

    @property
    def short_call(self) -> float | None:
        return next((l.strike for l in self.legs if l.right == "C" and l.action == "Sell to Open"), None)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["expiry"] = self.expiry.isoformat()
        d["earnings_date"] = self.earnings_date.isoformat() if self.earnings_date else None
        d["ex_dividend_date"] = self.ex_dividend_date.isoformat() if self.ex_dividend_date else None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Proposal":
        d = dict(d)
        d["expiry"] = date.fromisoformat(d["expiry"])
        d["earnings_date"] = date.fromisoformat(d["earnings_date"]) if d.get("earnings_date") else None
        d["ex_dividend_date"] = date.fromisoformat(d["ex_dividend_date"]) if d.get("ex_dividend_date") else None
        d["legs"] = [Leg(**l) for l in d["legs"]]
        return cls(**d)


@dataclass(frozen=True)
class DryRunResult:
    ok: bool
    buying_power_effect_usd: float | None  # positive = BP consumed
    fees_usd: float | None
    warnings: list[str]
    errors: list[str]
    cash_after_usd: float | None  # derivative buying power left after the trade
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderStatus:
    order_id: str | None
    status: str  # Received, Routed, Live, Filled, Cancelled, Rejected, Expired, UNKNOWN
    external_identifier: str
    fill_price: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_working(self) -> bool:
        return self.status in {"Received", "Routed", "In Flight", "Live", "Contingent"}

    @property
    def is_filled(self) -> bool:
        return self.status == "Filled"

    @property
    def is_terminal(self) -> bool:
        return self.status in {"Filled", "Cancelled", "Rejected", "Expired", "Removed"}
