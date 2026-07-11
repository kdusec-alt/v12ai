# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo
from models import DataTruth, SignalPacket

TW_TZ = ZoneInfo("Asia/Taipei")

def taipei_now() -> datetime:
    return datetime.now(TW_TZ)

def today_taipei_date() -> date:
    return taipei_now().date()

def make_truth(source: str, date_value: str, fallback: bool, accepted: bool, reason: str, freshness: str = "latest") -> DataTruth:
    return DataTruth(source=source, date=str(date_value or ""), fallback=bool(fallback), accepted=bool(accepted), reason=reason, freshness=freshness)

def truth_to_main_label(truth: DataTruth) -> str:
    if not truth.accepted:
        return "資料源：未採納"
    if truth.fallback:
        return "資料源：方向參考"
    return "資料源：已驗證"

def accept_signal(signal: SignalPacket, truth: DataTruth) -> SignalPacket:
    if not truth.accepted:
        return SignalPacket(signal.module, signal.signal, 0.0, -abs(signal.confidence), signal.risk + 10, 0.0, f"Truth Guard rejected: {truth.reason}", signal.source, signal.date, False)
    return signal

def today_taipei() -> str:
    return today_taipei_date().isoformat()

def parse_date_safe(text: str) -> str:
    try:
        return datetime.fromisoformat(str(text)[:10].replace("/", "-")).date().isoformat()
    except Exception:
        return str(text or "")[:10]

def _session_state() -> str:
    n = taipei_now(); t = n.time()
    if n.weekday() >= 5: return "CLOSED"
    if t < time(9, 0): return "PRE_MARKET"
    if t <= time(13, 30): return "INTRADAY"
    if t < time(15, 30): return "POST_CLOSE_WAIT"
    if t < time(18, 30): return "OFFICIAL_SYNC_WINDOW"
    return "OFFICIAL_FINAL_CHECK"

def _date_obj(x):
    try:
        return datetime.fromisoformat(str(x)[:10].replace("/", "-")).date()
    except Exception:
        return None

def validate_official_block(block: dict, target_date: str, label: str, max_lag_days: int = 5) -> dict:
    """Official-data guard for the V9-style main dashboard.

    Frontend rule from Tino:
    - If official rows are fetched, show the real rows with their real date.
    - Never print half-state text like "待同步 / 不納入正式分數" on the main dashboard.
    - If official rows are not fetched, keep the block unaccepted so the UI hides it.
    """
    if not isinstance(block, dict):
        return block
    out = dict(block); dd = _date_obj(out.get("date")); td = _date_obj(target_date)
    if not dd or not td:
        out.update({"accepted": False, "fallback": True, "freshness": "date_unknown", "reason": f"{label}日期不可驗證"}); return out
    if dd > td:
        out.update({"accepted": False, "fallback": True, "freshness": "future_error", "reason": f"{label}日期異常"}); return out
    if dd == td:
        out.update({"accepted": bool(out.get("accepted", True)), "fallback": False, "freshness": "today", "reason": out.get("reason") or f"{label}今日已同步"}); return out
    age = (td - dd).days; recent = dd.strftime("%m/%d")
    if age > max_lag_days:
        out.update({"accepted": False, "fallback": True, "freshness": "too_old", "reason": f"{label}資料過舊｜最近有效 {recent}"}); return out
    # Recent official data is still real data. Show it with date, but do not pretend it is today's row.
    out.update({
        "accepted": bool(out.get("accepted", True)),
        "fallback": True,
        "freshness": "recent_valid",
        "reason": f"{label}最近有效 {recent}｜非今日資料",
    })
    return out
