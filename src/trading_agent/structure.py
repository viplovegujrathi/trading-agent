"""Expiry, strikes, wings and sizing (strategy.md §2-4). Pure functions."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from .config import ScreenCfg, StrategyConfig, StructureCfg
from .indicators import bs_delta
from .models import Chain, Leg, OptionQuote, Proposal, Snapshot
from .tradingcal import is_standard_expiry


@dataclass
class StructureResult:
    proposal: Proposal | None
    reasons: list[str] = field(default_factory=list)


def floor_cent(x: float) -> float:
    return math.floor(round(x * 100, 6)) / 100


def choose_expiry(expiries: list[date], today: date, cfg: StructureCfg) -> date | None:
    lo, hi = cfg.dte_window
    eligible = [e for e in expiries if lo <= (e - today).days <= hi and is_standard_expiry(e)]
    if not eligible:
        return None
    return min(eligible, key=lambda e: (abs((e - today).days - cfg.dte_target), e))


def effective_delta(q: OptionQuote, spot: float, today: date) -> tuple[float | None, str]:
    if q.delta is not None:
        return abs(q.delta), q.delta_source
    if q.iv:
        t = max((q.expiry - today).days, 0) / 365
        return abs(bs_delta(spot, q.strike, t, q.iv, q.right)), "modeled"
    return None, "live"


def liquid(q: OptionQuote, cfg: ScreenCfg) -> str | None:
    """None if the short leg is liquid enough, else the reason."""
    if q.open_interest is None:
        return f"{q.symbol}: open interest unknown"
    if q.open_interest < cfg.min_open_interest_short:
        return f"{q.symbol}: OI {q.open_interest} < {cfg.min_open_interest_short}"
    pct = q.spread_pct_of_mid
    if pct is None:
        return f"{q.symbol}: no two-sided quote"
    if pct > cfg.max_short_leg_spread_pct_of_mid:
        return f"{q.symbol}: bid-ask {pct:.1f}% of mid > {cfg.max_short_leg_spread_pct_of_mid}"
    return None


def _short_candidates(
    chain: Chain, expiry: date, right: str, spot: float, bound: float, today: date, cfg: StrategyConfig
) -> tuple[list[tuple[OptionQuote, float, str]], list[str]]:
    """Short strikes that satisfy distance, delta and liquidity, nearest-the-money first."""
    strikes = chain.strikes(expiry)
    if right == "P":
        ordered = sorted((k for k in strikes if k <= bound), reverse=True)
    else:
        ordered = sorted(k for k in strikes if k >= bound)
    out, notes = [], []
    for k in ordered:
        q = chain.quote(expiry, k, right)
        if q is None:
            continue
        d, src = effective_delta(q, spot, today)
        if d is None:
            notes.append(f"{q.symbol}: delta unknown")
            continue
        if d > cfg.structure.max_short_delta:
            continue
        why = liquid(q, cfg.screen)
        if why:
            notes.append(why)
            continue
        out.append((q, d, src))
    return out, notes


def size(put_wing: float, call_wing: float, put_credit: float, call_credit: float) -> dict[str, float]:
    """strategy.md §4. Wings in $ per share, credits per share; results in $ per contract."""
    total = round(put_credit + call_credit, 4)
    collateral = max(put_wing, call_wing) * 100 - total * 100
    return {
        "total_credit": total,
        "collateral_usd": round(collateral, 2),
        "max_profit_usd": round(total * 100, 2),
        "max_loss_usd": round(collateral, 2),
    }


def _side_credit(short: OptionQuote, long: OptionQuote) -> float | None:
    if short.mid is None or long.mid is None:
        return None
    return round(short.mid - long.mid, 4)


def build_proposal(snap: Snapshot, chain: Chain, today: date, cfg: StrategyConfig) -> StructureResult:
    res = StructureResult(proposal=None)
    if snap.price is None or snap.sma50 is None:
        res.reasons.append("missing price or 50dma")
        return res
    spot, sc = snap.price, cfg.structure
    expiry = choose_expiry(list(chain.expirations), today, sc)
    if expiry is None:
        res.reasons.append(f"no standard expiry {sc.dte_window[0]}-{sc.dte_window[1]} DTE in chain")
        return res

    put_bound = spot * (1 - sc.short_put_min_otm_pct / 100)
    call_bound = spot * (1 + sc.short_call_min_above_price_pct / 100)
    if sc.short_call_at_or_above_50dma:
        call_bound = max(call_bound, snap.sma50)

    puts, put_notes = _short_candidates(chain, expiry, "P", spot, put_bound, today, cfg)
    calls, call_notes = _short_candidates(chain, expiry, "C", spot, call_bound, today, cfg)
    if not puts:
        res.reasons.append("no short put passes distance/delta/liquidity")
        res.reasons.extend(put_notes[:3])
        return res

    qty = cfg.portfolio.contracts_per_condor
    widths = sorted(sc.wing_widths_usd)
    # Only the nearest-the-money short put that passes is used: moving the short
    # strike further out is allowed, but we never move it toward the money.
    sp, sp_delta, sp_src = puts[0]
    sc_q = calls[0] if calls else None

    for pw in widths:
        lp = chain.quote(expiry, round(sp.strike - pw, 2), "P")
        if lp is None:
            continue
        put_credit = _side_credit(sp, lp)
        if put_credit is None or put_credit <= 0:
            continue

        # Narrowest eligible call wing (call wing <= put wing) with enough credit.
        best_call = None
        if sc_q is not None:
            cq, c_delta, c_src = sc_q
            for cw in widths:
                if sc.call_wing_le_put_wing and cw > pw:
                    break
                lc = chain.quote(expiry, round(cq.strike + cw, 2), "C")
                if lc is None:
                    continue
                call_credit = _side_credit(cq, lc)
                if call_credit is None or call_credit < sc.min_call_side_credit:
                    continue
                best_call = (cq, lc, cw, call_credit, c_delta, c_src)
                break

        if best_call is not None:
            cq, lc, cw, call_credit, c_delta, c_src = best_call
            total = floor_cent(put_credit + call_credit)
            put_credit_r = round(put_credit, 2)
            call_credit_r = round(total - put_credit_r, 2)
            p = _make(snap, expiry, today, sp, lp, cq, lc, pw, cw, put_credit_r, call_credit_r,
                      sp_delta, c_delta, {sp_src, c_src}, qty, "iron_condor")
        else:
            total = floor_cent(put_credit)
            p = _make(snap, expiry, today, sp, lp, None, None, pw, 0.0, total, 0.0,
                      sp_delta, None, {sp_src}, qty, "put_spread_fallback")
            p.flags.append("call side under min credit or unavailable: put-spread fallback")

        if p.collateral_usd > cfg.sizing.max_collateral_usd:
            res.reasons.append(f"{pw}-wide: collateral ${p.collateral_usd:.0f} > cap")
            continue
        if p.max_profit_usd < cfg.sizing.min_total_credit_usd:
            res.reasons.append(f"{pw}-wide: credit ${p.max_profit_usd:.0f} < ${cfg.sizing.min_total_credit_usd:.0f}")
            continue
        res.proposal = p
        return res

    if not res.reasons:
        res.reasons.append("no wing width has quotes on both legs")
    return res


def _make(snap: Snapshot, expiry: date, today: date, sp: OptionQuote, lp: OptionQuote,
          cq: OptionQuote | None, lc: OptionQuote | None, pw: float, cw: float,
          put_credit: float, call_credit: float, sp_delta: float, c_delta: float | None,
          delta_srcs: set[str], qty: int, kind: str) -> Proposal:
    legs = [
        Leg(lp.symbol, "P", lp.strike, "Buy to Open", qty, lp.bid, lp.ask, lp.delta),
        Leg(sp.symbol, "P", sp.strike, "Sell to Open", qty, sp.bid, sp.ask, sp.delta),
    ]
    quotes = [lp, sp]
    if cq is not None and lc is not None:
        legs += [
            Leg(cq.symbol, "C", cq.strike, "Sell to Open", qty, cq.bid, cq.ask, cq.delta),
            Leg(lc.symbol, "C", lc.strike, "Buy to Open", qty, lc.bid, lc.ask, lc.delta),
        ]
        quotes += [cq, lc]
    s = size(pw, cw, put_credit, call_credit)
    total = s["total_credit"]
    price_src = "modeled" if any(q.source == "modeled" for q in quotes) or snap.price_source == "modeled" else "live"
    assert snap.price is not None and snap.sma50 is not None
    return Proposal(
        ticker=snap.ticker,
        sector=snap.sector,
        expiry=expiry,
        dte=(expiry - today).days,
        underlying_price=snap.price,
        sma50=round(snap.sma50, 2),
        legs=legs,
        put_wing=pw,
        call_wing=cw,
        put_credit=put_credit,
        call_credit=call_credit,
        total_credit=total,
        collateral_usd=s["collateral_usd"] * qty,
        max_profit_usd=s["max_profit_usd"] * qty,
        max_loss_usd=s["max_loss_usd"] * qty,
        breakeven_low=round(sp.strike - total, 2),
        breakeven_high=round(cq.strike + total, 2) if cq is not None else None,
        short_put_delta=round(sp_delta, 3),
        short_call_delta=round(c_delta, 3) if c_delta is not None else None,
        quantity=qty,
        kind=kind,  # type: ignore[arg-type]
        price_source=price_src,  # type: ignore[arg-type]
        delta_source="modeled" if "modeled" in delta_srcs else "live",  # type: ignore[arg-type]
        iv_rank=snap.iv_rank,
        earnings_date=snap.earnings_date,
        ex_dividend_date=snap.ex_dividend_date,
    )
