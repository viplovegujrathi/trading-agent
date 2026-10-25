"""Expiry, strikes, wings and sizing (strategy.md sections 2-4). Pure functions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Quote:
    strike: float
    kind: str                # "P" or "C"
    bid: float
    ask: float
    delta: float
    open_interest: int

    @property
    def mid(self) -> float:
        return round((self.bid + self.ask) / 2, 2)

    @property
    def spread_pct_of_mid(self) -> float:
        return (self.ask - self.bid) / self.mid * 100 if self.mid > 0 else 1e9


@dataclass(frozen=True)
class Condor:
    short_put: float
    long_put: float
    short_call: float | None     # None = put-spread fallback
    long_call: float | None
    put_credit: float
    call_credit: float
    fallback: bool = False

    @property
    def put_wing(self) -> float:
        return self.short_put - self.long_put

    @property
    def call_wing(self) -> float:
        return (self.long_call - self.short_call) if self.short_call is not None else 0.0

    @property
    def total_credit(self) -> float:
        return round(self.put_credit + self.call_credit, 2)

    @property
    def collateral(self) -> float:
        return round(max(self.put_wing, self.call_wing) * 100 - self.total_credit * 100, 2)

    @property
    def max_profit(self) -> float:
        return round(self.total_credit * 100, 2)

    @property
    def breakeven_low(self) -> float:
        return round(self.short_put - self.total_credit, 2)

    @property
    def breakeven_high(self) -> float | None:
        return None if self.short_call is None else round(self.short_call + self.total_credit, 2)


def pick_expiry(listed: list[date], today: date, cfg: dict) -> date | None:
    """Listed Friday expiry in the DTE window, closest to the target. Holiday-shortened
    weeks list a Thursday expiry instead, which is excluded here (not a Friday)."""
    lo, hi = cfg["structure"]["dte_window"]
    target = cfg["structure"]["dte_target"]
    ok = [d for d in listed if d.weekday() == 4 and lo <= (d - today).days <= hi]
    return min(ok, key=lambda d: abs((d - today).days - target)) if ok else None


def close_by_date(expiry: date, market_holidays: set[date]) -> date:
    """Trading day before expiry (strategy.md section 6)."""
    d = expiry - timedelta(days=1)
    while d.weekday() >= 5 or d in market_holidays:
        d -= timedelta(days=1)
    return d


def _liquid(q: Quote, cfg: dict) -> bool:
    sc = cfg["screen"]
    return (q.open_interest >= sc["min_open_interest_short"]
            and q.spread_pct_of_mid <= sc["max_short_leg_spread_pct_of_mid"])


def build_condor(price: float, dma50: float, chain: list[Quote], cfg: dict) -> Condor | None:
    """Narrowest wings that clear the credit minimum within the collateral cap.
    Never moves a short strike toward the money to reach the credit; returns None instead."""
    st = cfg["structure"]
    by = {(q.kind, q.strike): q for q in chain}
    puts = sorted((q for q in chain if q.kind == "P"), key=lambda q: -q.strike)
    calls = sorted((q for q in chain if q.kind == "C"), key=lambda q: q.strike)

    put_max = price * (1 - st["short_put_min_otm_pct"] / 100)
    sp = next((q for q in puts if q.strike <= put_max and abs(q.delta) <= st["max_short_delta"]
               and _liquid(q, cfg)), None)
    if sp is None:
        return None

    call_min = max(dma50 if st["short_call_at_or_above_50dma"] else 0,
                   price * (1 + st["short_call_min_above_price_pct"] / 100))
    sc = next((q for q in calls if q.strike >= call_min and abs(q.delta) <= st["max_short_delta"]
               and _liquid(q, cfg)), None)

    widths = sorted(st["wing_widths_usd"])
    best_fallback: Condor | None = None
    for pw in widths:
        lp = by.get(("P", round(sp.strike - pw, 2)))
        if lp is None:
            continue
        put_credit = round(sp.mid - lp.mid, 2)
        if put_credit <= 0:
            continue
        # put-spread fallback candidate at this width
        fb = Condor(sp.strike, lp.strike, None, None, put_credit, 0.0, fallback=True)
        if best_fallback is None and _fits(fb, cfg):
            best_fallback = fb
        if sc is None:
            continue
        for cw in (w for w in widths if not st["call_wing_le_put_wing"] or w <= pw):
            lc = by.get(("C", round(sc.strike + cw, 2)))
            if lc is None:
                continue
            call_credit = round(sc.mid - lc.mid, 2)
            if call_credit < st["min_call_side_credit"]:
                continue
            c = Condor(sp.strike, lp.strike, sc.strike, lc.strike, put_credit, call_credit)
            if _fits(c, cfg):
                return c
    return best_fallback


def _fits(c: Condor, cfg: dict) -> bool:
    sz = cfg["sizing"]
    return c.collateral <= sz["max_collateral_usd"] and c.max_profit >= sz["min_total_credit_usd"]
