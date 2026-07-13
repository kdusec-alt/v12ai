# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
import threading
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

_TW = ZoneInfo("Asia/Taipei")
_NY = ZoneInfo("America/New_York")
_LOCK = threading.Lock()
_STATUS: Dict[str, Dict[str, Any]] = {}


def _now_tw(now: Optional[datetime] = None) -> datetime:
    if now is None:
        return datetime.now(_TW)
    if now.tzinfo is None:
        return now.replace(tzinfo=_TW)
    return now.astimezone(_TW)


def _market_window(market: str, now_tw: datetime) -> Dict[str, Any]:
    market = str(market or "").upper()
    if market == "TW":
        ready = now_tw.weekday() < 5 and (now_tw.hour > 14 or (now_tw.hour == 14 and now_tw.minute >= 10))
        return {"ready": ready, "trade_date": now_tw.date().isoformat(), "reason": "台股收盤後" if ready else "台股尚未進入收盤稽核時段"}

    now_ny = now_tw.astimezone(_NY)
    # Official daily close is normally stable after 16:15 New York time.
    ready = now_ny.weekday() < 5 and (now_ny.hour > 16 or (now_ny.hour == 16 and now_ny.minute >= 15))
    return {"ready": ready, "trade_date": now_ny.date().isoformat(), "reason": "美股收盤後" if ready else "美股尚未進入收盤稽核時段"}


def maybe_run_auto_audit_time_guard(
    now: Optional[datetime] = None,
    *,
    markets: Optional[List[str]] = None,
    execute: Optional[bool] = None,
) -> Dict[str, Any]:
    """Return a lightweight readiness report.

    The main Streamlit render path should call this with ``execute=False`` only.
    Actual network/audit work is available through the Admin Learning Center and
    is bounded by a small ticker batch.
    """
    now_tw = _now_tw(now)
    selected = [str(m).upper() for m in (markets or ["TW", "US"]) if str(m).upper() in {"TW", "US"}]
    report = {
        "status": "ready_admin_only",
        "mode": "lightweight_guard",
        "execute": False,
        "attempt_at_tw": now_tw.isoformat(timespec="seconds"),
        "markets": {m: _market_window(m, now_tw) for m in selected},
        "reason": "主畫面不執行；僅在 Admin Learning Center 小批次執行",
    }
    if execute:
        return execute_due_auto_audit_once(now_tw, markets=selected)
    return report


def execute_due_auto_audit_once(
    now: Optional[datetime] = None,
    *,
    markets: Optional[List[str]] = None,
    max_tickers_per_market: int = 5,
    scan_limit: int = 800,
    apply_safe_learning: bool = True,
    actual_foreign_billion: Optional[float] = None,
) -> Dict[str, Any]:
    """Run one controlled, duplicate-safe Auto Audit batch.

    No worker/thread is started.  The call is synchronous, Admin-triggered and
    guarded by a process lock so Streamlit reruns cannot overlap two batches.
    """
    now_tw = _now_tw(now)
    selected = [str(m).upper() for m in (markets or ["TW", "US"]) if str(m).upper() in {"TW", "US"}]
    # Bound every automatic scan. Prediction DNA rows can be large, so the
    # Admin-login path deliberately keeps only a small JSONL tail in memory.
    try:
        bounded_scan_limit = max(50, min(int(scan_limit), 1200))
    except Exception:
        bounded_scan_limit = 300
    if not _LOCK.acquire(blocking=False):
        return {
            "status": "busy",
            "attempt_at_tw": now_tw.isoformat(timespec="seconds"),
            "reason": "另一批 Auto Audit 正在執行",
            "markets": dict(_STATUS),
        }

    results: Dict[str, Dict[str, Any]] = {}
    try:
        from learning import auto_audit_queried_predictions, pending_auto_audit_summary

        for market in selected:
            window = _market_window(market, now_tw)
            pending = pending_auto_audit_summary(
                limit=bounded_scan_limit, market_filter=market, trade_date=str(window.get("trade_date") or "")
            )
            base = {
                "market": market,
                "trade_date": window.get("trade_date"),
                "attempt_at_tw": now_tw.isoformat(timespec="seconds"),
                "pending_t1": pending.get("pending_t1_count", 0),
                "pending_today": pending.get("pending_today_count", 0),
            }
            if not window.get("ready"):
                base.update({"status": "not_due", "reason": window.get("reason")})
                results[market] = base
                _STATUS[market] = dict(base)
                continue
            if not pending.get("pending_t1_count") and not pending.get("pending_today_count"):
                base.update({"status": "nothing_pending", "reason": "目前沒有待稽核正式樣本", "audited_t1": 0, "audited_today": 0})
                results[market] = base
                _STATUS[market] = dict(base)
                continue

            try:
                run = auto_audit_queried_predictions(
                    limit=bounded_scan_limit,
                    max_tickers=max(1, min(int(max_tickers_per_market), 8)),
                    apply_safe_learning=bool(apply_safe_learning),
                    actual_foreign_billion=(actual_foreign_billion if market == "TW" else None),
                    market_filter=market,
                    trade_date=str(window.get("trade_date") or ""),
                )
                base.update({
                    "status": "done",
                    "audited_t1": int(run.get("audited_t1_count") or 0),
                    "audited_today": int(run.get("audited_today_count") or 0),
                    "fetched": int(run.get("fetched_ticker_count") or 0),
                    "errors": len(run.get("errors") or []),
                    "scan_limit": bounded_scan_limit,
                    "reason": "安全小批次完成",
                })
            except Exception as exc:
                base.update({
                    "status": "failed_safe",
                    "reason": f"{type(exc).__name__}: {exc}",
                    "audited_t1": 0,
                    "audited_today": 0,
                })
            results[market] = base
            _STATUS[market] = dict(base)

        return {
            "status": "done",
            "attempt_at_tw": now_tw.isoformat(timespec="seconds"),
            "reason": "Admin controlled bounded batch",
            "scan_limit": bounded_scan_limit,
            "markets": results,
        }
    finally:
        _LOCK.release()


def auto_audit_status_rows() -> List[Dict[str, Any]]:
    if not _STATUS:
        guard = maybe_run_auto_audit_time_guard(execute=False)
        rows = []
        for market, meta in (guard.get("markets") or {}).items():
            rows.append({
                "market": market,
                "trade_date": meta.get("trade_date"),
                "status": "READY_ADMIN" if meta.get("ready") else "WAIT_CLOSE",
                "attempt_at_tw": guard.get("attempt_at_tw"),
                "pending_t1": None,
                "pending_today": None,
                "audited_t1": None,
                "audited_today": None,
                "reason": meta.get("reason"),
            })
        return rows

    rows: List[Dict[str, Any]] = []
    for market in ("TW", "US"):
        meta = _STATUS.get(market)
        if not meta:
            continue
        rows.append({
            "market": market,
            "trade_date": meta.get("trade_date"),
            "status": meta.get("status"),
            "attempt_at_tw": meta.get("attempt_at_tw"),
            "pending_t1": meta.get("pending_t1"),
            "pending_today": meta.get("pending_today"),
            "audited_t1": meta.get("audited_t1"),
            "audited_today": meta.get("audited_today"),
            "reason": meta.get("reason"),
        })
    return rows
