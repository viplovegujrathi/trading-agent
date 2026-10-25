"""Market data: underlying history, quotes, option chains, IV rank, events.

tastytrade supplies chains, quotes and market metrics. Daily history for the
50-day average, 52-week high and RSI comes from a HistoryProvider (CSV files by
default, data/history/<TICKER>.csv), because tastytrade only serves candles
over DXLink streaming. Every field that cannot be sourced is left as None, and
the screen treats None as a fail.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol

from .broker import TastytradeBroker, _f
from .config import StructureCfg
from .indicators import high_52w, rsi, sma
from .models import Chain, OptionQuote, Snapshot
from .structure import choose_expiry
from .tradingcal import utcnow

DIVIDEND_PROJECTION_DAYS = 91  # quarterly payers: project the next ex-date if none announced
STRIKE_RANGE = 0.25  # fetch strikes within +-25% of spot


@dataclass(frozen=True)
class Bar:
    day: date
    high: float
    low: float
    close: float


class HistoryProvider(Protocol):
    def bars(self, ticker: str) -> list[Bar]: ...


class CsvHistory:
    """data/history/<TICKER>.csv with columns date,high,low,close (oldest first)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def bars(self, ticker: str) -> list[Bar]:
        p = self.root / f"{ticker.upper()}.csv"
        if not p.exists():
            return []
        with p.open() as fh:
            rows = list(csv.DictReader(fh))
        bars = [Bar(date.fromisoformat(r["date"]), float(r["high"]), float(r["low"]), float(r["close"])) for r in rows]
        return sorted(bars, key=lambda b: b.day)


class MarketData(Protocol):
    env: str

    def snapshot(self, ticker: str, sector: str | None, today: date) -> Snapshot: ...
    def chain(self, ticker: str, today: date, cfg: StructureCfg) -> Chain: ...
    def underlying_quote(self, ticker: str) -> dict[str, float | None]: ...
    def option_quotes(self, symbols: list[str]) -> dict[str, tuple[float | None, float | None]]: ...


def snapshot_from_bars(ticker: str, sector: str | None, bars: list[Bar], price: float | None,
                       day_high: float | None, metrics: dict[str, Any], today: date,
                       price_source: str = "live") -> Snapshot:
    """Pure: build a Snapshot from history, a live price and parsed metrics."""
    closes = [b.close for b in bars if b.day < today]
    highs = [b.high for b in bars if b.day < today]
    if price is not None:
        closes = closes + [price]
        highs = highs + [max(price, day_high or price)]
    return Snapshot(
        ticker=ticker,
        sector=sector,
        price=price,
        sma50=sma(closes, 50),
        high_52w=high_52w(highs),
        rsi14=rsi(closes[-200:], 14),
        iv_rank=metrics.get("iv_rank"),
        earnings_date=metrics.get("earnings_date"),
        earnings_known=metrics.get("earnings_known", False),
        ex_dividend_date=metrics.get("ex_dividend_date"),
        dividend_known=metrics.get("dividend_known", False),
        as_of=utcnow(),
        price_source=price_source,  # type: ignore[arg-type]
    )


def _d(x: Any) -> date | None:
    if not x:
        return None
    try:
        return date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


def parse_metrics(item: dict[str, Any], today: date) -> dict[str, Any]:
    """Normalize a tastytrade market-metrics item. Unknown stays unknown."""
    ivr = _f(item.get("implied-volatility-index-rank") or item.get("tw-implied-volatility-index-rank"))
    if ivr is not None and ivr <= 1:
        ivr *= 100
    earnings = item.get("earnings") or {}
    e_date = _d(earnings.get("expected-report-date"))
    out: dict[str, Any] = {
        "iv_rank": round(ivr, 1) if ivr is not None else None,
        "iv": _f(item.get("implied-volatility-index")),
        "earnings_date": e_date if e_date and e_date >= today else None,
        "earnings_known": e_date is not None and e_date >= today,
    }
    next_ex = _d(item.get("dividend-next-date"))
    last_ex = _d(item.get("dividend-ex-date"))
    yld = _f(item.get("dividend-yield"))
    if next_ex and next_ex >= today:
        out.update(ex_dividend_date=next_ex, dividend_known=True)
    elif last_ex:
        projected = last_ex
        while projected < today:
            projected += timedelta(days=DIVIDEND_PROJECTION_DAYS)
        out.update(ex_dividend_date=projected, dividend_known=True)
        out["dividend_projected"] = True
    elif yld is not None and yld == 0:
        out.update(ex_dividend_date=None, dividend_known=True)
    else:
        out.update(ex_dividend_date=None, dividend_known=False)
    return out


class TastytradeMarketData:
    def __init__(self, broker: TastytradeBroker, history: HistoryProvider) -> None:
        self.broker = broker
        self.history = history
        self.env = broker.env
        self._metrics: dict[str, dict[str, Any]] = {}

    def _metrics_for(self, ticker: str, today: date) -> dict[str, Any]:
        if ticker not in self._metrics:
            items = self.broker.market_metrics([ticker])
            self._metrics[ticker] = parse_metrics(items[0], today) if items else {}
        return self._metrics[ticker]

    def underlying_quote(self, ticker: str) -> dict[str, float | None]:
        items = self.broker.market_data([ticker], [])
        if not items:
            return {"price": None, "day_high": None, "day_low": None}
        i = items[0]
        price = _f(i.get("last")) or _f(i.get("mark")) or _f(i.get("mid"))
        return {"price": price, "day_high": _f(i.get("day-high-price")), "day_low": _f(i.get("day-low-price"))}

    def snapshot(self, ticker: str, sector: str | None, today: date) -> Snapshot:
        q = self.underlying_quote(ticker)
        return snapshot_from_bars(ticker, sector, self.history.bars(ticker), q["price"], q["day_high"],
                                  self._metrics_for(ticker, today), today)

    def option_quotes(self, symbols: list[str]) -> dict[str, tuple[float | None, float | None]]:
        return {i["symbol"]: (_f(i.get("bid")), _f(i.get("ask"))) for i in self.broker.market_data([], symbols)}

    def chain(self, ticker: str, today: date, cfg: StructureCfg) -> Chain:
        nested = self.broker.nested_chain(ticker)
        by_date = {date.fromisoformat(e["expiration-date"]): e for e in nested if e.get("expiration-date")}
        expiry = choose_expiry(list(by_date), today, cfg)
        if expiry is None:
            return Chain(ticker, {})
        spot = self.underlying_quote(ticker)["price"]
        if spot is None:
            return Chain(ticker, {})
        meta: dict[str, tuple[float, str]] = {}
        for s in by_date[expiry].get("strikes", []):
            k = float(s["strike-price"])
            if abs(k / spot - 1) > STRIKE_RANGE:
                continue
            meta[s["put"]] = (k, "P")
            meta[s["call"]] = (k, "C")
        fallback_iv = self._metrics_for(ticker, today).get("iv")
        strikes: dict[float, dict[str, OptionQuote]] = {}
        for i in self.broker.market_data([], list(meta)):
            sym = i.get("symbol")
            if sym not in meta:
                continue
            k, right = meta[sym]
            delta = _f(i.get("delta"))
            strikes.setdefault(k, {})[right] = OptionQuote(
                symbol=sym, underlying=ticker, expiry=expiry, strike=k, right=right,  # type: ignore[arg-type]
                bid=_f(i.get("bid")), ask=_f(i.get("ask")), delta=delta,
                open_interest=int(_f(i.get("open-interest"))) if _f(i.get("open-interest")) is not None else None,
                iv=_f(i.get("volatility")) or fallback_iv,
                source="live", delta_source="live" if delta is not None else "modeled",
            )
        return Chain(ticker, {expiry: strikes})
