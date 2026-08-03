# -*- coding: utf-8 -*-
"""V1086 executable entry-trigger regression contracts."""
from types import SimpleNamespace

from evidence_arbitration_v1083 import _decisive_action, _entry_plan


def _forecast(*, last, low, high, market="TW", defense=None, attack=None, no_chase=None):
    card = {"現價": last, "最低": low, "最高": high}
    if defense is not None:
        card["防守"] = defense
    if attack is not None:
        card["攻擊"] = attack
    if no_chase is not None:
        card["不追"] = no_chase
    return SimpleNamespace(
        ticker=SimpleNamespace(market=market),
        decision_card=card,
    )


def test_pullback_confirmation_never_reuses_vwap_inside_low_zone():
    forecast = _forecast(last=55.75, low=53.90, high=56.65, defense=53.90)
    entry = {"state": "WAIT_VWAP_PULLBACK", "operative_price": 55.75, "vwap": 55.42}
    plan = _entry_plan(forecast, entry)

    assert plan["actionable"] is True
    assert plan["price_order_valid"] is True
    assert plan["low_entry_zone"]["upper"] < plan["confirmation_price"]
    assert plan["confirmation_price"] >= 55.75
    assert plan["breakout_price"] > 56.65
    assert plan["invalidation_price"] < plan["confirmation_price"]
    assert "買進觸發" in plan["display_line"]
    assert "突破觸發" in plan["display_line"]


def test_overheated_stock_gets_future_buy_triggers_not_permanent_no_chase():
    forecast = _forecast(
        last=3910, low=3780, high=3910, defense=3780, attack=4000, no_chase=3910
    )
    entry = {"state": "OVERHEATED_NO_CHASE", "operative_price": 3910, "vwap": 3867}
    plan = _entry_plan(forecast, entry)
    action = _decisive_action(
        entry, plan, {"score": 88},
        [{"stance_value": 1, "strength": 100}],
    )

    assert plan["confirmation_price"] >= 3910
    assert plan["breakout_price"] > 4000
    assert action["code"] == "HOLD"
    assert action["entry_trigger_attached"] is True
    assert "回測站回" in action["instruction"]
    assert "放量突破" in action["instruction"]


def test_unverifiable_session_remains_hard_block_without_fake_prices():
    forecast = _forecast(last=55.75, low=53.90, high=56.65)
    entry = {"state": "DATA_WAIT", "operative_price": 55.75}
    plan = _entry_plan(forecast, entry)
    action = _decisive_action(entry, plan, {"score": 70}, [])

    assert plan["actionable"] is False
    assert action["code"] == "BLOCK"
    assert action["entry_trigger_attached"] is False
