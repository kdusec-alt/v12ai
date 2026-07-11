# -*- coding: utf-8 -*-
"""TINO V12 Foreign Flow Predictor V2.

Purpose
-------
Foreign-flow radar layer for the existing V9/TV FX-difference model.

Design rules
------------
- The V9/TV FX pressure formula remains the base model.
- This module does not fetch data and does not pretend to be official broker flow.
- Direction is weighted first; amount is only an estimated pressure tier.
- Futures net-short, macro event and VWAP state are calibration factors.
- Main UI output stays human-readable V9 style; engineering details remain trace-only.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

FOREIGN_FLOW_PROFILE_KEY = "__FOREIGN_FLOW_V2__"


def _learning_profile() -> Dict[str, Any]:
    # Optional persisted calibration. Failure must never affect front-stage radar.
    try:
        from memory_store import load_profiles
        p = load_profiles().get(FOREIGN_FLOW_PROFILE_KEY, {})
        return p if isinstance(p, dict) else {}
    except Exception:
        return {}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "None", "nan", "待估"):
            return default
        txt = str(value).replace(",", "").replace("億", "").replace("億內", "")
        return float(txt)
    except Exception:
        return default


def _contains_sell(direction: str) -> bool:
    text = str(direction or "")
    return "賣" in text or "撤退" in text or "高壓" in text or "逃命" in text


def _contains_buy(direction: str) -> bool:
    text = str(direction or "")
    return "買" in text or "回流" in text or "回補" in text


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _level_from_amount(direction: str, amount_billion: float) -> str:
    """Human, non-alarming pressure tier for the main UI.

    Important: Foreign Flow V2 is a pressure estimator, not the official
    broker number.  Avoid emotional words such as 逃命 / 崩盤 / 撤退 on the
    front stage; those words caused overreaction when the amount model hit
    a hard cap.
    """
    if _contains_sell(direction):
        if amount_billion >= 700:
            return "高壓"
        if amount_billion >= 350:
            return "壓力偏高"
        if amount_billion >= 150:
            return "偏賣"
        if amount_billion >= 50:
            return "小賣壓"
        return "中性"
    if _contains_buy(direction):
        if amount_billion >= 700:
            return "買盤強"
        if amount_billion >= 350:
            return "買盤偏強"
        if amount_billion >= 150:
            return "偏買"
        if amount_billion >= 50:
            return "小買盤"
        return "中性"
    return "中性"


def _amount_band(direction: str, score_abs: float, amount0: float, futures: Dict[str, Any], support_score: float) -> Tuple[int, int, int, str]:
    """Return low/high/mid/tier for foreign-flow pressure.

    Direction and pressure tier matter more than fake precision.  The old
    model converted stacked pressure into a single number and often hit
    1200億.  Here we decouple amount from direction and output a calibrated
    band.  Large futures net-short is pressure, but a shrinking net-short
    must reduce the band, not amplify it.
    """
    if direction == "neutral" or score_abs < 15:
        return 0, 80, 40, "中性"

    if score_abs < 30:
        lo, hi = 50, 150
    elif score_abs < 50:
        lo, hi = 120, 300
    elif score_abs < 70:
        lo, hi = 200, 450
    elif score_abs < 85:
        lo, hi = 350, 650
    else:
        lo, hi = 500, 850

    # V9 FX base is a clue, not a target. Compress huge FX impulses.
    if amount0 < 120:
        hi = min(hi, 180)
        lo = min(lo, 80)
    elif amount0 < 300:
        hi = min(hi, 350)
    elif amount0 > 900 and score_abs >= 70 and support_score < -25:
        hi = min(900, hi + 100)

    try:
        net_oi = _as_float(futures.get("net_oi"), 0.0) if isinstance(futures, dict) else 0.0
        delta = _as_float(futures.get("delta"), 0.0) if isinstance(futures, dict) else 0.0
    except Exception:
        net_oi, delta = 0.0, 0.0

    # If foreign futures remain net-short but the net short is shrinking,
    # downgrade one tier.  This matches the intuition: still pressure, but
    # not expanding pressure.
    if direction == "sell" and net_oi < 0 and delta > 500:
        lo, hi = max(50, int(lo * 0.70)), max(120, int(hi * 0.72))
    elif direction == "sell" and net_oi < 0 and delta < -500:
        lo, hi = int(lo * 1.08), int(hi * 1.10)
    elif direction == "buy" and net_oi < 0 and delta > 500:
        lo, hi = int(lo * 1.05), int(hi * 1.08)

    # Final anti-fake-precision cap.  900億以上 requires audited official
    # confirmation; front-stage V2 should not print 1200億 during normal days.
    hi = int(_clamp(hi, 80, 900))
    lo = int(_clamp(lo, 0, max(hi - 50, 0)))
    mid = int(round((lo + hi) / 2.0))
    tier = _level_from_amount("預估大盤外資賣壓" if direction == "sell" else "預估大盤外資買盤" if direction == "buy" else "中性", mid)
    return lo, hi, mid, tier


def _direction_words(direction_score: float) -> Tuple[str, str, str]:
    if direction_score <= -15:
        return "sell", "偏賣", "預估大盤外資賣壓"
    if direction_score >= 15:
        return "buy", "偏買", "預估大盤外資買盤"
    return "neutral", "中性", "預估大盤外資中性"


def _vwap_state_from_context(ctx: Dict[str, Any]) -> str:
    """Return market-wide VWAP state for foreign-flow calibration.

    Foreign Flow V2 is a market pressure model, not a single-stock model.
    Earlier builds read the queried stock's price_snapshot here, so switching
    from 2337 to 6770 could change the *market-wide* foreign-flow estimate.
    Only an explicit market_vwap_state may affect this layer.  Otherwise keep
    VWAP neutral/observational so the value stays identical across tickers.
    """
    explicit = str(ctx.get("market_vwap_state") or ctx.get("taiex_vwap_state") or "")
    if explicit in {"VWAP 上方", "VWAP 下方"}:
        return explicit
    return "VWAP觀察"


def _base_amount(tv: Dict[str, Any]) -> float:
    raw = tv.get("amount_billion")
    if isinstance(raw, str) and raw.endswith("億內"):
        return 45.0
    return abs(_as_float(raw, 0.0))


def predict_foreign_flow_v2(tv_pressure: Dict[str, Any], context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Build Foreign Flow Predictor V2 output from the existing V9 TV pressure.

    Output contains both a structured V2 block and a tv_pressure-compatible copy.
    The compatible copy lets existing V9 UI/features keep working without layout
    changes while the Radar can prefer the V2 display line.
    """
    tv = dict(tv_pressure or {})
    if not tv.get("accepted"):
        return {
            "accepted": False,
            "source": "FOREIGN_FLOW_PREDICTOR_V2_WAIT",
            "reason": str(tv.get("reason") or "匯率/大盤同步中"),
            "tv_pressure": tv,
        }

    ctx = context or {}
    futures = ctx.get("futures", {}) if isinstance(ctx.get("futures", {}), dict) else {}
    macro = ctx.get("macro", {}) if isinstance(ctx.get("macro", {}), dict) else {}
    amount0 = _base_amount(tv)
    if amount0 <= 0:
        return {
            "accepted": False,
            "source": "FOREIGN_FLOW_PREDICTOR_V2_WAIT",
            "reason": "V9匯率模型金額不足，暫不輸出假數字",
            "tv_pressure": tv,
        }

    original_direction = str(tv.get("direction") or "")
    fx_score = 0.0
    if _contains_sell(original_direction):
        fx_score = -_clamp(amount0 / 600.0 * 35.0, 8.0, 35.0)
    elif _contains_buy(original_direction):
        fx_score = _clamp(amount0 / 600.0 * 35.0, 8.0, 35.0)

    reasons: List[str] = []
    if fx_score < 0:
        reasons.append("匯率壓力偏賣")
    elif fx_score > 0:
        reasons.append("匯率壓力偏買")
    else:
        reasons.append("匯率壓力中性")

    futures_score = 0.0
    if futures.get("accepted"):
        net_oi = _as_float(futures.get("net_oi"), 0.0)
        delta = _as_float(futures.get("delta"), 0.0)
        if net_oi <= -70000:
            futures_score -= 24
            reasons.append("期貨淨空高")
        elif net_oi <= -50000:
            futures_score -= 18
            reasons.append("期貨淨空偏高")
        elif net_oi <= -30000:
            futures_score -= 10
            reasons.append("期貨空單仍壓")
        elif net_oi >= -15000:
            futures_score += 8
            reasons.append("期貨空單壓力低")
        if delta < -500:
            futures_score -= 6
            reasons.append("淨空增加")
        elif delta > 500:
            futures_score += 6
            reasons.append("空單回補")
    futures_score = _clamp(futures_score, -30.0, 30.0)

    macro_score = 0.0
    if macro.get("accepted"):
        event_score = _as_float(macro.get("event_score"), 0.0)
        calendar = str(macro.get("calendar") or "")
        strength = str(macro.get("strength") or "")
        macro_score += _clamp(event_score * 20.0, -12.0, 12.0)
        if strength == "高" and any(x in calendar for x in ("NFP", "FOMC", "CPI")):
            # Event week raises pressure, but should not overpower direction.
            macro_score += -5.0 if fx_score < 0 else 3.0 if fx_score > 0 else -2.0
            reasons.append("Macro事件週")
        elif any(x in calendar for x in ("NFP", "FOMC", "CPI")):
            reasons.append("Macro事件觀察")
    macro_score = _clamp(macro_score, -20.0, 20.0)

    vwap_state = _vwap_state_from_context(ctx)
    vwap_score = 0.0
    if vwap_state == "VWAP 下方":
        vwap_score = -12.0
        reasons.append("大盤VWAP下方")
    elif vwap_state == "VWAP 上方":
        vwap_score = 6.0
        reasons.append("大盤VWAP上方")

    direction_score = _clamp(fx_score + futures_score + macro_score + vwap_score, -100.0, 100.0)
    direction, direction_label, direction_text = _direction_words(direction_score)

    # Amount is secondary and must be a band, not a fake broker-like number.
    # Direction score still decides 偏買/偏賣; amount band only describes pressure tier.
    support_score = futures_score + macro_score + vwap_score
    profile = _learning_profile()
    learned_scale = _clamp(_as_float(profile.get("approved_foreign_amount_scale"), 1.0), 0.85, 1.15)
    learned_bias = _clamp(_as_float(profile.get("approved_foreign_direction_bias"), 0.0), -5.0, 5.0)
    if learned_bias:
        direction_score = _clamp(direction_score + learned_bias, -100.0, 100.0)
        direction, direction_label, direction_text = _direction_words(direction_score)

    band_lo, band_hi, amount, level = _amount_band(direction, abs(direction_score), amount0 * learned_scale, futures, support_score)
    alert = level
    if direction == "neutral" and band_hi <= 80:
        amount_out: Any = "80億內"
        amount_range_text = "80億內"
    else:
        amount_out = amount
        amount_range_text = f"{band_lo}～{band_hi}億"

    confidence_base = _as_float(tv.get("confidence"), 72.0)
    confidence = int(_clamp(confidence_base + abs(direction_score) * 0.18, 55.0, 88.0))
    evidence = " / ".join(reasons[:4])

    tv2 = dict(tv)
    tv2.update({
        "direction": direction_text,
        "amount_billion": amount_out,
        "amount_range": amount_range_text,
        "level": level,
        "alert": alert,
        "confidence": confidence,
        "source": "FOREIGN_FLOW_PREDICTOR_V2",
        "official": False,
        "model_role": "intraday_foreign_flow_simulation",
        "reason": f"{tv.get('reason','匯率差公式')}｜V2校正：{evidence}",
    })

    amount_txt = amount_range_text
    tone = "賣壓" if direction == "sell" else "買盤" if direction == "buy" else "中性"
    display = f"外資V2｜{direction_label}｜估{tone} {amount_txt}｜{alert}"

    return {
        "accepted": True,
        "direction": direction,
        "direction_label": direction_label,
        "direction_text": direction_text,
        "amount_billion": amount_out,
        "amount_range": amount_range_text,
        "level": level,
        "alert": alert,
        "pressure_score": round(direction_score, 2),
        "confidence": confidence,
        "fx_score": round(fx_score, 2),
        "futures_score": round(futures_score, 2),
        "macro_score": round(macro_score, 2),
        "vwap_score": round(vwap_score, 2),
        "vwap_state": vwap_state,
        "evidence": evidence,
        "display": display,
        "source": "FOREIGN_FLOW_PREDICTOR_V2",
        "learning_amount_scale": round(learned_scale, 4),
        "learning_direction_bias": round(learned_bias, 4),
        "date": tv.get("date"),
        "official": False,
        "model_role": "intraday_foreign_flow_simulation",
        "tv_pressure": tv2,
    }


def calibrate_foreign_flow_pressure(tv_pressure: Dict[str, Any], context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Backward-compatible API used by earlier V12 builds."""
    v2 = predict_foreign_flow_v2(tv_pressure, context)
    if isinstance(v2, dict) and isinstance(v2.get("tv_pressure"), dict):
        return dict(v2["tv_pressure"])
    return dict(tv_pressure or {})
