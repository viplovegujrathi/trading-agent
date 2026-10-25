from datetime import date

import pytest

from conftest import TODAY, make_chain, snap

from trading_agent.structure import build_proposal, choose_expiry, size

EXP = date(2026, 11, 27)  # 32 DTE from TODAY


def test_worked_example_from_strategy_md():
    s = size(5, 5, 1.00, 0.60)
    assert s["total_credit"] == 1.6
    assert s["collateral_usd"] == 340
    assert s["max_profit_usd"] == 160
    assert size(5, 0, 1.00, 0)["collateral_usd"] == 400  # put spread alone


def test_choose_expiry_prefers_target_within_window(cfg):
    exps = [date(2026, 11, 20), date(2026, 11, 27), date(2026, 12, 4), date(2026, 12, 18), date(2027, 1, 15)]
    assert choose_expiry(exps, TODAY, cfg.structure) == date(2026, 11, 27)  # 32 DTE ~ target 33
    assert choose_expiry([date(2026, 11, 20)], TODAY, cfg.structure) is None  # 25 DTE
    assert choose_expiry([date(2026, 11, 25)], TODAY, cfg.structure) is None  # Wednesday


def base_chain(call_bid_ask=(1.00, 1.10), long_call=(0.35, 0.45)):
    puts = {
        97.5: (2.9, 3.1, -0.40), 95.0: (2.0, 2.2, -0.30), 92.5: (1.45, 1.55, -0.22),
        90.0: (1.20, 1.30, -0.19), 87.5: (0.80, 0.90, -0.14), 85.0: (0.45, 0.55, -0.10),
        82.5: (0.30, 0.40, -0.07), 80.0: (0.20, 0.30, -0.05),
    }
    calls = {
        105.0: (1.9, 2.1, 0.30), 107.5: (1.4, 1.6, 0.24), 110.0: (call_bid_ask[0], call_bid_ask[1], 0.18),
        112.5: (long_call[0], long_call[1], 0.12), 115.0: (0.15, 0.25, 0.08), 117.5: (0.05, 0.10, 0.04),
    }
    return make_chain(EXP, puts, calls)


def test_builds_condor_with_rules(cfg):
    r = build_proposal(snap(), base_chain(), TODAY, cfg)
    p = r.proposal
    assert p is not None, r.reasons
    assert p.kind == "iron_condor"
    assert p.short_put == 90.0            # highest strike >=3% OTM with delta <= 0.20
    assert p.short_call == 110.0          # >= max(50dma 105, price*1.05) and delta <= 0.20
    assert p.call_wing <= p.put_wing
    assert [l.action for l in p.legs] == ["Buy to Open", "Sell to Open", "Sell to Open", "Buy to Open"]
    assert p.max_profit_usd >= 100 and p.collateral_usd <= 1000
    assert p.collateral_usd == pytest.approx(max(p.put_wing, p.call_wing) * 100 - p.total_credit * 100)
    assert p.breakeven_low == pytest.approx(p.short_put - p.total_credit)


def test_short_call_respects_50dma(cfg):
    r = build_proposal(snap(sma50=108.0), base_chain(), TODAY, cfg)
    assert r.proposal is None or r.proposal.short_call >= 108.0


def test_call_side_under_min_credit_falls_back_to_put_spread(cfg):
    chain = base_chain(call_bid_ask=(0.20, 0.24), long_call=(0.12, 0.16))
    r = build_proposal(snap(), chain, TODAY, cfg)
    p = r.proposal
    assert p is not None, r.reasons
    assert p.kind == "put_spread_fallback"
    assert len(p.legs) == 2 and p.call_wing == 0
    assert any("fallback" in f for f in p.flags)


def test_never_moves_short_strike_toward_money(cfg):
    # Put credit too thin everywhere: must skip, not move the short put above 3% OTM / 0.20 delta.
    puts = {97.5: (2.9, 3.1, -0.40), 90.0: (0.30, 0.34, -0.10), 85.0: (0.20, 0.24, -0.06),
            80.0: (0.10, 0.14, -0.03)}
    chain = make_chain(EXP, puts, {})
    r = build_proposal(snap(), chain, TODAY, cfg)
    assert r.proposal is None


def test_illiquid_short_strike_skipped(cfg):
    chain = base_chain()
    illiquid = chain.expirations[EXP][90.0]["P"]
    from dataclasses import replace
    chain.expirations[EXP][90.0]["P"] = replace(illiquid, open_interest=100)
    r = build_proposal(snap(), chain, TODAY, cfg)
    assert r.proposal is None or r.proposal.short_put < 90.0


def test_no_expiry_in_window(cfg):
    chain = make_chain(date(2026, 11, 13), {90.0: (1, 1.1, -0.2)}, {})
    r = build_proposal(snap(), chain, TODAY, cfg)
    assert r.proposal is None and "no standard expiry" in r.reasons[0]
