"""Exit rules on open condors (strategy.md §6) and broker reconciliation. Pure."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .config import ExitsCfg
from .tradingcal import last_close_day

NEAR_MONEY_PCT = 2.0  # short call within 2% of spot counts as "near the money" for dividend risk


@dataclass
class ExitDecision:
    triggers: list[str] = field(default_factory=list)
    close_side: str = "all"  # "all" or "C" (dividend risk closes only the call spread)
    touched_put: bool = False
    touched_call: bool = False
    pnl_per_share: float | None = None

    @property
    def should_close(self) -> bool:
        return bool(self.triggers)


def short_strikes(pos: dict[str, Any]) -> tuple[float | None, float | None]:
    sp = next((l["strike"] for l in pos["legs"] if l["right"] == "P" and l["action"] == "Sell to Open"), None)
    sc = next((l["strike"] for l in pos["legs"] if l["right"] == "C" and l["action"] == "Sell to Open"), None)
    return sp, sc


def evaluate_exits(pos: dict[str, Any], today: date, spot: float | None, day_low: float | None,
                   day_high: float | None, close_debit: float | None, cfg: ExitsCfg,
                   ex_dividend_date: date | None = None) -> ExitDecision:
    d = ExitDecision()
    expiry = date.fromisoformat(pos["expiry"])
    credit = float(pos["entry_credit"])
    sp, sc = short_strikes(pos)
    flags = pos.get("flags", {})

    # Touch flags are sticky: touched intraday then recovered is still touched.
    lows = [x for x in (spot, day_low) if x is not None]
    highs = [x for x in (spot, day_high) if x is not None]
    low = min(lows) if lows else None
    high = max(highs) if highs else None
    d.touched_put = bool(flags.get("short_put_touched")) or (sp is not None and low is not None and low <= sp)
    d.touched_call = bool(flags.get("short_call_touched")) or (sc is not None and high is not None and high >= sc)

    if close_debit is not None:
        d.pnl_per_share = round(credit - close_debit, 4)
        if d.pnl_per_share >= credit * cfg.take_profit_pct_of_max / 100:
            d.triggers.append(f"take profit: {d.pnl_per_share:.2f} >= {cfg.take_profit_pct_of_max:.0f}% of {credit:.2f}")
        if -d.pnl_per_share >= credit * cfg.stop_loss_multiple_of_credit:
            d.triggers.append(f"stop loss: loss {-d.pnl_per_share:.2f} >= {cfg.stop_loss_multiple_of_credit}x credit")
    if (expiry - today).days <= cfg.close_at_dte:
        d.triggers.append(f"{(expiry - today).days} DTE <= {cfg.close_at_dte}")
    if cfg.close_on_short_strike_touch and (d.touched_put or d.touched_call):
        d.triggers.append("short strike touched: " + ("put" if d.touched_put else "call"))
    if cfg.close_day_before_expiry and today >= last_close_day(expiry):
        d.triggers.append(f"last trading day before expiry {expiry}")

    if ex_dividend_date and sc is not None and spot is not None and today <= ex_dividend_date <= expiry:
        if spot >= sc * (1 - NEAR_MONEY_PCT / 100):
            d.triggers.append(f"ex-dividend {ex_dividend_date} with short call {sc} near the money")
            if len(d.triggers) == 1:
                d.close_side = "C"
    return d


def detect_assignment(pos: dict[str, Any], broker_positions: list[dict[str, Any]]) -> list[str]:
    """Signs of early assignment: a short leg vanished or stock appeared in the underlying."""
    held = {p["symbol"]: p["quantity"] for p in broker_positions}
    issues = []
    for l in pos["legs"]:
        if l["action"] == "Sell to Open" and held.get(l["symbol"], 0) > -l["quantity"]:
            issues.append(f"short leg {l['symbol']} missing at broker")
    for p in broker_positions:
        if p.get("instrument_type") == "Equity" and p.get("symbol") == pos["ticker"] and p["quantity"]:
            issues.append(f"{p['quantity']} shares of {pos['ticker']} in account")
    return issues
