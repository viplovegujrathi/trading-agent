"""Hard limits (risk.md). The only place limits are enforced.

Every check returns a RiskDecision listing *all* violations so a rejected ticket
says exactly why. Checks are pure; the kill-switch helpers are the only I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .config import StrategyConfig
from .models import DryRunResult, Leg, Proposal
from .tradingcal import in_trading_window

# Dry-run buying-power effect may exceed the hand-computed collateral by at most
# rounding noise. Anything more means our model of the margin is wrong: reject.
BP_TOLERANCE_USD = 1.00


@dataclass(frozen=True)
class OpenCondor:
    ticker: str
    sector: str | None
    collateral_usd: float


@dataclass(frozen=True)
class PortfolioState:
    open_condors: list[OpenCondor]
    orders_submitted_today: int
    realized_pnl_today_usd: float
    derivative_buying_power_usd: float | None  # from balances; None = unknown


@dataclass
class RiskDecision:
    ok: bool
    stage: str
    violations: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.ok = False
        self.violations.append(msg)


# ---------------------------------------------------------------- structure


def defined_risk_violations(legs: list[Leg], opening: bool) -> list[str]:
    """Every short must be covered by a long of the same right and quantity,
    further out of the money. Rejects naked shorts and strangles."""
    out: list[str] = []
    short_action = "Sell to Open" if opening else "Buy to Close"
    long_action = "Buy to Open" if opening else "Sell to Close"
    if len(legs) not in (2, 4):
        out.append(f"order must have 2 or 4 legs, has {len(legs)}")
    if any(l.action not in (short_action, long_action) for l in legs):
        out.append("legs mix opening and closing actions")
    for right in ("P", "C"):
        side = [l for l in legs if l.right == right]
        if not side:
            continue
        shorts = [l for l in side if l.action == short_action]
        longs = [l for l in side if l.action == long_action]
        if len(shorts) != 1 or len(longs) != 1:
            out.append(f"{right} side must be exactly one short and one long")
            continue
        s, lg = shorts[0], longs[0]
        if s.quantity != lg.quantity:
            out.append(f"{right} side quantities differ")
        if right == "P" and not lg.strike < s.strike:
            out.append("long put must be below short put")
        if right == "C" and not lg.strike > s.strike:
            out.append("long call must be above short call")
    if opening and not any(l.right == "P" for l in legs):
        out.append("opening order must include the put spread")
    return out


# ---------------------------------------------------------------- open path


def check_pre_dry_run(p: Proposal, pf: PortfolioState, cfg: StrategyConfig, today: date, halted: bool) -> RiskDecision:
    d = RiskDecision(ok=True, stage="pre_dry_run")
    if halted:
        d.fail("kill switch on: no new opens")
    for v in defined_risk_violations(p.legs, opening=True):
        d.fail(v)
    if p.collateral_usd > cfg.sizing.max_collateral_usd:
        d.fail(f"collateral ${p.collateral_usd:.2f} > ${cfg.sizing.max_collateral_usd:.0f}")
    if p.max_profit_usd < cfg.sizing.min_total_credit_usd:
        d.fail(f"credit ${p.max_profit_usd:.2f} < ${cfg.sizing.min_total_credit_usd:.0f}")
    if len(pf.open_condors) + 1 > cfg.portfolio.max_open_condors:
        d.fail(f"open condors would be {len(pf.open_condors) + 1} > {cfg.portfolio.max_open_condors}")
    same_ticker = sum(1 for c in pf.open_condors if c.ticker == p.ticker)
    if same_ticker + 1 > cfg.portfolio.max_per_ticker:
        d.fail(f"already {same_ticker} open on {p.ticker}")
    if p.sector is None:
        d.fail("sector unknown")
    else:
        same_sector = sum(1 for c in pf.open_condors if c.sector == p.sector)
        if same_sector + 1 > cfg.portfolio.max_per_sector:
            d.fail(f"already {same_sector} open in {p.sector}")
    if any(l.quantity != cfg.portfolio.contracts_per_condor for l in p.legs) or p.quantity != cfg.portfolio.contracts_per_condor:
        d.fail(f"contracts per condor must be {cfg.portfolio.contracts_per_condor}")
    if p.short_put_delta > cfg.structure.max_short_delta:
        d.fail(f"short put delta {p.short_put_delta} > {cfg.structure.max_short_delta}")
    if p.short_call_delta is not None and p.short_call_delta > cfg.structure.max_short_delta:
        d.fail(f"short call delta {p.short_call_delta} > {cfg.structure.max_short_delta}")
    if p.kind == "iron_condor" and cfg.structure.call_wing_le_put_wing and p.call_wing > p.put_wing:
        d.fail(f"call wing {p.call_wing} wider than put wing {p.put_wing}")
    if cfg.screen.exclude_earnings_before_expiry and p.earnings_date and today <= p.earnings_date <= p.expiry:
        d.fail(f"earnings {p.earnings_date} before expiry {p.expiry}")
    if cfg.screen.exclude_ex_dividend_before_expiry and p.ex_dividend_date and today <= p.ex_dividend_date <= p.expiry:
        d.fail(f"ex-dividend {p.ex_dividend_date} before expiry {p.expiry}")
    lo, hi = cfg.structure.dte_window
    if not lo <= (p.expiry - today).days <= hi:
        d.fail(f"expiry {p.expiry} outside {lo}-{hi} DTE")
    return d


def check_post_dry_run(p: Proposal, dr: DryRunResult, pf: PortfolioState, cfg: StrategyConfig) -> RiskDecision:
    d = RiskDecision(ok=True, stage="post_dry_run")
    if not dr.ok or dr.errors:
        d.fail(f"dry-run rejected: {'; '.join(dr.errors) or 'not ok'}")
    if dr.warnings:
        d.fail(f"dry-run warnings: {'; '.join(dr.warnings)}")
    bp = dr.buying_power_effect_usd
    if bp is None:
        d.fail("dry-run gave no buying-power effect")
    else:
        if bp > cfg.sizing.max_collateral_usd:
            d.fail(f"dry-run BP effect ${bp:.2f} > ${cfg.sizing.max_collateral_usd:.0f}")
        if bp > p.collateral_usd + BP_TOLERANCE_USD:
            d.fail(f"dry-run BP effect ${bp:.2f} > computed collateral ${p.collateral_usd:.2f}")
    if dr.cash_after_usd is None:
        d.fail("dry-run gave no buying power after the trade")
    elif dr.cash_after_usd < cfg.account.cash_buffer_usd:
        d.fail(f"buying power after trade ${dr.cash_after_usd:.2f} < buffer ${cfg.account.cash_buffer_usd:.0f}")
    if p.max_profit_usd < cfg.sizing.min_total_credit_usd:
        d.fail(f"credit ${p.max_profit_usd:.2f} < ${cfg.sizing.min_total_credit_usd:.0f}")
    return d


# ---------------------------------------------------------------- close path


def check_close(legs: list[Leg], position_legs: list[Leg], debit_limit: float) -> RiskDecision:
    """A close is one order that exactly unwinds the open legs, with a debit limit."""
    d = RiskDecision(ok=True, stage="close")
    for v in defined_risk_violations(legs, opening=False):
        d.fail(v)
    want = sorted((l.symbol, l.quantity) for l in position_legs)
    have = sorted((l.symbol, l.quantity) for l in legs)
    if want != have:
        d.fail("closing legs do not match the open position")
    if debit_limit <= 0:
        d.fail("closing order needs a positive net-debit limit")
    return d


# ---------------------------------------------------------------- submit


def check_pre_submit(
    *, kind: str, pf: PortfolioState, cfg: StrategyConfig, now: datetime, halted: bool,
    approved: bool, expired: bool,
) -> RiskDecision:
    d = RiskDecision(ok=True, stage="pre_submit")
    if not approved:
        d.fail("no explicit approval for this ticket")
    if expired:
        d.fail("approval window expired")
    if pf.orders_submitted_today + 1 > cfg.portfolio.max_orders_per_day:
        d.fail(f"orders today would be {pf.orders_submitted_today + 1} > {cfg.portfolio.max_orders_per_day}")
    if kind == "open":
        if halted:
            d.fail("kill switch on: no new opens")
        if not in_trading_window(now, cfg.pricing.avoid_first_last_minutes):
            d.fail("outside trading window (market closed or first/last 15 minutes)")
    elif kind == "close":
        if not in_trading_window(now, 0):
            d.fail("market closed")
    else:
        d.fail(f"unknown order kind {kind!r}")
    return d


def daily_loss_breached(pf: PortfolioState, cfg: StrategyConfig) -> bool:
    return -pf.realized_pnl_today_usd > cfg.portfolio.daily_loss_halt_usd


# ---------------------------------------------------------------- kill switch


def halt_path(state_dir: Path) -> Path:
    return state_dir / "HALT"


def is_halted(state_dir: Path, env_flag: bool) -> bool:
    return env_flag or halt_path(state_dir).exists()


def trip_halt(state_dir: Path, reason: str, ts: str) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    p = halt_path(state_dir)
    with p.open("a") as fh:
        fh.write(f"{ts} {reason}\n")


def clear_halt(state_dir: Path) -> bool:
    p = halt_path(state_dir)
    if p.exists():
        p.unlink()
        return True
    return False
