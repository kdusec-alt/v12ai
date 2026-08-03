# -*- coding: utf-8 -*-
"""Regression contract for V1085.1 decisive action calibration."""
from evidence_arbitration_v1083 import _decisive_action


def _row(stance, strength):
    return {"stance_value": stance, "strength": strength}


def _run(state, score, rows, current=100.0, invalid=95.0):
    return _decisive_action(
        {"state": state},
        {
            "current_price": current,
            "invalidation_price": invalid,
            "confirmation_price": 101.0,
        },
        {"score": score},
        rows,
    )


def test_normal_evidence_conflict_is_hold_not_block():
    # Representative of the reported case: price bullish, ABC and chips bearish.
    result = _run(
        "WAIT_VWAP_PULLBACK",
        67,
        [_row(1, 90), _row(-1, 88), _row(-1, 80)],
    )
    assert result["code"] == "HOLD"
    assert result["label"] == "空手不追｜持股續抱"


def test_limit_liquidity_is_no_chase_not_block():
    result = _run(
        "LIMIT_LIQUIDITY_WAIT",
        60,
        [_row(1, 92), _row(-1, 80), _row(-1, 78)],
    )
    assert result["code"] == "HOLD"


def test_overheated_trend_is_no_chase_not_reduce():
    result = _run("OVERHEATED_NO_CHASE", 75, [_row(1, 90)])
    assert result["code"] == "HOLD"


def test_block_reserved_for_unverifiable_data():
    result = _run("DATA_WAIT", 0, [])
    assert result["code"] == "BLOCK"
    assert result["hard_risk_veto"] is True


def test_strong_bearish_confluence_can_still_block_new_entry():
    result = _run(
        "WAIT_VWAP_PULLBACK",
        38,
        [_row(1, 45), _row(-1, 90), _row(-1, 80)],
    )
    assert result["code"] == "BLOCK"


def test_price_below_invalidation_is_sell():
    result = _run("WAIT_VWAP_PULLBACK", 70, [_row(1, 90)], current=94, invalid=95)
    assert result["code"] == "SELL"
