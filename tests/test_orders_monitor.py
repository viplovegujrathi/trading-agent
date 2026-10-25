from datetime import date

import pytest

from conftest import TODAY, proposal

from trading_agent.monitor import detect_assignment, evaluate_exits
from trading_agent.orders import (
    build_open_order, build_order, closing_debit_mid, closing_legs, new_external_identifier, occ_symbol,
    parse_occ, reprice_limit, valid_external_identifier,
)


def test_occ_symbol_roundtrip():
    s = occ_symbol("AAPL", date(2026, 11, 27), "P", 182.5)
    assert s == "AAPL  261127P00182500"
    assert parse_occ(s) == ("AAPL", date(2026, 11, 27), "P", 182.5)


def test_external_identifier_format():
    ext = new_external_identifier("XYZ", date(2026, 11, 27))
    assert ext.startswith("ic-XYZ-20261127-") and valid_external_identifier(ext)
    assert new_external_identifier("XYZ", date(2026, 11, 27), "close").startswith("cl-")


def test_open_order_is_one_four_leg_limit_credit():
    o = build_open_order(proposal(), "ic-XYZ-20261127-deadbeef")
    assert o["order-type"] == "Limit" and o["price-effect"] == "Credit" and o["time-in-force"] == "Day"
    assert o["price"] == "1.60" and len(o["legs"]) == 4
    assert o["external-identifier"] == "ic-XYZ-20261127-deadbeef"


def test_builder_refuses_bad_orders():
    p = proposal()
    with pytest.raises(ValueError):
        build_order(p.legs, 0, "Credit", "x")
    with pytest.raises(ValueError):
        build_order(p.legs[:1], 1.0, "Credit", "x")  # single leg: no legging in
    with pytest.raises(ValueError):
        build_order(p.legs, 1.0, "Market", "x")


def test_reprice_never_below_min_credit():
    assert reprice_limit(1.60, 0.05, 1.00) == 1.55
    assert reprice_limit(1.02, 0.05, 1.00) is None


def test_closing_legs_and_debit():
    p = proposal()
    legs = closing_legs(p.legs)
    assert [l.action for l in legs] == ["Sell to Close", "Buy to Close", "Buy to Close", "Sell to Close"]
    quotes = {p.legs[0].symbol: (0.10, 0.20), p.legs[1].symbol: (0.40, 0.50),
              p.legs[2].symbol: (0.30, 0.40), p.legs[3].symbol: (0.05, 0.15)}
    assert closing_debit_mid(quotes, p.legs) == pytest.approx(0.55)
    assert closing_debit_mid({}, p.legs) is None


def pos(**kw):
    p = proposal()
    base = {"id": "ic-XYZ", "ticker": "XYZ", "expiry": "2026-11-27", "entry_credit": 1.60, "quantity": 1,
            "legs": [{"symbol": l.symbol, "right": l.right, "strike": l.strike, "action": l.action,
                      "quantity": l.quantity} for l in p.legs], "flags": {}}
    base.update(kw)
    return base


def test_take_profit_at_half(cfg):
    assert any("take profit" in t for t in evaluate_exits(pos(), TODAY, 100, 99, 101, 0.80, cfg.exits).triggers)
    assert not evaluate_exits(pos(), TODAY, 100, 99, 101, 0.81, cfg.exits).triggers


def test_stop_loss_at_2x_credit(cfg):
    d = evaluate_exits(pos(), TODAY, 100, 99, 101, 4.80, cfg.exits)
    assert any("stop loss" in t for t in d.triggers)


def test_21_dte(cfg):
    assert any("DTE" in t for t in evaluate_exits(pos(), date(2026, 11, 6), 100, 99, 101, 1.2, cfg.exits).triggers)


def test_touch_intraday_then_recover_still_flagged(cfg):
    d = evaluate_exits(pos(), TODAY, 95, 89.5, 96, 1.2, cfg.exits)  # day low touched short put 90
    assert d.touched_put and any("touched" in t for t in d.triggers)
    later = evaluate_exits(pos(flags={"short_put_touched": True}), TODAY, 97, 96, 98, 1.0, cfg.exits)
    assert later.touched_put and any("touched" in t for t in later.triggers)


def test_day_before_holiday_thursday_expiry(cfg):
    p = pos(expiry="2027-03-25")
    d = evaluate_exits(p, date(2027, 3, 24), 100, 99, 101, 1.2, cfg.exits)
    assert any("before expiry" in t for t in d.triggers)


def test_ex_dividend_closes_call_side(cfg):
    d = evaluate_exits(pos(expiry="2026-12-24"), TODAY, 108.5, 108, 109, 1.5, cfg.exits,
                       ex_dividend_date=date(2026, 11, 10))
    assert d.triggers and d.close_side == "C"


def test_assignment_detection():
    p = pos()
    held = [{"symbol": l["symbol"], "quantity": 1 if l["action"] == "Buy to Open" else -1,
             "instrument_type": "Equity Option"} for l in p["legs"]]
    assert detect_assignment(p, held) == []
    missing_short = [h for h in held if h["symbol"] != p["legs"][1]["symbol"]]
    missing_short.append({"symbol": "XYZ", "quantity": 100, "instrument_type": "Equity"})
    issues = detect_assignment(p, missing_short)
    assert len(issues) == 2
