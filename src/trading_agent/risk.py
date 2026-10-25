"""Hard limits (risk.md). Runs before and after the broker dry-run."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .structure import Condor


@dataclass(frozen=True)
class PortfolioState:
    open_tickers: tuple[str, ...] = ()
    open_sectors: tuple[str, ...] = ()       # one entry per open condor
    orders_today: int = 0
    cash: float = 0.0
    realized_pnl_today: float = 0.0
    halted: bool = False                     # state/HALT or TA_HALT=1


@dataclass(frozen=True)
class RiskResult:
    ok: bool
    violations: tuple[str, ...]


def check_pre(c: Condor, ticker: str, sector: str, short_put_delta: float,
              short_call_delta: float | None, contracts: int, st: PortfolioState,
              cfg: dict) -> RiskResult:
    p, sz, stc = cfg["portfolio"], cfg["sizing"], cfg["structure"]
    v: list[str] = []
    if st.halted:
        v.append("kill switch active: no new opens")
    if st.realized_pnl_today <= -p["daily_loss_halt_usd"]:
        v.append("daily loss limit reached")
    if len(st.open_tickers) >= p["max_open_condors"]:
        v.append("max open condors reached")
    if st.open_tickers.count(ticker) >= p["max_per_ticker"]:
        v.append("ticker already has an open condor")
    if st.open_sectors.count(sector) >= p["max_per_sector"]:
        v.append("sector limit reached")
    if contracts > p["contracts_per_condor"]:
        v.append("too many contracts")
    if st.orders_today >= p["max_orders_per_day"]:
        v.append("daily order limit reached")
    if c.collateral > sz["max_collateral_usd"]:
        v.append("collateral over cap")
    if c.max_profit < sz["min_total_credit_usd"]:
        v.append("credit under minimum")
    if abs(short_put_delta) > stc["max_short_delta"]:
        v.append("short put delta too high")
    if short_call_delta is not None and abs(short_call_delta) > stc["max_short_delta"]:
        v.append("short call delta too high")
    if stc["call_wing_le_put_wing"] and c.call_wing > c.put_wing:
        v.append("call wing wider than put wing")
    return RiskResult(not v, tuple(v))


def check_post_dry_run(c: Condor, bp_effect: float, st: PortfolioState,
                       cfg: dict, warnings: tuple[str, ...] = ()) -> RiskResult:
    """bp_effect: buying power the broker says the order will use (positive number)."""
    v: list[str] = []
    cap = cfg["sizing"]["max_collateral_usd"]
    if warnings:
        v.append(f"dry-run warnings: {', '.join(warnings)}")
    if bp_effect > c.collateral + 0.01:
        v.append("broker buying-power effect exceeds computed collateral")
    if bp_effect > cap:
        v.append("broker buying-power effect over collateral cap")
    if st.cash - bp_effect < cfg["account"]["cash_buffer_usd"]:
        v.append("cash buffer would fall below minimum")
    if c.max_profit < cfg["sizing"]["min_total_credit_usd"]:
        v.append("credit under minimum")
    return RiskResult(not v, tuple(v))


def in_blackout(minutes_since_open: int, minutes_to_close: int, cfg: dict) -> bool:
    n = cfg["pricing"]["avoid_first_last_minutes"]
    return minutes_since_open < n or minutes_to_close < n
