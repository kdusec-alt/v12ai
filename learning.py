# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
import hashlib
import re

from config import VERSION
from models import FinalForecast, LearningSuggestion, SignalPacket
from memory_store import (
    AUDIT_LOG,
    PREDICTION_LOG,
    append_jsonl,
    load_profiles,
    read_audit_log,
    read_prediction_log,
    save_profiles,
)
from ticker_resolver import resolve_ticker
try:
    from learning_market_clock import target_trade_date_for_forecast, fetch_actual_daily_snapshot, actual_matches_target
except Exception:
    def target_trade_date_for_forecast(forecast):
        from datetime import timedelta
        day = datetime.now(TW_TZ).date() + timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
        return day.isoformat()
    def fetch_actual_daily_snapshot(ticker):
        return {"actual_valid": False, "price_date": "", "source": "learning_clock_unavailable"}
    def actual_matches_target(actual, target_date):
        return False
try:
    from learning_dna import prediction_dna, contribution_attribution, update_attribution_learning
except Exception:
    def prediction_dna(forecast, direction, card):
        return {
            "schema": "TINO_PREDICTION_DNA_DEGRADED",
            "ticker": getattr(getattr(forecast, "ticker", None), "resolved_symbol", ""),
            "factor_contributions": dict(direction.get("factor_contributions") or {}),
            "family_contributions": dict(direction.get("family_contributions") or {}),
        }
    def contribution_attribution(values, actual_direction, **kwargs):
        return {}
    def update_attribution_learning(current, attribution, **kwargs):
        return dict(current or {})

TW_TZ = ZoneInfo("Asia/Taipei")
NY_TZ = ZoneInfo("America/New_York")
ERROR_TYPES = [
    "FQC overpull", "LCR underweight", "Macro overfit", "BSI missing", "法人日期誤判",
    "T1 High Magnet", "Risk Cascade 漏判", "ETF mode error", "Ticker resolver error",
]

def _now() -> str:
    return datetime.now(TW_TZ).isoformat(timespec="seconds")
def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default
def _first_number(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, (int, float)):
        return _safe_float(value, 0.0)
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    if not match:
        return default
    try:
        return float(match.group(0))
    except Exception:
        return default
def _canonical(raw_symbol: str) -> str:
    try:
        return resolve_ticker(raw_symbol).resolved_symbol
    except Exception:
        return str(raw_symbol or "").strip().upper()
def _prediction_market(row: Dict[str, Any]) -> str:
    """Return TW/US from logged prediction row without touching ticker resolver.
    Auto Audit Time Guard uses this to avoid auditing US rows at the Taiwan
    close window and vice versa.  Keep this helper local and conservative so
    existing manual/admin audit behavior remains unchanged when market_filter
    is not supplied.
    """
    m = str(row.get("market") or "").strip().upper()
    if m in {"TW", "US"}:
        return m
    t = str(row.get("ticker") or "").strip().upper()
    if t.endswith(".TW") or t.endswith(".TWO") or (t[:4].isdigit() and len(t) >= 4):
        return "TW"
    return "US" if t else ""
def _market_matches(row: Dict[str, Any], market_filter: Optional[str] = None) -> bool:
    mf = str(market_filter or "").strip().upper()
    if not mf:
        return True
    return _prediction_market(row) == mf
def _run_date_tw() -> str:
    return datetime.now(TW_TZ).date().isoformat()
def _audit_trade_date(market: Optional[str] = None) -> str:
    """Calendar date of the official session being audited.
    US close occurs on the following Taipei calendar morning, so using the
    Taipei date would permanently skip US T1 rows.
    """
    return (
        datetime.now(NY_TZ).date().isoformat()
        if str(market or "").upper() == "US"
        else _run_date_tw()
    )
def _predictions_targeting(ticker: str, target_date: Optional[str] = None, limit: int = 1000) -> List[Dict[str, Any]]:
    key = _canonical(ticker)
    target = str(target_date or _run_date_tw())
    rows: List[Dict[str, Any]] = []
    for r in read_prediction_log(limit):
        if r.get("ticker") == key and str(r.get("target_trade_date") or "") == target:
            rows.append(r)
    return rows
def _latest_t1_candidate(ticker: str, target_date: Optional[str] = None, limit: int = 1000) -> Optional[Dict[str, Any]]:
    today = str(target_date or _run_date_tw())
    rows = [r for r in _predictions_targeting(ticker, today, limit) if r.get("next_close_est") is not None]
    # Prefer true previous-session predictions; avoid using a same-day rerun to audit itself.
    prev_rows = [r for r in rows if str(r.get("run_date_tw") or "") < today]
    rows = prev_rows or rows
    if not rows:
        return None
    rows.sort(key=lambda x: str(x.get("run_time_tw") or ""))
    return rows[-1]
def _forecast_data_label(forecast: FinalForecast) -> str:
    try:
        return str((forecast.decision_card or {}).get("資料標題", ""))
    except Exception:
        return ""
def _forecast_session_mode(forecast: FinalForecast) -> str:
    label = _forecast_data_label(forecast)
    if "盤中" in label:
        return "intraday"
    if "收盤" in label:
        return "closed"
    if "盤前" in label:
        return "pre_market"
    if "盤後" in label:
        return "after_hours"
    return "unknown"
def _latest_audit_for_prediction(prediction_id: str, target: str = "today") -> Optional[Dict[str, Any]]:
    audit_id = f"{prediction_id}:{target}"
    for row in reversed(read_audit_log(500)):
        if row.get("audit_id") == audit_id:
            return row
    return None
def _same_day_predictions(ticker: str, limit: int = 500, market: Optional[str] = None) -> List[Dict[str, Any]]:
    key = _canonical(ticker)
    audit_date = _audit_trade_date(market)
    rows = []
    for r in read_prediction_log(limit):
        if r.get("ticker") != key:
            continue
        row_market = _prediction_market(r)
        row_date = (
            str(r.get("target_trade_date") or "")
            if row_market == "US"
            else str(r.get("run_date_tw") or "")
        )
        if row_date == audit_date:
            rows.append(r)
    return rows
def prediction_signature(forecast: FinalForecast) -> str:
    # Include model/direction contract so a new model version cannot be silently
    # deduplicated against an older forecast that happened to share T1 prices.
    direction = (forecast.decision_card or {}).get("_direction_engine", {})
    direction = direction if isinstance(direction, dict) else {}
    base = "|".join([
        VERSION,
        _run_date_tw(),
        _forecast_session_mode(forecast),
        target_trade_date_for_forecast(forecast),
        forecast.ticker.resolved_symbol,
        str(forecast.final_t0),
        str(forecast.final_t1),
        str(forecast.final_t1_high),
        str(forecast.final_t1_low),
        str(forecast.confidence),
        str(direction.get("label")),
        str(direction.get("score")),
        str(forecast.reality_anchor),
    ])
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]
def forecast_snapshot(forecast: FinalForecast, macro: str = "neutral", live_data: bool = True) -> Dict[str, Any]:
    card = forecast.decision_card or {}
    direction = card.get("_direction_engine") if isinstance(card.get("_direction_engine"), dict) else {}
    anchor = _safe_float(card.get("現價"), 0.0)
    next_close = _safe_float(forecast.final_t1, 0.0)
    predicted_return_pct = ((next_close - anchor) / anchor * 100.0) if anchor > 0 and next_close > 0 else 0.0
    predicted_direction = str(direction.get("label") or "")
    if predicted_direction not in {"UP", "DOWN", "NEUTRAL"}:
        predicted_direction = "UP" if predicted_return_pct > 0.30 else "DOWN" if predicted_return_pct < -0.30 else "NEUTRAL"
    return {
        "id": prediction_signature(forecast),
        "run_time_tw": _now(),
        "run_date_tw": _run_date_tw(),
        "target_trade_date": target_trade_date_for_forecast(forecast),
        "target_kind": "T1_CLOSE_NEXT_SESSION",
        "session_mode": _forecast_session_mode(forecast),
        "data_label": _forecast_data_label(forecast),
        "model_version": VERSION,
        "ticker": forecast.ticker.resolved_symbol,
        "name": forecast.ticker.name,
        "market": forecast.ticker.market,
        "asset_type": forecast.ticker.asset_type,
        "macro_bias": macro,
        "live_data": bool(live_data),
        "spot_last": anchor,
        "anchor_close": anchor,
        "vwap_state": card.get("VWAP位置"),
        "today_close_est": forecast.final_t0,
        "next_close_est": forecast.final_t1,
        "next_high_est": forecast.final_t1_high,
        "next_low_est": forecast.final_t1_low,
        "predicted_return_pct": round(predicted_return_pct, 4),
        "predicted_direction": predicted_direction,
        "direction_neutral_band_pct": 0.30,
        "direction_score": direction.get("score"),
        "p_up": direction.get("p_up"),
        "p_neutral": direction.get("p_neutral"),
        "p_down": direction.get("p_down"),
        "direction_confidence": direction.get("confidence"),
        "direction_quality": direction.get("quality"),
        "direction_conflict": direction.get("conflict"),
        "direction_regime": direction.get("regime"),
        "direction_family_scores": dict(direction.get("family_scores") or {}),
        "direction_family_weights": dict(direction.get("family_weights") or {}),
        "direction_family_contributions": dict(direction.get("family_contributions") or {}),
        "direction_factor_contributions": dict(direction.get("factor_contributions") or {}),
        "direction_risk_contributions": dict(direction.get("risk_contributions") or {}),
        "direction_confidence_adjustments": dict(direction.get("confidence_adjustments") or {}),
        "direction_confidence_components": dict(direction.get("confidence_components") or {}),
        "direction_learning_calibration": dict(direction.get("learning_calibration") or {}),
        "entry_low_1": _first_number(card.get("低接第一批")),
        "entry_low_2": _first_number(card.get("低接第二批")),
        "turn_level": _first_number(card.get("轉強")),
        "defense_stop": _first_number(card.get("防守")),
        "no_chase_level": _first_number(card.get("不追")),
        "confidence": forecast.confidence,
        "one_liner": forecast.one_liner,
        "tags": forecast.tags,
        "radar": forecast.radar,
        "foreign_flow_v2": dict(card.get("_foreign_flow_v2", {}) or {}),
        "tv_pressure": dict(card.get("_tv_pressure", {}) or {}),
        "truths": [getattr(x, "__dict__", {}) for x in forecast.data_truths],
        "trace": forecast.trace.to_rows() if forecast.trace else [],
        "prediction_dna": prediction_dna(forecast, direction, card),
        "learning_schema": "RC4.5_DNA_V1",
        "audited": False,
    }
def log_prediction(forecast: FinalForecast, macro: str = "neutral", live_data: bool = True) -> Dict[str, Any]:
    """Write one prediction snapshot per signature.
    Streamlit reruns often; this guard prevents the Auto-Learning panel from
    duplicating the same forecast every time the sidebar is opened.
    RC2.4.3 Price Truth Guard:
    stopped/invalid forecasts are deliberately not written into prediction_log,
    because they are data-quality events rather than formal T1 samples.
    """
    if forecast is None or bool(getattr(forecast, "stopped", False)):
        return {
            "skipped": True,
            "reason": getattr(forecast, "stop_reason", "stopped_or_invalid_forecast"),
            "ticker": getattr(getattr(forecast, "ticker", None), "resolved_symbol", ""),
        }
    price_meta = dict((getattr(forecast, "decision_card", {}) or {}).get("_price_meta", {}) or {})
    src = str(price_meta.get("source") or "")
    # Official Sample gate：
    # - decision_blocked / sample fallback / price unavailable 絕對不進正式樣本。
    # - TWSE/TPEX MIS、YahooQuote、YahooChart、Yahoo daily 等真實價格來源可進正式樣本。
    # - limited_price_mode 仍可留下 T1 正式樣本，但標記 price_sample_quality，避免完全沒有昨測今收。
    #   之後 Audit / Bias 可依 sample_quality 降權，不污染高品質樣本統計。
    hard_block = bool(price_meta.get("decision_blocked"))
    bad_src = any(k in src.upper() for k in ("SAMPLE", "FALLBACK", "UNAVAILABLE", "PRICE_UNAVAILABLE_STOP"))
    if hard_block or bad_src:
        return {
            "skipped": True,
            "reason": "price_not_verified_for_official_learning",
            "ticker": getattr(getattr(forecast, "ticker", None), "resolved_symbol", ""),
        }
    row = forecast_snapshot(forecast, macro, live_data)
    limited_mode = bool(price_meta.get("limited_price_mode"))
    price_verified = bool(price_meta.get("price_verified", not limited_mode))
    row["valid_price_sample"] = True
    row["price_sample_quality"] = "verified" if price_verified and not limited_mode else "reference_limited"
    row["price_source"] = src
    row["price_status"] = price_meta.get("status")
    rid = row.get("id")
    if rid:
        for old in read_prediction_log(500):
            if old.get("id") == rid:
                return old
    append_jsonl(PREDICTION_LOG, row)
    return row
def suggest_from_forecast(forecast: FinalForecast, actual_close: Optional[float] = None) -> List[LearningSuggestion]:
    if forecast.stopped or forecast.final_t1 is None:
        return []
    if actual_close is None:
        return [LearningSuggestion(forecast.ticker.resolved_symbol, "pending_audit", "等待收盤後比對，不自動改主程式", 0.0, "尚未有 actual close", False)]
    pred = forecast.final_t0 if forecast.final_t0 is not None else forecast.final_t1
    err_pct = (actual_close - pred) / pred * 100.0 if pred else 0.0
    if abs(err_pct) < 1.5:
        return [LearningSuggestion(forecast.ticker.resolved_symbol, "within_tolerance", "不調權重", 0.0, f"誤差 {err_pct:+.2f}%", False)]
    etype = _classify_error(forecast, actual_close, err_pct)
    direction = "+" if err_pct > 0 else "-"
    return [LearningSuggestion(forecast.ticker.resolved_symbol, etype, f"連續同類錯誤後建議 {direction}1～3% 個股偏壓", 1.0, f"誤差 {err_pct:+.2f}%｜單次只記錄", True)]
def _classify_error(forecast: FinalForecast, actual_close: float, err_pct: float) -> str:
    radar_text = "\n".join(str(v) for v in (forecast.radar or {}).values())
    if "VWAP 下方" in radar_text and err_pct > 0:
        return "VWAP underweight"
    if "VWAP 上方" in radar_text and err_pct < 0:
        return "FQC overpull"
    if "融資" in radar_text and err_pct < 0:
        return "Risk Cascade 漏判"
    if "事件" in radar_text and abs(err_pct) >= 2.5:
        return "Macro overfit"
    return "under_prediction" if err_pct > 0 else "over_prediction"
def audit_prediction_row(
    row: Dict[str, Any],
    actual_close: float,
    source: str = "manual",
    target: str = "today",
    actual_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Persist close, direction and predicted-range accuracy.
    RC4.2 keeps price-point accuracy separate from direction accuracy.  This is
    important for cases such as 2327: direction can be correct while the close
    magnitude or intraday tail is underestimated.
    """
    target = "next" if str(target).lower().startswith("next") else "today"
    prediction_id = str(row.get("id") or "")
    audit_id = f"{prediction_id}:{target}"
    for old in read_audit_log(500):
        if old.get("audit_id") == audit_id:
            return old
    snap = dict(actual_snapshot or {})
    pred_key = "next_close_est" if target == "next" else "today_close_est"
    pred = _safe_float(row.get(pred_key))
    actual = _safe_float(actual_close)
    err = actual - pred if pred else 0.0
    err_pct = ((actual - pred) / pred * 100.0) if pred else 0.0
    ticker = str(row.get("ticker") or "UNKNOWN")
    error_type = "within_tolerance" if abs(err_pct) < 1.0 else ("under_prediction" if err_pct > 0 else "over_prediction")
    anchor = _safe_float(row.get("anchor_close") or row.get("spot_last"), 0.0)
    neutral_band = max(_safe_float(row.get("direction_neutral_band_pct"), 0.30), 0.05)
    actual_return_pct = ((actual - anchor) / anchor * 100.0) if anchor > 0 and actual > 0 else 0.0
    actual_direction = "UP" if actual_return_pct > neutral_band else "DOWN" if actual_return_pct < -neutral_band else "NEUTRAL"
    if target == "next":
        predicted_direction = str(row.get("predicted_direction") or "")
    else:
        today_return = ((_safe_float(row.get("today_close_est")) - anchor) / anchor * 100.0) if anchor > 0 else 0.0
        predicted_direction = "UP" if today_return > neutral_band else "DOWN" if today_return < -neutral_band else "NEUTRAL"
    if predicted_direction not in {"UP", "DOWN", "NEUTRAL"}:
        predicted_return = ((_safe_float(row.get(pred_key)) - anchor) / anchor * 100.0) if anchor > 0 else 0.0
        predicted_direction = "UP" if predicted_return > neutral_band else "DOWN" if predicted_return < -neutral_band else "NEUTRAL"
    direction_hit = bool(anchor > 0 and predicted_direction == actual_direction)
    brier = None
    assigned_probability = None
    if target == "next":
        p_up = _safe_float(row.get("p_up"), -1.0)
        p_neutral = _safe_float(row.get("p_neutral"), -1.0)
        p_down = _safe_float(row.get("p_down"), -1.0)
        if min(p_up, p_neutral, p_down) >= 0 and 0.98 <= (p_up + p_neutral + p_down) <= 1.02:
            y_up = 1.0 if actual_direction == "UP" else 0.0
            y_neutral = 1.0 if actual_direction == "NEUTRAL" else 0.0
            y_down = 1.0 if actual_direction == "DOWN" else 0.0
            brier = ((p_up - y_up) ** 2 + (p_neutral - y_neutral) ** 2 + (p_down - y_down) ** 2) / 3.0
            assigned_probability = {"UP": p_up, "NEUTRAL": p_neutral, "DOWN": p_down}[actual_direction]
    predicted_low = _safe_float(row.get("next_low_est"), 0.0) if target == "next" else 0.0
    predicted_high = _safe_float(row.get("next_high_est"), 0.0) if target == "next" else 0.0
    actual_open = _safe_float(snap.get("actual_open"), 0.0)
    actual_high = _safe_float(snap.get("actual_high"), 0.0)
    actual_low = _safe_float(snap.get("actual_low"), 0.0)
    range_available = bool(target == "next" and predicted_low > 0 and predicted_high > predicted_low)
    close_in_range = bool(range_available and predicted_low <= actual <= predicted_high)
    actual_range_in_band = bool(
        range_available and actual_low > 0 and actual_high > 0
        and actual_low >= predicted_low and actual_high <= predicted_high
    )
    downside_tail_breach_pct = (
        ((actual_low - predicted_low) / predicted_low) * 100.0
        if range_available and actual_low > 0 and actual_low < predicted_low else 0.0
    )
    upside_tail_break_pct = (
        ((actual_high - predicted_high) / predicted_high) * 100.0
        if range_available and actual_high > predicted_high else 0.0
    )
    defense_stop = _safe_float(row.get("defense_stop"), 0.0)
    defense_touched = bool(defense_stop > 0 and actual_low > 0 and actual_low <= defense_stop)
    actual_valid = bool(snap.get("actual_valid", True))
    audit = {
        "audit_id": audit_id,
        "audit_time_tw": _now(),
        "audit_date_tw": _run_date_tw(),
        "prediction_id": prediction_id,
        "ticker": ticker,
        "market": row.get("market"),
        "model_version": row.get("model_version"),
        "target": target,
        "target_trade_date": (
            row.get("target_trade_date")
            if target == "next" or _prediction_market(row) == "US"
            else row.get("run_date_tw")
        ),
        "prediction_run_date_tw": row.get("run_date_tw"),
        "predicted_close": round(pred, 4),
        "actual_close": round(actual, 4),
        "error": round(err, 4),
        "error_pct": round(err_pct, 4),
        "error_type": error_type,
        "anchor_close": round(anchor, 4) if anchor else None,
        "predicted_return_pct": row.get("predicted_return_pct") if target == "next" else None,
        "actual_return_pct": round(actual_return_pct, 4),
        "neutral_band_pct": round(neutral_band, 4),
        "predicted_direction": predicted_direction,
        "actual_direction": actual_direction,
        "direction_hit": direction_hit,
        "direction_brier": round(brier, 6) if brier is not None else None,
        "actual_direction_probability": round(assigned_probability, 6) if assigned_probability is not None else None,
        "predicted_low": round(predicted_low, 4) if predicted_low else None,
        "predicted_high": round(predicted_high, 4) if predicted_high else None,
        "actual_open": round(actual_open, 4) if actual_open else None,
        "actual_high": round(actual_high, 4) if actual_high else None,
        "actual_low": round(actual_low, 4) if actual_low else None,
        "close_in_predicted_range": close_in_range if range_available else None,
        "actual_range_inside_predicted_band": actual_range_in_band if range_available and actual_low > 0 and actual_high > 0 else None,
        "downside_tail_breach_pct": round(downside_tail_breach_pct, 4),
        "upside_tail_break_pct": round(upside_tail_break_pct, 4),
        "defense_stop": round(defense_stop, 4) if defense_stop else None,
        "defense_stop_touched": defense_touched if defense_stop and actual_low else None,
        "direction_score": row.get("direction_score"),
        "direction_confidence": row.get("direction_confidence"),
        "direction_quality": row.get("direction_quality"),
        "direction_conflict": row.get("direction_conflict"),
        "direction_regime": row.get("direction_regime"),
        "direction_family_contributions": dict(row.get("direction_family_contributions") or {}),
        "direction_factor_contributions": dict(row.get("direction_factor_contributions") or {}),
        "direction_risk_contributions": dict(row.get("direction_risk_contributions") or {}),
        "family_attribution": contribution_attribution(
            dict(row.get("direction_family_contributions") or {}), actual_direction, limit=16
        ) if target == "next" else {},
        "factor_attribution": contribution_attribution(
            dict(row.get("direction_factor_contributions") or {}), actual_direction, limit=20
        ) if target == "next" else {},
        "dominant_force": ((row.get("prediction_dna") or {}).get("dominant_force") if isinstance(row.get("prediction_dna"), dict) else None),
        "dominant_force_hit": None,
        "price_sample_quality": row.get("price_sample_quality"),
        "actual_price_date": snap.get("price_date"),
        "actual_market_status": snap.get("market_status"),
        "actual_price_source": snap.get("source"),
        "prediction_run_time_tw": row.get("run_time_tw"),
        "prediction_session_mode": row.get("session_mode"),
        "source": source,
        "safe_to_apply": bool(abs(err_pct) >= 1.0 and row.get("price_sample_quality") == "verified" and actual_valid),
        "applied": False,
    }
    audit["actual_valid"] = actual_valid
    dominant_force = str(audit.get("dominant_force") or "")
    if dominant_force and isinstance(audit.get("factor_attribution"), dict):
        dominant_row = audit["factor_attribution"].get(dominant_force)
        if isinstance(dominant_row, dict):
            audit["dominant_force_hit"] = bool(dominant_row.get("aligned"))
    append_jsonl(AUDIT_LOG, audit)
    _update_profile_from_audit(audit)
    return audit
def audit_today_prediction_for_forecast(forecast: FinalForecast, actual_close: float, source: str = "auto_close_compare") -> Dict[str, Any]:
    """Find today's intraday prediction snapshot, compare it with actual close, and persist audit.
    This is the real Auto-Learning bridge for the frontend:
    prediction snapshot -> actual close -> error -> stock profile.
    """
    key = forecast.ticker.resolved_symbol
    rows = _same_day_predictions(key, 800, forecast.ticker.market)
    intraday_rows = [r for r in rows if r.get("session_mode") == "intraday" and r.get("today_close_est") is not None]
    candidate = intraday_rows[-1] if intraday_rows else None
    if not candidate:
        return {
            "status": "no_intraday_prediction",
            "ticker": key,
            "actual_close": actual_close,
            "message": "尚無今日盤中預測快照，無法做真正預測VS實際。",
        }
    audit = audit_prediction_row(candidate, actual_close, source=source, target="today")
    audit["status"] = "audited"
    return audit
def audit_t1_prediction_for_forecast(forecast: FinalForecast, actual_close: float, source: str = "auto_t1_close_compare") -> Dict[str, Any]:
    """Compare the previous session T1 snapshot with today's official close.
    This is the V12 T1 Memory Bridge: yesterday forecast -> today close -> audit.
    It does not mutate model code; it only writes audit memory/profile.
    """
    key = forecast.ticker.resolved_symbol
    today = _audit_trade_date(forecast.ticker.market)
    candidate = _latest_t1_candidate(key, today, 1200)
    if not candidate:
        return {
            "status": "no_t1_prediction",
            "ticker": key,
            "target_trade_date": today,
            "actual_close": actual_close,
            "message": "尚無昨日 T1 預測快照，無法做昨測今收。",
        }
    audit = audit_prediction_row(candidate, actual_close, source=source, target="next")
    audit["status"] = "audited"
    return audit
def _format_close_audit_display(audit: Dict[str, Any], label: str) -> Dict[str, Any]:
    pred = _safe_float(audit.get("predicted_close"))
    actual = _safe_float(audit.get("actual_close"))
    err = _safe_float(audit.get("error"))
    err_pct = _safe_float(audit.get("error_pct"))
    if label == "昨測今收":
        direction = "昨日低估，今日收盤強於預期" if err > 0 else "昨日高估，今日收盤弱於預期" if err < 0 else "命中今日收盤"
        run_date = str(audit.get("prediction_run_date_tw") or "前一交易日")
        audit["display"] = f"昨測今收：{run_date} 預估 {pred:.2f}｜今日收盤 {actual:.2f}｜誤差 {err:+.2f} / {err_pct:+.2f}%｜{direction}"
    else:
        direction = "低估收盤，模型偏保守" if err > 0 else "高估收盤，模型偏樂觀" if err < 0 else "命中收盤"
        audit["display"] = f"今日預測VS實際：預估 {pred:.2f}｜實際 {actual:.2f}｜誤差 {err:+.2f} / {err_pct:+.2f}%｜{direction}"
    return audit
def t1_prediction_vs_actual(forecast: FinalForecast, actual_close: Optional[float] = None, write: bool = False) -> Dict[str, Any]:
    """UI-ready 昨測今收. Default is preview/read-only; write only from two-click audit."""
    key = forecast.ticker.resolved_symbol
    if actual_close is None:
        actual_close = _safe_float((forecast.decision_card or {}).get("現價"))
    if write:
        audit = audit_t1_prediction_for_forecast(forecast, _safe_float(actual_close), source="frontend_t1_close_compare")
        if audit.get("status") == "audited":
            return _format_close_audit_display(audit, "昨測今收")
        audit["display"] = "昨測今收：尚無昨日 T1 預測快照"
        return audit
    candidate = _latest_t1_candidate(key, _audit_trade_date(forecast.ticker.market), 1200)
    if not candidate:
        return {"status": "no_t1_prediction", "ticker": key, "display": "昨測今收：尚無昨日 T1 預測快照"}
    old = _latest_audit_for_prediction(str(candidate.get("id") or ""), "next")
    if old:
        old = dict(old); old["status"] = "audited"; return _format_close_audit_display(old, "昨測今收")
    pred = _safe_float(candidate.get("next_close_est"))
    actual = _safe_float(actual_close)
    err = actual - pred if pred else 0.0
    err_pct = ((actual - pred) / pred * 100.0) if pred else 0.0
    return {"status": "preview", "ticker": key, "display": f"昨測今收預覽：{candidate.get('run_date_tw')} 預估 {pred:.2f}｜目前/收盤 {actual:.2f}｜誤差 {err:+.2f} / {err_pct:+.2f}%｜尚未寫入"}
def today_prediction_vs_actual(forecast: FinalForecast, actual_close: Optional[float] = None, write: bool = False) -> Dict[str, Any]:
    """UI-ready 今日預測VS實際. Default is preview/read-only; write only from two-click audit."""
    key = forecast.ticker.resolved_symbol
    if actual_close is None:
        actual_close = _safe_float((forecast.decision_card or {}).get("現價"))
    rows = _same_day_predictions(key, 800)
    intraday_rows = [r for r in rows if r.get("session_mode") == "intraday" and r.get("today_close_est") is not None]
    if not intraday_rows:
        return {"status": "no_intraday_prediction", "ticker": key, "actual_close": actual_close, "display": "今日預測VS實際：尚無盤中預測快照"}
    row = intraday_rows[-1]
    if write:
        audit = audit_prediction_row(row, actual_close, source="frontend_close_compare", target="today")
        return _format_close_audit_display(audit, "今日預測VS實際")
    old = _latest_audit_for_prediction(str(row.get("id") or ""), "today")
    if old:
        old = dict(old); old["status"] = "audited"; return _format_close_audit_display(old, "今日預測VS實際")
    pred = _safe_float(row.get("today_close_est"))
    actual = _safe_float(actual_close)
    err = actual - pred if pred else 0.0
    err_pct = ((actual - pred) / pred * 100.0) if pred else 0.0
    return {"status": "preview", "ticker": key, "display": f"今日預測VS實際預覽：預估 {pred:.2f}｜目前/收盤 {actual:.2f}｜誤差 {err:+.2f} / {err_pct:+.2f}%｜尚未寫入"}
def _update_profile_from_audit(audit: Dict[str, Any]) -> None:
    profiles = load_profiles()
    ticker = str(audit.get("ticker") or "UNKNOWN")
    p = profiles.get(ticker, {})
    audits = int(p.get("audit_count", 0)) + 1
    old_avg = _safe_float(p.get("avg_abs_error_pct", 0.0))
    new_abs = abs(_safe_float(audit.get("error_pct")))
    avg_abs = ((old_avg * (audits - 1)) + new_abs) / audits
    error_type = str(audit.get("error_type") or "unknown")
    counts = p.get("error_type_counts", {}) if isinstance(p.get("error_type_counts"), dict) else {}
    counts[error_type] = int(counts.get(error_type, 0)) + 1
    direction_count = int(p.get("direction_audit_count", 0))
    hit_rate = _safe_float(p.get("direction_hit_rate", 0.0), 0.0)
    avg_brier = _safe_float(p.get("avg_direction_brier", 0.0), 0.0)
    if audit.get("target") == "next" and audit.get("direction_hit") is not None:
        direction_count += 1
        hit = 1.0 if audit.get("direction_hit") else 0.0
        hit_rate = ((hit_rate * (direction_count - 1)) + hit) / direction_count
        if audit.get("direction_brier") is not None:
            brier = _safe_float(audit.get("direction_brier"), 0.0)
            avg_brier = ((avg_brier * (direction_count - 1)) + brier) / direction_count
    range_count = int(p.get("range_audit_count", 0))
    range_hit_rate = _safe_float(p.get("close_range_hit_rate", 0.0), 0.0)
    avg_tail_breach = _safe_float(p.get("avg_downside_tail_breach_pct", 0.0), 0.0)
    if audit.get("target") == "next" and audit.get("close_in_predicted_range") is not None:
        range_count += 1
        range_hit = 1.0 if audit.get("close_in_predicted_range") else 0.0
        range_hit_rate = ((range_hit_rate * (range_count - 1)) + range_hit) / range_count
        tail = abs(min(_safe_float(audit.get("downside_tail_breach_pct"), 0.0), 0.0))
        avg_tail_breach = ((avg_tail_breach * (range_count - 1)) + tail) / range_count
    family_learning = p.get("family_learning", {}) if isinstance(p.get("family_learning"), dict) else {}
    factor_learning = p.get("factor_learning", {}) if isinstance(p.get("factor_learning"), dict) else {}
    verified_t1 = bool(
        audit.get("target") == "next"
        and audit.get("price_sample_quality") == "verified"
        and audit.get("actual_valid")
        and str(audit.get("actual_direction") or "") in {"UP", "DOWN"}
    )
    if verified_t1:
        family_learning = update_attribution_learning(family_learning, dict(audit.get("family_attribution") or {}), now_text=_now())
        factor_learning = update_attribution_learning(factor_learning, dict(audit.get("factor_attribution") or {}), now_text=_now())

    # V12.1 safety gate: no permanent stock bias from two observations.
    # Only verified samples, at least 20 audited closes, and a repeated error
    # pattern may create a small suggestion.  Tino approval is still required.
    suggested_bias = _safe_float(p.get("suggested_bias", 0.0))
    verified = audit.get("price_sample_quality") == "verified"
    repeated = int(counts.get(error_type, 0)) >= 5
    if verified and audits >= 20 and repeated:
        if error_type == "under_prediction":
            suggested_bias = min(0.02, suggested_bias + 0.0025)
        elif error_type == "over_prediction":
            suggested_bias = max(-0.02, suggested_bias - 0.0025)
    p.update({
        "ticker": ticker,
        "audit_count": audits,
        "avg_abs_error_pct": round(avg_abs, 4),
        "last_error_pct": audit.get("error_pct"),
        "last_error_type": error_type,
        "error_type_counts": counts,
        "direction_audit_count": direction_count,
        "direction_hit_rate": round(hit_rate, 4) if direction_count else None,
        "avg_direction_brier": round(avg_brier, 6) if direction_count else None,
        "last_predicted_direction": audit.get("predicted_direction"),
        "last_actual_direction": audit.get("actual_direction"),
        "last_direction_hit": audit.get("direction_hit"),
        "range_audit_count": range_count,
        "close_range_hit_rate": round(range_hit_rate, 4) if range_count else None,
        "avg_downside_tail_breach_pct": round(avg_tail_breach, 4) if range_count else None,
        "last_close_in_predicted_range": audit.get("close_in_predicted_range"),
        "last_downside_tail_breach_pct": audit.get("downside_tail_breach_pct"),
        "last_defense_stop_touched": audit.get("defense_stop_touched"),
        "suggested_bias": round(suggested_bias, 4),
        "approved_bias": _safe_float(p.get("approved_bias", 0.0)),
        "family_learning": family_learning,
        "factor_learning": factor_learning,
        "learning_maturity": round(min(1.0, direction_count / 40.0), 4) if direction_count else 0.0,
        "active_family_count": sum(1 for row in family_learning.values() if isinstance(row, dict) and int(_safe_float(row.get("count"), 0.0)) >= 8),
        "last_dominant_force": audit.get("dominant_force"),
        "last_dominant_force_hit": audit.get("dominant_force_hit"),
        "learning_schema": "RC4.5_DNA_V1",
        "learning_gate": "verified_t1_family>=8; price_bias>=20_and_repeated>=5",
        "updated_at_tw": _now(),
    })
    profiles[ticker] = p
    save_profiles(profiles)
def approve_profile_bias(ticker: str, max_abs_bias: float = 0.02) -> Dict[str, Any]:
    profiles = load_profiles()
    key = _canonical(ticker)
    p = profiles.get(key, {"ticker": key})
    suggested = max(-max_abs_bias, min(max_abs_bias, _safe_float(p.get("suggested_bias", 0.0))))
    p["approved_bias"] = round(suggested, 4)
    p["approved_at_tw"] = _now()
    p["approval_note"] = "Tino Admin approved; applied as small SignalPacket bias only."
    profiles[key] = p
    save_profiles(profiles)
    return p
def reset_profile_bias(ticker: str) -> Dict[str, Any]:
    profiles = load_profiles()
    key = _canonical(ticker)
    p = profiles.get(key, {"ticker": key})
    p["approved_bias"] = 0.0
    p["approved_at_tw"] = _now()
    p["approval_note"] = "Tino Admin reset approved learning bias."
    profiles[key] = p
    save_profiles(profiles)
    return p
def get_profile(ticker: str) -> Dict[str, Any]:
    return load_profiles().get(_canonical(ticker), {})
def build_learning_signals(raw_symbol: str) -> List[SignalPacket]:
    key = _canonical(raw_symbol)
    profile = get_profile(key)
    bias = _safe_float(profile.get("approved_bias", 0.0))
    if abs(bias) < 0.0001:
        return []
    direction = "偏多修正" if bias > 0 else "偏空修正"
    return [SignalPacket(
        "LearningProfile",
        f"{key} 個股學習{direction}｜{bias:+.2%}",
        0.0,
        0.8,
        0.0,
        bias,
        f"Auto-Learning Audit approved profile｜audit_count={profile.get('audit_count', 0)}｜avg_abs_error={profile.get('avg_abs_error_pct', 'NA')}%",
        "AutoLearningAudit",
        str(profile.get("approved_at_tw") or profile.get("updated_at_tw") or ""),
        True,
    )]
def audit_latest_prediction_for_ticker(ticker: str, actual_close: float, target: str = "today") -> Optional[Dict[str, Any]]:
    key = _canonical(ticker)
    rows = [r for r in read_prediction_log(1000) if r.get("ticker") == key]
    if not rows:
        return None
    if target == "next":
        candidate = _latest_t1_candidate(key, _run_date_tw(), 1200)
        if candidate:
            return audit_prediction_row(candidate, actual_close, source="manual_admin_t1", target="next")
    if target == "today":
        intraday = [r for r in rows if r.get("run_date_tw") == _run_date_tw() and r.get("session_mode") == "intraday"]
        if intraday:
            return audit_prediction_row(intraday[-1], actual_close, source="manual_admin", target="today")
    return audit_prediction_row(rows[-1], actual_close, source="manual_admin", target=target)

# === TINO V12 Hotfix: Query Ledger Auto Audit ===
def _audit_id_set(limit: int = 1200) -> set[str]:
    """Read the bounded audit tail once and keep only compact IDs.

    The previous pending scan reopened and reparsed the audit JSONL once per
    prediction row.  On a long-lived Streamlit process that created avoidable
    I/O and temporary Python objects.  One compact set keeps memory stable and
    makes duplicate checks O(1).
    """
    try:
        bounded = max(100, min(int(limit), 2000))
    except Exception:
        bounded = 1200
    return {
        str(row.get("audit_id") or "")
        for row in read_audit_log(bounded)
        if isinstance(row, dict) and row.get("audit_id")
    }


def _audit_exists(prediction_id: str, target: str, audit_ids: Optional[set[str]] = None) -> bool:
    audit_id = f"{prediction_id}:{target}"
    if audit_ids is not None:
        return audit_id in audit_ids
    return audit_id in _audit_id_set(1200)


def pending_auto_audit_summary(limit: int = 1200, market_filter: Optional[str] = None, trade_date: Optional[str] = None) -> Dict[str, Any]:
    """Summarize snapshots created by normal Analyze that can be audited later.
    Every Analyze already writes a prediction snapshot through app.py/log_prediction.
    This helper lets Admin show that queried tickers are queued for one-click audit.
    """
    today = str(trade_date or _audit_trade_date(market_filter))
    preds = read_prediction_log(limit)
    audit_ids = _audit_id_set(max(1200, int(limit or 0)))
    t1_pending = []
    today_pending = []
    seen_t1 = set()
    seen_today = set()
    for r in preds:
        pid = str(r.get("id") or "")
        ticker = str(r.get("ticker") or "")
        if not pid or not ticker or not _market_matches(r, market_filter):
            continue
        if str(r.get("target_trade_date") or "") == today and r.get("next_close_est") is not None and not _audit_exists(pid, "next", audit_ids):
            k = (ticker, str(r.get("target_trade_date") or ""), pid)
            if k not in seen_t1:
                t1_pending.append(r); seen_t1.add(k)
        row_market = _prediction_market(r)
        today_target = (
            str(r.get("target_trade_date") or "")
            if row_market == "US"
            else str(r.get("run_date_tw") or "")
        )
        if (
            today_target == today
            and str(r.get("session_mode") or "") == "intraday"
            and r.get("today_close_est") is not None
            and not _audit_exists(pid, "today", audit_ids)
        ):
            k = (ticker, today_target, pid)
            if k not in seen_today:
                today_pending.append(r); seen_today.add(k)
    return {
        "prediction_count": len(preds),
        "pending_t1_count": len(t1_pending),
        "pending_today_count": len(today_pending),
        "pending_tickers": sorted({str(r.get("ticker") or "") for r in (t1_pending + today_pending) if r.get("ticker")}),
        "market_filter": str(market_filter or "ALL").upper(),
        "latest_prediction": preds[-1] if preds else {},
    }
def auto_audit_queried_predictions(limit: int = 1200, max_tickers: int = 6, apply_safe_learning: bool = True, actual_foreign_billion: Optional[float] = None, market_filter: Optional[str] = None, trade_date: Optional[str] = None) -> Dict[str, Any]:
    """Audit all tickers that Tino has queried/logged.
    Intended workflow:
    1) Tino presses Analyze for any ticker. app.py automatically logs the snapshot.
    2) After close, Tino presses this one button. It fetches each logged ticker's
       current/official close, writes T1/today audits, updates profiles, and can
       calibrate Foreign Flow V2 once using the supplied official market foreign flow.
    Duplicate audits are blocked by audit_id, so reruns are safe.
    """
    today = str(trade_date or _audit_trade_date(market_filter))
    preds = read_prediction_log(limit)
    audit_ids = _audit_id_set(max(1200, int(limit or 0)))
    # keep the latest row per ticker/target/session bucket to avoid over-fetching
    t1_rows: Dict[str, Dict[str, Any]] = {}
    today_rows: Dict[str, Dict[str, Any]] = {}
    for r in preds:
        pid = str(r.get("id") or "")
        ticker = str(r.get("ticker") or "")
        if not pid or not ticker or not _market_matches(r, market_filter):
            continue
        if str(r.get("target_trade_date") or "") == today and r.get("next_close_est") is not None and not _audit_exists(pid, "next", audit_ids):
            # newest prediction for the same ticker/target date wins
            key = f"{ticker}|{r.get('target_trade_date')}"
            t1_rows[key] = r
        row_market = _prediction_market(r)
        today_target = (
            str(r.get("target_trade_date") or "")
            if row_market == "US"
            else str(r.get("run_date_tw") or "")
        )
        if (
            today_target == today
            and str(r.get("session_mode") or "") == "intraday"
            and r.get("today_close_est") is not None
            and not _audit_exists(pid, "today", audit_ids)
        ):
            key = f"{ticker}|{today_target}"
            today_rows[key] = r
    all_rows = list(t1_rows.values()) + list(today_rows.values())
    tickers = []
    for r in all_rows:
        t = str(r.get("ticker") or "")
        if t and t not in tickers:
            tickers.append(t)
    tickers = tickers[:max_tickers]
    actuals: Dict[str, Dict[str, Any]] = {}
    errors: List[Dict[str, Any]] = []
    for t in tickers:
        try:
            actuals[t] = fetch_actual_daily_snapshot(t)
        except Exception as exc:
            errors.append({"ticker": t, "error": f"{type(exc).__name__}: {exc}"})
    audited_t1: List[Dict[str, Any]] = []
    audited_today: List[Dict[str, Any]] = []
    audited_foreign: List[Dict[str, Any]] = []
    for r in t1_rows.values():
        t = str(r.get("ticker") or "")
        snap = dict(actuals.get(t) or {})
        target_date = str(r.get("target_trade_date") or "")
        if not actual_matches_target(snap, target_date):
            errors.append({
                "ticker": t,
                "target": "next",
                "error": "official_close_not_ready_or_target_date_mismatch",
                "target_date": target_date,
                "actual_price_date": snap.get("price_date"),
                "market_status": snap.get("market_status"),
            })
            continue
        actual = _safe_float(snap.get("actual_close"), 0.0)
        audited_t1.append(audit_prediction_row(
            r, actual, source="auto_audit_all_queried_t1", target="next", actual_snapshot=snap
        ))
    for r in today_rows.values():
        t = str(r.get("ticker") or "")
        snap = dict(actuals.get(t) or {})
        target_date = (
            str(r.get("target_trade_date") or "")
            if _prediction_market(r) == "US"
            else str(r.get("run_date_tw") or "")
        )
        if not actual_matches_target(snap, target_date):
            errors.append({
                "ticker": t,
                "target": "today",
                "error": "official_close_not_ready_or_target_date_mismatch",
                "target_date": target_date,
                "actual_price_date": snap.get("price_date"),
                "market_status": snap.get("market_status"),
            })
            continue
        actual = _safe_float(snap.get("actual_close"), 0.0)
        audited_today.append(audit_prediction_row(
            r, actual, source="auto_audit_all_queried_today", target="today", actual_snapshot=snap
        ))
    if actual_foreign_billion is not None:
        # Market foreign flow is market-wide; audit once per run-date snapshot to prevent duplicate signal overweight.
        used_dates = set()
        for r in reversed(preds):
            rd = str(r.get("run_date_tw") or "")
            if not rd or rd in used_dates:
                continue
            if _forecast_flow_from_row(r):
                audited_foreign.append(audit_foreign_flow_for_row(r, actual_foreign_billion, source="auto_audit_all_queried_foreign_flow"))
                used_dates.add(rd)
                break
    approved = {}
    if apply_safe_learning:
        profiles = load_profiles()
        for t in tickers:
            try:
                approved[t] = approve_profile_bias(t)
            except Exception:
                pass
        if actual_foreign_billion is not None:
            try:
                approved[FOREIGN_FLOW_PROFILE_KEY] = approve_foreign_flow_learning()
            except Exception:
                pass
    return {
        "status": "done",
        "market_filter": str(market_filter or "ALL").upper(),
        "audited_t1_count": len(audited_t1),
        "audited_today_count": len(audited_today),
        "audited_foreign_count": len(audited_foreign),
        "fetched_ticker_count": len(actuals),
        "errors": errors[:20],
        "actuals": actuals,
        "dashboard": prediction_audit_dashboard(limit),
        "approved_count": len(approved),
    }
def recent_learning_tables(limit: int = 80) -> Dict[str, List[Dict[str, Any]]]:
    return {
        "predictions": read_prediction_log(limit),
        "audits": read_audit_log(limit),
        "profiles": list(load_profiles().values()),
    }

# === TINO V12 Hotfix: Prediction Audit Dashboard + Foreign Flow Auto-Learning ===
FOREIGN_FLOW_PROFILE_KEY = "__FOREIGN_FLOW_V2__"
def _sign(v: Any) -> int:
    x = _safe_float(v, 0.0)
    return 1 if x > 0 else -1 if x < 0 else 0
def _amount_tier(abs_billion: float) -> str:
    v = abs(_safe_float(abs_billion, 0.0))
    if v >= 600:
        return "600億以上"
    if v >= 300:
        return "300~600億"
    if v >= 100:
        return "100~300億"
    if v >= 30:
        return "30~100億"
    return "30億內"
def _tier_index(tier: str) -> int:
    order = ["30億內", "30~100億", "100~300億", "300~600億", "600億以上"]
    try:
        return order.index(str(tier))
    except Exception:
        return 0
def _forecast_flow_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    ff = row.get("foreign_flow_v2") if isinstance(row.get("foreign_flow_v2"), dict) else {}
    if ff and ff.get("accepted"):
        return ff
    tv = row.get("tv_pressure") if isinstance(row.get("tv_pressure"), dict) else {}
    if tv and tv.get("accepted"):
        direction_txt = str(tv.get("direction") or "")
        direction = "sell" if "賣" in direction_txt else "buy" if "買" in direction_txt else "neutral"
        return {
            "accepted": True,
            "direction": direction,
            "direction_label": "偏賣" if direction == "sell" else "偏買" if direction == "buy" else "中性",
            "amount_billion": tv.get("amount_billion"),
            "display": str(tv.get("reason") or "TV外資壓力公式"),
        }
    radar = row.get("radar") if isinstance(row.get("radar"), dict) else {}
    line = str(radar.get("外資期貨") or "")
    if not line:
        return {}
    direction = "sell" if "賣壓" in line or "偏賣" in line else "buy" if "買盤" in line or "偏買" in line else "neutral"
    import re
    m = re.search(r"(\d+(?:\.\d+)?)億", line)
    return {"accepted": bool(m), "direction": direction, "direction_label": direction, "amount_billion": float(m.group(1)) if m else None, "display": line}
def audit_foreign_flow_for_row(row: Dict[str, Any], actual_foreign_billion: float, source: str = "manual_admin_foreign_flow") -> Dict[str, Any]:
    """Compare predicted foreign-flow direction/tier against official close data.
    The official value is used only for calibration/audit. It is not copied as
    the next prediction answer.
    """
    prediction_id = str(row.get("id") or "")
    audit_id = f"{prediction_id}:foreign_flow_v2"
    for old in read_audit_log(800):
        if old.get("audit_id") == audit_id:
            return old
    ff = _forecast_flow_from_row(row)
    pred_amt = abs(_safe_float(ff.get("amount_billion"), 0.0))
    pred_dir = str(ff.get("direction") or "neutral")
    pred_sign = -1 if pred_dir == "sell" else 1 if pred_dir == "buy" else 0
    actual = _safe_float(actual_foreign_billion, 0.0)
    actual_sign = _sign(actual)
    direction_hit = bool(pred_sign and actual_sign and pred_sign == actual_sign)
    pred_tier = _amount_tier(pred_amt)
    actual_tier = _amount_tier(abs(actual))
    tier_gap = _tier_index(actual_tier) - _tier_index(pred_tier)
    abs_err = abs(abs(actual) - pred_amt)
    audit = {
        "audit_id": audit_id,
        "audit_time_tw": _now(),
        "audit_date_tw": _run_date_tw(),
        "prediction_id": prediction_id,
        "ticker": str(row.get("ticker") or "UNKNOWN"),
        "target": "foreign_flow_v2",
        "target_trade_date": row.get("run_date_tw"),
        "prediction_run_date_tw": row.get("run_date_tw"),
        "predicted_direction": pred_dir,
        "actual_direction": "sell" if actual < 0 else "buy" if actual > 0 else "neutral",
        "direction_hit": direction_hit,
        "predicted_amount_billion": round(pred_amt, 2),
        "actual_foreign_billion": round(actual, 2),
        "predicted_tier": pred_tier,
        "actual_tier": actual_tier,
        "tier_gap": tier_gap,
        "amount_abs_error_billion": round(abs_err, 2),
        "source": source,
        "safe_to_apply": True,
        "applied": False,
    }
    append_jsonl(AUDIT_LOG, audit)
    _update_foreign_flow_profile(audit)
    return audit
def _update_foreign_flow_profile(audit: Dict[str, Any]) -> None:
    profiles = load_profiles()
    p = profiles.get(FOREIGN_FLOW_PROFILE_KEY, {"ticker": FOREIGN_FLOW_PROFILE_KEY})
    n = int(p.get("foreign_audit_count", 0)) + 1
    old_hit = _safe_float(p.get("direction_hit_rate", 0.0), 0.0)
    hit_val = 1.0 if audit.get("direction_hit") else 0.0
    hit_rate = ((old_hit * (n - 1)) + hit_val) / n
    old_err = _safe_float(p.get("avg_amount_abs_error_billion", 0.0), 0.0)
    err = _safe_float(audit.get("amount_abs_error_billion"), 0.0)
    avg_err = ((old_err * (n - 1)) + err) / n
    tier_gap = int(audit.get("tier_gap") or 0)
    suggested_scale = _safe_float(p.get("suggested_foreign_amount_scale", 1.0), 1.0)
    suggested_bias = _safe_float(p.get("suggested_foreign_direction_bias", 0.0), 0.0)
    if audit.get("direction_hit"):
        if tier_gap >= 1:
            suggested_scale += 0.04
        elif tier_gap <= -1:
            suggested_scale -= 0.04
    else:
        suggested_bias += -0.5 if audit.get("actual_direction") == "sell" else 0.5 if audit.get("actual_direction") == "buy" else 0.0
    suggested_scale = max(0.70, min(1.35, suggested_scale))
    suggested_bias = max(-6.0, min(6.0, suggested_bias))
    p.update({
        "ticker": FOREIGN_FLOW_PROFILE_KEY,
        "foreign_audit_count": n,
        "direction_hit_rate": round(hit_rate, 4),
        "avg_amount_abs_error_billion": round(avg_err, 2),
        "last_direction_hit": bool(audit.get("direction_hit")),
        "last_tier_gap": tier_gap,
        "last_actual_foreign_billion": audit.get("actual_foreign_billion"),
        "suggested_foreign_amount_scale": round(suggested_scale, 4),
        "suggested_foreign_direction_bias": round(suggested_bias, 4),
        "approved_foreign_amount_scale": _safe_float(p.get("approved_foreign_amount_scale", 1.0), 1.0),
        "approved_foreign_direction_bias": _safe_float(p.get("approved_foreign_direction_bias", 0.0), 0.0),
        "updated_at_tw": _now(),
    })
    profiles[FOREIGN_FLOW_PROFILE_KEY] = p
    save_profiles(profiles)
def approve_foreign_flow_learning() -> Dict[str, Any]:
    profiles = load_profiles()
    p = profiles.get(FOREIGN_FLOW_PROFILE_KEY, {"ticker": FOREIGN_FLOW_PROFILE_KEY})
    p["approved_foreign_amount_scale"] = round(max(0.75, min(1.30, _safe_float(p.get("suggested_foreign_amount_scale", 1.0), 1.0))), 4)
    p["approved_foreign_direction_bias"] = round(max(-5.0, min(5.0, _safe_float(p.get("suggested_foreign_direction_bias", 0.0), 0.0))), 4)
    p["approved_at_tw"] = _now()
    p["approval_note"] = "Tino two-click audit approved; applied only as small Foreign Flow V2 calibration."
    profiles[FOREIGN_FLOW_PROFILE_KEY] = p
    save_profiles(profiles)
    return p
def two_click_close_audit(forecast: FinalForecast, actual_close: float, actual_foreign_billion: Optional[float] = None, apply_safe_learning: bool = True) -> Dict[str, Any]:
    """One admin action: write snapshot, audit T1/today, audit foreign flow, optionally approve safe weights."""
    row = log_prediction(forecast)
    result: Dict[str, Any] = {"snapshot_id": row.get("id"), "ticker": forecast.ticker.resolved_symbol}
    result["t1_audit"] = audit_t1_prediction_for_forecast(forecast, actual_close, source="two_click_t1_close_compare")
    result["today_audit"] = audit_today_prediction_for_forecast(forecast, actual_close, source="two_click_today_close_compare")
    if actual_foreign_billion is not None:
        result["foreign_flow_audit"] = audit_foreign_flow_for_row(row, actual_foreign_billion, source="two_click_foreign_flow_compare")
    if apply_safe_learning:
        result["approved_profile"] = approve_profile_bias(forecast.ticker.resolved_symbol)
        if actual_foreign_billion is not None:
            result["approved_foreign_flow"] = approve_foreign_flow_learning()
    return result
def prediction_audit_dashboard(limit: int = 300) -> Dict[str, Any]:
    audits = read_audit_log(limit)
    price_audits = [a for a in audits if a.get("target") in ("today", "next")]
    t1 = [a for a in price_audits if a.get("target") == "next"]
    ff = [a for a in audits if a.get("target") == "foreign_flow_v2"]
    def _avg_abs(rows, key):
        vals = [abs(_safe_float(r.get(key), 0.0)) for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None
    price_direction_hits = [1 if r.get("direction_hit") else 0 for r in t1 if r.get("direction_hit") is not None]
    brier_values = [_safe_float(r.get("direction_brier")) for r in t1 if r.get("direction_brier") is not None]
    foreign_direction_hits = [1 if r.get("direction_hit") else 0 for r in ff if r.get("direction_hit") is not None]
    tier_hits = [1 if int(r.get("tier_gap") or 0) == 0 else 0 for r in ff]
    return {
        "price_audit_count": len(price_audits),
        "t1_audit_count": len(t1),
        "t1_avg_abs_error_pct": _avg_abs(t1, "error_pct"),
        "t1_direction_hit_rate": round(sum(price_direction_hits) / len(price_direction_hits) * 100, 2) if price_direction_hits else None,
        "t1_direction_brier": round(sum(brier_values) / len(brier_values), 6) if brier_values else None,
        "today_avg_abs_error_pct": _avg_abs([a for a in price_audits if a.get("target") == "today"], "error_pct"),
        "foreign_flow_audit_count": len(ff),
        "foreign_direction_hit_rate": round(sum(foreign_direction_hits) / len(foreign_direction_hits) * 100, 2) if foreign_direction_hits else None,
        "foreign_tier_hit_rate": round(sum(tier_hits) / len(tier_hits) * 100, 2) if tier_hits else None,
        "foreign_avg_abs_error_billion": _avg_abs(ff, "amount_abs_error_billion"),
        "last_price_audit": price_audits[-1] if price_audits else {},
        "last_foreign_flow_audit": ff[-1] if ff else {},
        "foreign_flow_profile": load_profiles().get(FOREIGN_FLOW_PROFILE_KEY, {}),
    }
