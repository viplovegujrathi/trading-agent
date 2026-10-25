from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from trading_agent.config import StrategyConfig, load_strategy
from trading_agent.models import Chain, Leg, OptionQuote, Proposal, Snapshot
from trading_agent.orders import occ_symbol

TODAY = date(2026, 10, 26)  # a Monday
# 11:00 ET on TODAY, inside the trading window.
IN_WINDOW = datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def cfg() -> StrategyConfig:
    return load_strategy(env="sandbox")


def snap(**kw) -> Snapshot:
    base = dict(
        ticker="XYZ", sector="Health Care", price=100.0, sma50=105.0, high_52w=125.0, rsi14=40.0,
        iv_rank=40.0, earnings_date=date(2027, 1, 20), earnings_known=True, ex_dividend_date=None,
        dividend_known=True,
    )
    base.update(kw)
    return Snapshot(**base)


def q(expiry: date, strike: float, right: str, bid: float, ask: float, delta: float, oi: int = 1000,
      root: str = "XYZ") -> OptionQuote:
    return OptionQuote(occ_symbol(root, expiry, right, strike), root, expiry, strike, right, bid, ask,  # type: ignore[arg-type]
                       delta, oi)


def make_chain(expiry: date, puts: dict[float, tuple[float, float, float]],
               calls: dict[float, tuple[float, float, float]], root: str = "XYZ") -> Chain:
    strikes: dict[float, dict[str, OptionQuote]] = {}
    for k, (b, a, d) in puts.items():
        strikes.setdefault(k, {})["P"] = q(expiry, k, "P", b, a, d, root=root)
    for k, (b, a, d) in calls.items():
        strikes.setdefault(k, {})["C"] = q(expiry, k, "C", b, a, d, root=root)
    return Chain(root, {expiry: strikes})


def proposal(**kw) -> Proposal:
    expiry = kw.pop("expiry", date(2026, 11, 27))
    legs = kw.pop("legs", None) or [
        Leg(occ_symbol("XYZ", expiry, "P", 85), "P", 85, "Buy to Open", 1),
        Leg(occ_symbol("XYZ", expiry, "P", 90), "P", 90, "Sell to Open", 1),
        Leg(occ_symbol("XYZ", expiry, "C", 110), "C", 110, "Sell to Open", 1),
        Leg(occ_symbol("XYZ", expiry, "C", 115), "C", 115, "Buy to Open", 1),
    ]
    base = dict(
        ticker="XYZ", sector="Health Care", expiry=expiry, dte=(expiry - TODAY).days, underlying_price=100.0,
        sma50=105.0, legs=legs, put_wing=5.0, call_wing=5.0, put_credit=1.0, call_credit=0.6,
        total_credit=1.6, collateral_usd=340.0, max_profit_usd=160.0, max_loss_usd=340.0, breakeven_low=88.4,
        breakeven_high=111.6, short_put_delta=0.18, short_call_delta=0.17, quantity=1, kind="iron_condor",
        price_source="live", delta_source="live", iv_rank=40.0,
    )
    base.update(kw)
    return Proposal(**base)
