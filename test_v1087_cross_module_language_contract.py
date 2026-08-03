# -*- coding: utf-8 -*-
"""V1087 cross-module and language regression contracts."""
from types import SimpleNamespace

from evidence_arbitration_v1083 import _cross_module_gate, _decisive_action, _entry_plan


def _forecast(symbol, last, low, high, t1, market="TW"):
    return SimpleNamespace(
        ticker=SimpleNamespace(market=market, symbol=symbol, resolved_symbol=symbol),
        final_t1=t1,
        decision_card={"現價": last, "最低": low, "最高": high, "防守": low},
    )


def _abc(a, b, c):
    return {"a": a, "b": b, "c": c}


def _chip(stance=-1, strength=80):
    return [{"category": "chip", "label": "法人籌碼", "stance_value": stance,
             "stance": "偏空" if stance < 0 else "偏多", "strength": strength}]


def test_6770_negative_t1_and_defensive_abc_removes_fake_buy_prices():
    forecast = _forecast("6770", 55.60, 53.90, 56.65, 54.40)
    entry = {"state": "WAIT_VWAP_PULLBACK", "operative_price": 55.60, "vwap": 55.42}
    top = _chip(-1, 82)
    gate = _cross_module_gate(forecast, entry, _abc(6, 49, 45), top)
    plan = _entry_plan(forecast, entry, gate)
    action = _decisive_action(entry, plan, {"score": 62}, top, gate, forecast)

    assert gate["entry_qualified"] is False
    assert gate["t1_return_pct"] < 0
    assert plan["actionable"] is False
    assert plan["entry_state_code"] == "NO_ENTRY"
    assert action["code"] == "HOLD"
    assert "暫不買" in action["label"]
    assert "T1" in action["reason"]


def test_2308_price_weakness_plus_negative_cross_modules_means_reduce():
    forecast = _forecast("2308", 1598, 1560, 1640, 1575)
    entry = {"state": "WAIT_VWAP_RECLAIM", "operative_price": 1598, "vwap": 1606}
    top = _chip(-1, 86)
    gate = _cross_module_gate(forecast, entry, _abc(18, 50, 32), top)
    plan = _entry_plan(forecast, entry, gate)
    action = _decisive_action(entry, plan, {"score": 40}, top, gate, forecast)

    assert gate["entry_qualified"] is False
    assert action["code"] == "REDUCE"
    assert "減碼" in action["label"]
    assert "不低接" in action["instruction"]


def test_2454_limit_up_is_no_chase_not_permanent_block():
    forecast = _forecast("2454", 3910, 3780, 3910, 3930)
    entry = {"state": "OVERHEATED_NO_CHASE", "operative_price": 3910, "vwap": 3867}
    top = _chip(-1, 76)
    gate = _cross_module_gate(forecast, entry, _abc(17, 72, 11), top)
    plan = _entry_plan(forecast, entry, gate)
    action = _decisive_action(entry, plan, {"score": 88}, top, gate, forecast)

    assert gate["code"] == "RECHECK_NEXT_SESSION"
    assert plan["actionable"] is False
    assert action["code"] == "HOLD"
    assert "不追" in action["label"]
    assert "下一Session" in action["instruction"] or "回測" in action["instruction"]


def test_positive_t1_abc_and_trigger_can_reach_buy_for_tw_or_us():
    for market, symbol in (("TW", "2330"), ("US", "NVDA")):
        forecast = _forecast(symbol, 100, 97, 101, 103, market)
        entry = {"state": "BUY_TODAY_CONFIRM", "operative_price": 100, "vwap": 99}
        top = _chip(1, 85)
        gate = _cross_module_gate(forecast, entry, _abc(58, 27, 15), top)
        plan = _entry_plan(forecast, entry, gate)
        action = _decisive_action(entry, plan, {"score": 78}, top, gate, forecast)

        assert gate["allow_immediate_buy"] is True
        assert plan["entry_state_code"] == "TRIGGERED"
        assert action["code"] == "BUY"
        assert action["language_schema"] == "V1087_EVIDENCE_LANGUAGE"
        assert "買進" in action["label"]
