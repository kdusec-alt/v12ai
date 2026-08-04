# -*- coding: utf-8 -*-
"""V1087 cross-module and language regression contracts."""
from types import SimpleNamespace

from evidence_arbitration_v1083 import _abc as _parse_abc
from evidence_arbitration_v1083 import _cross_module_gate, _decisive_action, _entry_plan
from evidence_reasoning_v1082 import build_evidence_reasoning as build_v1082


def _forecast(symbol, last, low, high, t1, market="TW", asset_type="stock", radar=None):
    return SimpleNamespace(
        ticker=SimpleNamespace(
            market=market, symbol=symbol, resolved_symbol=symbol, asset_type=asset_type,
        ),
        final_t1=t1,
        decision_card={"現價": last, "最低": low, "最高": high, "防守": low},
        radar=dict(radar or {}),
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
    assert "低檔監控" in action["label"]
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


def test_selling_expansion_above_invalidation_reduces_without_false_breach_claim():
    forecast = _forecast("2308", 1592.5, 1560, 1640, 1570)
    entry = {"state": "SELLING_EXPANSION_BLOCK", "operative_price": 1592.5, "vwap": 1606}
    plan = {
        "current_price": 1592.5, "session_low": 1560, "invalidation_price": 1412,
        "confirmation_price": None, "breakout_price": None, "trigger_status": "NO_ENTRY",
        "price_order_valid": False, "actionable": False, "entry_state_label": "本日無買進資格",
    }
    action = _decisive_action(entry, plan, {"score": 38}, _chip(-1, 86), {}, forecast)

    assert action["code"] == "REDUCE"
    assert action["situation_code"] == "REDUCE_SELLING_EXPANSION"
    assert action["exit_basis"] == "SELLING_EXPANSION"
    assert action["current_vs_invalidation"] == "ABOVE_OR_EQUAL"
    assert "減碼" in action["label"]
    assert "現價已跌破" not in action["reason"]
    assert "仍未跌破" in action["reason"]
    assert "--" not in action["instruction"]
    assert "VWAP／關鍵均價" in action["instruction"]


def test_failed_breakout_above_invalidation_has_its_own_reason():
    forecast = _forecast("TEST", 105, 101, 110, 104)
    entry = {"state": "FAILED_BREAKOUT_EXIT", "operative_price": 105, "vwap": 106}
    plan = {
        "current_price": 105, "session_low": 101, "invalidation_price": 98,
        "confirmation_price": 108, "breakout_price": None, "trigger_status": "NO_ENTRY",
        "price_order_valid": False, "actionable": False, "entry_state_label": "本日無買進資格",
    }
    action = _decisive_action(entry, plan, {"score": 45}, _chip(-1, 70), {}, forecast)

    assert action["code"] == "REDUCE"
    assert action["situation_code"] == "REDUCE_FAILED_BREAKOUT"
    assert action["exit_basis"] == "FAILED_BREAKOUT"
    assert "突破失敗" in action["label"]
    assert "現價已跌破" not in action["reason"]


def test_current_price_below_invalidation_is_the_only_direct_price_sell():
    forecast = _forecast("TEST", 97, 96, 105, 95)
    entry = {"state": "SELLING_EXPANSION_BLOCK", "operative_price": 97, "vwap": 100}
    plan = {
        "current_price": 97, "session_low": 96, "invalidation_price": 98,
        "confirmation_price": 100, "breakout_price": None, "trigger_status": "NO_ENTRY",
        "price_order_valid": False, "actionable": False, "entry_state_label": "本日無買進資格",
    }
    action = _decisive_action(entry, plan, {"score": 35}, _chip(-1, 90), {}, forecast)

    assert action["code"] == "SELL"
    assert action["situation_code"] == "SELL_PRICE_INVALID"
    assert action["exit_basis"] == "CURRENT_PRICE_INVALIDATION"
    assert action["current_vs_invalidation"] == "BELOW"
    assert "現價已跌破" in action["reason"]


def test_intraday_breach_recovered_is_hold_watch_not_reduce():
    forecast = _forecast("TEST", 101, 97, 104, 102)
    entry = {"state": "SELLING_EXPANSION_BLOCK", "operative_price": 101, "vwap": 100}
    plan = {
        "current_price": 101, "session_low": 97, "invalidation_price": 98,
        "confirmation_price": 102, "breakout_price": None, "trigger_status": "NO_ENTRY",
        "price_order_valid": False, "actionable": False, "entry_state_label": "本日無買進資格",
    }
    action = _decisive_action(entry, plan, {"score": 48}, _chip(-1, 75), {}, forecast)

    assert action["code"] == "HOLD"
    assert action["situation_code"] == "HOLD_RECLAIM_WATCH"
    assert action["exit_basis"] == "INTRADAY_BREACH_RECLAIMED"
    assert action["intraday_breach_recovered"] is True
    assert "現價已跌破" not in action["reason"]
    assert "已收回" in action["reason"]


def test_etf_institutional_flow_uses_actor_totals_not_stock_chip_vocabulary():
    radar = {
        "三大法人": (
            "外資 今日 +29,502張｜3日 +31,000張｜5日 +32,000張｜10日 +39,344張｜連買2天 "
            "投信 今日 +0張｜3日 +500張｜5日 +800張｜10日 +1,141張｜方向觀察 "
            "自營 今日 -13,104張｜3日 -20,000張｜5日 -25,000張｜10日 -36,779張｜連賣2天 "
            "法人日期：2026-07-31｜來源：YahooInstitutional｜法人同步"
        ),
    }
    forecast = _forecast("0052", 59.35, 58.55, 59.80, 59.30, asset_type="etf", radar=radar)
    entry = {"state": "WAIT_VWAP_PULLBACK", "operative_price": 59.35, "vwap_position": "above"}
    result = build_v1082(forecast, entry)
    chip = next(row for row in result["top_drivers"] if row["category"] == "chip")

    assert chip["label"] == "ETF申贖／法人流向"
    assert chip["stance"] == "偏多"
    assert "今日合計 +16,398張" in chip["text"]
    assert "10日合計 +3,706張" in chip["text"]
    assert "籌碼／法人" not in chip["label"]


def test_abc_pullback_dominance_is_neutral_path_not_bearish_risk():
    abc = _parse_abc({"ABC 多空情境": "A突破 16%｜B回測 72%｜C防守 12%"})

    assert abc is not None
    assert abc["path_code"] == "PULLBACK"
    assert abc["label"] == "ABC回測情境"
    assert abc["stance"] == "中性"
    assert abc["strength"] == 72
    assert "回測情境主導" in abc["text"]


def test_0052_etf_keeps_only_pullback_trigger_with_truthful_entry_state():
    forecast = _forecast("0052", 59.35, 58.55, 59.80, 59.30, asset_type="etf")
    entry = {"state": "WAIT_VWAP_PULLBACK", "operative_price": 59.35, "vwap": 59.24}
    abc = _parse_abc({"ABC 多空情境": "A突破 16%｜B回測 72%｜C防守 12%"})
    top = _chip(1, 72)
    top[0]["label"] = "ETF申贖／法人流向"
    gate = _cross_module_gate(forecast, entry, abc, top)
    plan = _entry_plan(forecast, entry, gate)
    action = _decisive_action(entry, plan, {"score": 72}, top, gate, forecast)

    assert gate["code"] == "CONTROLLED_LOW_TRADE"
    assert gate["controlled_low_trade"] is True
    assert gate["trade_level"] == "TRADEABLE"
    assert gate["entry_qualified"] is True
    assert gate["allow_pullback"] is True
    assert gate["allow_breakout"] is False
    assert plan["entry_state_code"] == "WAIT_PULLBACK_RECLAIM"
    assert plan["entry_state_label"] == "等待再次回測後站回"
    assert plan["entry_sequence_verified"] is False
    assert plan["session_touched_entry_zone"] is True
    assert plan["confirmation_price"] is not None
    assert round(plan["confirmation_price"] * 100) % 5 == 0
    assert plan["breakout_price"] is None
    assert "突破資格關閉" in plan["breakout_text"]
    assert "A情境僅" not in plan["breakout_text"]
    assert "ABC突破僅 16%" in plan["breakout_text"]
    assert action["code"] == "HOLD"
    assert "--" not in action["instruction"]
    assert "回測" in action["instruction"]
    assert "僅保留回測型" in action["reason"]


def test_deep_falling_zone_remains_monitor_only_not_fake_low_entry():
    forecast = _forecast("TEST", 55.45, 54.80, 57.20, 54.20)
    entry = {"state": "WAIT_VWAP_RECLAIM", "operative_price": 55.45, "vwap": 55.50}
    gate = _cross_module_gate(forecast, entry, _abc(6, 54, 40), _chip(-1, 82))
    plan = _entry_plan(forecast, entry, gate)

    assert gate["controlled_low_trade"] is False
    assert gate["entry_qualified"] is False
    assert gate["trade_level"] == "LOW_MONITOR"
    assert plan["trade_level_label"] == "低檔監控｜尚未止跌"
    assert plan["actionable"] is False
    action = _decisive_action(entry, plan, {"score": 55}, _chip(-1, 82), gate, forecast)
    assert action["code"] in {"HOLD", "REDUCE"}
    assert action["code"] != "BUY"


def test_near_zero_t1_safe_pullback_keeps_controlled_small_trade_path():
    forecast = _forecast("TEST", 100, 97, 101, 99.60)
    entry = {"state": "WAIT_VWAP_RECLAIM", "operative_price": 100, "vwap": 99.50}
    gate = _cross_module_gate(forecast, entry, _abc(18, 58, 24), _chip(-1, 60))
    plan = _entry_plan(forecast, entry, gate)

    assert gate["code"] == "CONTROLLED_LOW_TRADE"
    assert gate["controlled_low_trade"] is True
    assert gate["allow_pullback"] is True
    assert gate["allow_breakout"] is False
    assert gate["trade_level"] == "TRADEABLE"
    assert plan["trade_level_label"] == "可以交易｜小倉試單"
    assert plan["actionable"] is True


def test_controlled_low_trade_never_bypasses_event_or_strong_chip_veto():
    forecast = _forecast("TEST", 100, 97, 101, 99.60)
    entry = {"state": "WAIT_VWAP_RECLAIM", "operative_price": 100, "vwap": 99.50}
    for top in (
        _chip(-1, 82),
        [{"category": "event", "stance_value": -1, "strength": 85}],
    ):
        gate = _cross_module_gate(forecast, entry, _abc(18, 58, 24), top)
        assert gate["controlled_low_trade"] is False
        assert gate["allow_immediate_buy"] is False


def test_confirmed_controlled_low_entry_uses_small_position_language():
    forecast = _forecast("TEST", 100, 97, 101, 99.60)
    entry = {"state": "BUY_TODAY_CONFIRM", "operative_price": 100, "vwap": 99.50}
    gate = _cross_module_gate(forecast, entry, _abc(18, 58, 24), _chip(1, 60))
    plan = _entry_plan(forecast, entry, gate)
    action = _decisive_action(entry, plan, {"score": 65}, _chip(1, 60), gate, forecast)

    assert gate["controlled_low_trade"] is True
    assert plan["trade_level"] == "LOW_ENTRY_READY"
    assert action["code"] == "BUY"
    assert "可以低接" in action["label"]
    assert "20%～30%" in action["instruction"]

def test_6770_strong_rebound_is_not_labeled_as_not_bottomed():
    forecast = _forecast("6770", 57.20, 53.90, 57.20, 56.37)
    entry = {
        "state": "WAIT_VWAP_PULLBACK",
        "operative_price": 57.20,
        "operative_return_pct": 7.04,
        "vwap": 56.10,
        "vwap_position": "above",
    }
    top = _chip(-1, 82)
    gate = _cross_module_gate(forecast, entry, _abc(6, 54, 40), top)
    plan = _entry_plan(forecast, entry, gate)
    action = _decisive_action(entry, plan, {"score": 55}, top, gate, forecast)

    assert gate["entry_qualified"] is False
    assert gate["rebound_monitor"] is True
    assert gate["trade_level"] == "REBOUND_MONITOR"
    assert plan["trade_level_label"] == "反彈監控｜已止跌反彈，尚未確認轉強"
    assert action["code"] == "HOLD"
    assert "反彈監控" in action["label"]
    assert "尚未止跌" not in action["label"]
    assert "不追" in action["instruction"] or "等待回測" in action["instruction"]
    assert "T1" in action["reason"]


def test_positive_day_below_vwap_does_not_fake_rebound_confirmation():
    forecast = _forecast("TEST", 100, 95, 102, 98)
    entry = {
        "state": "WAIT_VWAP_RECLAIM",
        "operative_price": 100,
        "operative_return_pct": 3.50,
        "vwap": 101,
        "vwap_position": "below",
    }
    top = _chip(-1, 82)
    gate = _cross_module_gate(forecast, entry, _abc(8, 52, 40), top)
    plan = _entry_plan(forecast, entry, gate)

    assert gate["rebound_monitor"] is False
    assert gate["trade_level"] == "LOW_MONITOR"
    assert plan["trade_level_label"] == "低檔監控｜尚未止跌"


def test_deleveraging_recovery_can_reach_first_layer_buy_before_full_trend_confirmation():
    forecast = _forecast("RECOVERY", 100, 96, 102, 98.5)
    entry = {
        "state": "BUY_TODAY_CONFIRM", "operative_price": 100,
        "operative_return_pct": 2.2, "vwap": 99, "vwap_position": "above",
    }
    top = [
        {"category": "chip", "label": "籌碼／法人", "text": "融資減少、健康去槓桿｜法人轉買",
         "source": "Institution/Short Evidence", "stance_value": 1, "strength": 68, "verified": True},
        {"category": "event", "label": "事件／新聞", "text": "財報優於預期",
         "source": "News/Event Truth Guard", "stance_value": 1, "strength": 72, "verified": True},
    ]
    gate = _cross_module_gate(forecast, entry, _abc(22, 58, 20), top)
    plan = _entry_plan(forecast, entry, gate)
    action = _decisive_action(entry, plan, {"score": 72}, top, gate, forecast)

    assert gate["recovery_setup"] is True
    assert gate["trade_level"] == "RECOVERY_SETUP"
    assert plan["entry_state_code"] == "TRIGGERED"
    assert plan["trade_level"] == "RECOVERY_ENTRY_READY"
    assert action["code"] == "BUY"
    assert action["situation_code"] == "BUY_RECOVERY_ENTRY"
    assert "第一層" in action["label"]
    assert "15%～20%" in action["instruction"]


def test_deleveraging_alone_never_becomes_a_buy_signal():
    forecast = _forecast("NO_KNIFE", 100, 94, 102, 96)
    entry = {"state": "BUY_TODAY_CONFIRM", "operative_price": 100,
             "operative_return_pct": 1.2, "vwap": 99, "vwap_position": "above"}
    top = [{"category": "chip", "label": "籌碼／法人", "text": "融資大減、融資清洗",
            "source": "Institution/Short Evidence", "stance_value": -1, "strength": 82, "verified": True}]
    gate = _cross_module_gate(forecast, entry, _abc(10, 50, 40), top)

    assert gate["deleveraging_evidence"] is True
    assert gate["recovery_setup"] is False
    assert gate["entry_qualified"] is False


def test_strong_reclaim_resets_old_intraday_breach_reduce_lock():
    forecast = _forecast("RECLAIM", 106, 97, 106, 104)
    entry = {"state": "WAIT_VWAP_PULLBACK", "operative_price": 106,
             "operative_return_pct": 5.0, "vwap": 102, "vwap_position": "above"}
    gate = _cross_module_gate(forecast, entry, _abc(20, 60, 20), _chip(1, 70))
    plan = {
        "current_price": 106, "session_low": 97, "invalidation_price": 98,
        "confirmation_price": 103, "breakout_price": 107, "trigger_status": "PENDING",
        "price_order_valid": True, "actionable": True, "entry_state_label": "等待回測",
    }
    action = _decisive_action(entry, plan, {"score": 68}, _chip(1, 70), gate, forecast)

    assert gate["rebound_monitor"] is True
    assert action["code"] == "HOLD"
    assert action["situation_code"] != "REDUCE_INTRADAY_BREACH_RECLAIMED"
