# -*- coding: utf-8 -*-
"""Controlled Taiwan close recheck for V13.

Purpose
-------
After Admin login at/after the Taiwan close-data window, re-analyse only the
Taiwan tickers that already produced a formal Prediction Log row earlier on the
same Taipei calendar day.  The routine is deliberately bounded and fail-safe:

* no universe scan and no watchlist scan;
* no background worker/thread;
* only a small batch per Streamlit rerun;
* one automatic closed snapshot per ticker/day;
* official same-day institutional data is required before the ticker is marked
  complete;
* V12 Decision objects are never mutated.
"""
from __future__ import annotations

from datetime import datetime, time as clock_time, timedelta
from time import perf_counter
from typing import Any, Callable, Dict, List, Mapping
from zoneinfo import ZoneInfo
import gc
import os
import re

from memory_store import read_prediction_log
from .repository import (
    append_close_recheck_event,
    load_close_recheck_state,
    save_close_recheck_state,
)

TW_TZ = ZoneInfo("Asia/Taipei")
_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_DATE_RE = re.compile(r"法人日期[:：]\s*(\d{4}-\d{2}-\d{2})")


def close_recheck_enabled() -> bool:
    raw = str(os.environ.get("TINO_V13_CLOSE_RECHECK", "1") or "1").strip().lower()
    return raw in _TRUE_VALUES


def _int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(str(os.environ.get(name, default)).strip())
    except Exception:
        value = default
    return max(low, min(high, value))


def _float_env(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(str(os.environ.get(name, default)).strip())
    except Exception:
        value = default
    return max(low, min(high, value))


def _cutoff_time() -> clock_time:
    raw = str(os.environ.get("TINO_V13_CLOSE_RECHECK_TIME", "17:00") or "17:00").strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
    if not match:
        return clock_time(17, 0)
    hour = max(0, min(23, int(match.group(1))))
    minute = max(0, min(59, int(match.group(2))))
    return clock_time(hour, minute)


def _now_tw(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(TW_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=TW_TZ)
    return now.astimezone(TW_TZ)


def _is_tw_ticker(ticker: str, market: str = "") -> bool:
    symbol = str(ticker or "").strip().upper()
    mkt = str(market or "").strip().upper()
    return bool(mkt == "TW" or symbol.endswith(".TW") or symbol.endswith(".TWO"))


def _row_date(row: Mapping[str, Any]) -> str:
    value = str(row.get("run_date_tw") or "").strip()
    if value:
        return value[:10]
    return str(row.get("run_time_tw") or "")[:10]


def _row_time(row: Mapping[str, Any]) -> str:
    return str(row.get("run_time_tw") or "")


def _institution_line(row_or_forecast: Any) -> str:
    if isinstance(row_or_forecast, Mapping):
        radar = row_or_forecast.get("radar")
    else:
        radar = getattr(row_or_forecast, "radar", None)
    if isinstance(radar, Mapping):
        return str(radar.get("三大法人") or "")
    return ""


def _institution_date(row_or_forecast: Any) -> str:
    match = _DATE_RE.search(_institution_line(row_or_forecast))
    return match.group(1) if match else ""


def _margin_line(row_or_forecast: Any) -> str:
    if isinstance(row_or_forecast, Mapping):
        radar = row_or_forecast.get("radar")
    else:
        radar = getattr(row_or_forecast, "radar", None)
    if isinstance(radar, Mapping):
        return str(radar.get("資券 / 融資融券") or "")
    return ""


def _material_diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> Dict[str, Any]:
    fields = (
        "session_mode",
        "spot_last",
        "today_close_est",
        "next_close_est",
        "predicted_direction",
        "direction_score",
        "confidence",
    )
    changed: Dict[str, Any] = {}
    for key in fields:
        old = before.get(key)
        new = after.get(key)
        if old != new:
            changed[key] = {"before": old, "after": new}

    old_inst = _institution_line(before)
    new_inst = _institution_line(after)
    if old_inst != new_inst:
        changed["institutional"] = {
            "before_date": _institution_date(before),
            "after_date": _institution_date(after),
        }

    old_margin = _margin_line(before)
    new_margin = _margin_line(after)
    if old_margin != new_margin:
        changed["margin"] = True

    old_factors = before.get("direction_factor_contributions")
    new_factors = after.get("direction_factor_contributions")
    if isinstance(old_factors, Mapping) and isinstance(new_factors, Mapping) and dict(old_factors) != dict(new_factors):
        changed["direction_factor_contributions"] = True
    return changed


def _compact_close_snapshot(row: Mapping[str, Any]) -> Dict[str, Any]:
    factors = row.get("direction_factor_contributions")
    return {
        "id": row.get("id"),
        "run_time_tw": row.get("run_time_tw"),
        "session_mode": row.get("session_mode"),
        "spot_last": row.get("spot_last"),
        "today_close_est": row.get("today_close_est"),
        "next_close_est": row.get("next_close_est"),
        "predicted_direction": row.get("predicted_direction"),
        "direction_score": row.get("direction_score"),
        "confidence": row.get("confidence"),
        "institution_date": _institution_date(row),
        "institutional": _institution_line(row),
        "margin": _margin_line(row),
        "direction_factor_contributions": dict(factors) if isinstance(factors, Mapping) else {},
    }


def _today_rows(limit: int, today: str) -> List[Dict[str, Any]]:
    rows = []
    for row in read_prediction_log(limit):
        if not isinstance(row, dict):
            continue
        if _row_date(row) != today:
            continue
        if not _is_tw_ticker(str(row.get("ticker") or ""), str(row.get("market") or "")):
            continue
        rows.append(row)
    rows.sort(key=_row_time)
    return rows


def build_close_recheck_plan(
    *,
    now: datetime | None = None,
    limit: int = 1800,
    max_tickers: int = 30,
) -> Dict[str, Any]:
    current = _now_tw(now)
    today = current.date().isoformat()
    rows = _today_rows(limit, today)
    state = load_close_recheck_state()
    state_day = str(state.get("trade_date") or "")
    completed = set(str(x).upper() for x in (state.get("completed_tickers") or [])) if state_day == today else set()

    latest_by_ticker: Dict[str, Dict[str, Any]] = {}
    has_closed: set[str] = set()
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        latest_by_ticker[ticker] = row
        if str(row.get("session_mode") or "").lower() == "closed" and _institution_date(row) == today:
            has_closed.add(ticker)

    candidates = [
        ticker
        for ticker, row in sorted(latest_by_ticker.items(), key=lambda item: _row_time(item[1]))
        if ticker not in completed and ticker not in has_closed
    ][: max(1, int(max_tickers))]

    return {
        "trade_date": today,
        "eligible": current.time() >= _cutoff_time(),
        "cutoff": _cutoff_time().strftime("%H:%M"),
        "candidate_tickers": candidates,
        "latest_by_ticker": latest_by_ticker,
        "completed_tickers": sorted(completed | has_closed),
        "today_prediction_rows": len(rows),
        "today_unique_tickers": len(latest_by_ticker),
    }


def _safe_call_capture(capture: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None, row: Mapping[str, Any]) -> Dict[str, Any]:
    if capture is None:
        return {"status": "not_configured"}
    try:
        value = capture(row)
        return dict(value) if isinstance(value, Mapping) else {"status": "unknown"}
    except Exception as exc:
        return {"status": "degraded", "reason": f"{type(exc).__name__}: {exc}"}


def run_login_close_recheck(
    st: Any,
    *,
    analyzer: Callable[[str, str, bool], Any],
    snapshot_builder: Callable[[Any, str, bool], Mapping[str, Any]],
    log_writer: Callable[..., Mapping[str, Any]],
    research_capture: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None,
    macro: str,
    live_data: bool,
    now: datetime | None = None,
) -> Dict[str, Any]:
    """Run one bounded close-recheck cycle and optionally request another rerun.

    The caller should invoke this only from the authenticated Admin path.  The
    function handles at most ``TINO_V13_CLOSE_RECHECK_BATCH`` tickers or the
    configured time budget, then asks Streamlit for another rerun until the
    queue is drained.  Failures/stale institutional rows stop the loop and are
    retried on the next login instead of spinning.
    """
    started = perf_counter()
    current = _now_tw(now)
    today = current.date().isoformat()

    if not close_recheck_enabled():
        return {"status": "disabled", "trade_date": today}
    if not bool(getattr(st, "session_state", {}).get("admin_authenticated", False)):
        return {"status": "locked", "trade_date": today}
    if not bool(live_data):
        return {"status": "skipped", "reason": "live_data_disabled", "trade_date": today}
    if current.time() < _cutoff_time():
        return {"status": "waiting", "trade_date": today, "cutoff": _cutoff_time().strftime("%H:%M")}
    halted_until_raw = str(st.session_state.get("close_recheck_halted_until") or "")
    if halted_until_raw:
        try:
            halted_until = datetime.fromisoformat(halted_until_raw)
            if halted_until.tzinfo is None:
                halted_until = halted_until.replace(tzinfo=TW_TZ)
            if current < halted_until.astimezone(TW_TZ):
                return dict(st.session_state.get("last_close_recheck_report") or {
                    "status": "cooldown",
                    "trade_date": today,
                    "retry_after_tw": halted_until.astimezone(TW_TZ).isoformat(timespec="minutes"),
                })
        except Exception:
            st.session_state.pop("close_recheck_halted_until", None)

    max_tickers = _int_env("TINO_V13_CLOSE_RECHECK_MAX_TICKERS", 30, 1, 80)
    batch_size = _int_env("TINO_V13_CLOSE_RECHECK_BATCH", 4, 1, 12)
    time_budget = _float_env("TINO_V13_CLOSE_RECHECK_BUDGET_SEC", 35.0, 5.0, 90.0)
    plan = build_close_recheck_plan(now=current, max_tickers=max_tickers)
    candidates: List[str] = list(plan.get("candidate_tickers") or [])
    latest_by_ticker: Dict[str, Dict[str, Any]] = dict(plan.get("latest_by_ticker") or {})

    if not candidates:
        saved = load_close_recheck_state()
        same_day = str(saved.get("trade_date") or "") == today
        report = {
            "status": "complete",
            "trade_date": today,
            "today_tickers": int(plan.get("today_unique_tickers") or 0),
            "updated": int(saved.get("updated") or 0) if same_day else 0,
            "formal_written": int(saved.get("formal_written") or 0) if same_day else 0,
            "context_updated": int(saved.get("context_updated") or 0) if same_day else 0,
            "unchanged": int(saved.get("unchanged") or 0) if same_day else 0,
            "waiting_institution": int(saved.get("waiting_institution") or 0) if same_day else 0,
            "errors": int(saved.get("errors") or 0) if same_day else 0,
            "remaining": 0,
            "last_run_time_tw": saved.get("last_run_time_tw") if same_day else "",
            "total_ms": round((perf_counter() - started) * 1000.0, 3),
        }
        st.session_state["last_close_recheck_report"] = report
        return report

    state = load_close_recheck_state()
    if str(state.get("trade_date") or "") != today:
        state = {
            "trade_date": today,
            "completed_tickers": [],
            "updated": 0,
            "formal_written": 0,
            "context_updated": 0,
            "unchanged": 0,
            "waiting_institution": 0,
            "errors": 0,
            "waiting_tickers": [],
            "error_tickers": [],
            "last_run_time_tw": "",
        }
    completed = set(str(x).upper() for x in (state.get("completed_tickers") or []))
    waiting_tickers = set(str(x).upper() for x in (state.get("waiting_tickers") or []))
    error_tickers = set(str(x).upper() for x in (state.get("error_tickers") or []))

    updated = formal_written = context_updated = unchanged = waiting = errors = 0
    processed: List[Dict[str, Any]] = []
    halted = False

    for ticker in candidates[:batch_size]:
        if perf_counter() - started >= time_budget:
            break
        baseline = dict(latest_by_ticker.get(ticker) or {})
        item_started = perf_counter()
        forecast = None
        try:
            forecast = analyzer(ticker, macro, live_data)
            if forecast is None or bool(getattr(forecast, "stopped", False)):
                raise RuntimeError(str(getattr(forecast, "stop_reason", "stopped_or_invalid_forecast")))

            inst_date = _institution_date(forecast)
            if inst_date != today:
                waiting += 1
                waiting_tickers.add(ticker)
                error_tickers.discard(ticker)
                processed.append({
                    "ticker": ticker,
                    "status": "waiting_institution",
                    "institution_date": inst_date,
                    "elapsed_ms": round((perf_counter() - item_started) * 1000.0, 3),
                })
                # Do not spin automatically while Yahoo/FinMind still exposes
                # the previous row.  A later login can retry safely.
                halted = True
                continue

            preview = dict(snapshot_builder(forecast, macro, live_data) or {})
            if str(preview.get("session_mode") or "").lower() != "closed":
                waiting += 1
                waiting_tickers.add(ticker)
                error_tickers.discard(ticker)
                processed.append({
                    "ticker": ticker,
                    "status": "waiting_closed_session",
                    "institution_date": inst_date,
                    "session_mode": preview.get("session_mode"),
                    "elapsed_ms": round((perf_counter() - item_started) * 1000.0, 3),
                })
                halted = True
                continue

            diff = _material_diff(baseline, preview)
            if not diff:
                unchanged += 1
                completed.add(ticker)
                waiting_tickers.discard(ticker)
                error_tickers.discard(ticker)
                append_close_recheck_event({
                    "event_id": f"{today}:{ticker}:unchanged",
                    "trade_date": today,
                    "run_time_tw": current.isoformat(timespec="seconds"),
                    "ticker": ticker,
                    "status": "unchanged",
                    "baseline_prediction_id": baseline.get("id"),
                    "baseline_run_time_tw": baseline.get("run_time_tw"),
                    "baseline_session_mode": baseline.get("session_mode"),
                    "institution_date": inst_date,
                    "changes": {},
                    "elapsed_ms": round((perf_counter() - item_started) * 1000.0, 3),
                })
                processed.append({"ticker": ticker, "status": "unchanged", "institution_date": inst_date})
                continue

            preview_id = str(preview.get("id") or "")
            baseline_id = str(baseline.get("id") or "")
            if preview_id and baseline_id and preview_id == baseline_id:
                # The formal decision is identical, while official close/chip
                # context changed.  Preserve the comparison in V13 without
                # duplicating one T1 training sample in Prediction Log.
                updated += 1
                context_updated += 1
                completed.add(ticker)
                waiting_tickers.discard(ticker)
                error_tickers.discard(ticker)
                append_close_recheck_event({
                    "event_id": f"{today}:{ticker}:context:{preview_id}",
                    "trade_date": today,
                    "run_time_tw": current.isoformat(timespec="seconds"),
                    "ticker": ticker,
                    "status": "context_updated",
                    "baseline_prediction_id": baseline_id,
                    "baseline_run_time_tw": baseline.get("run_time_tw"),
                    "baseline_session_mode": baseline.get("session_mode"),
                    "closed_prediction_id": preview_id,
                    "institution_date": inst_date,
                    "session_mode": preview.get("session_mode"),
                    "changes": diff,
                    "close_snapshot": _compact_close_snapshot(preview),
                    "research_status": "not_rewritten_same_prediction",
                    "elapsed_ms": round((perf_counter() - item_started) * 1000.0, 3),
                })
                processed.append({
                    "ticker": ticker,
                    "status": "context_updated",
                    "institution_date": inst_date,
                    "prediction_id": preview_id,
                    "research_status": "not_rewritten_same_prediction",
                })
                continue

            logged = dict(log_writer(forecast, macro=macro, live_data=live_data) or {})
            if logged.get("skipped"):
                raise RuntimeError(str(logged.get("reason") or "prediction_write_skipped"))
            research = _safe_call_capture(research_capture, logged)
            updated += 1
            formal_written += 1
            completed.add(ticker)
            waiting_tickers.discard(ticker)
            error_tickers.discard(ticker)
            append_close_recheck_event({
                "event_id": f"{today}:{ticker}:{logged.get('id') or 'written'}",
                "trade_date": today,
                "run_time_tw": current.isoformat(timespec="seconds"),
                "ticker": ticker,
                "status": "prediction_written",
                "baseline_prediction_id": baseline.get("id"),
                "baseline_run_time_tw": baseline.get("run_time_tw"),
                "baseline_session_mode": baseline.get("session_mode"),
                "closed_prediction_id": logged.get("id"),
                "institution_date": inst_date,
                "session_mode": logged.get("session_mode"),
                "changes": diff,
                "close_snapshot": _compact_close_snapshot(preview),
                "research_status": research.get("status"),
                "elapsed_ms": round((perf_counter() - item_started) * 1000.0, 3),
            })
            processed.append({
                "ticker": ticker,
                "status": "prediction_written",
                "institution_date": inst_date,
                "prediction_id": logged.get("id"),
                "research_status": research.get("status"),
            })
        except Exception as exc:
            errors += 1
            error_tickers.add(ticker)
            waiting_tickers.discard(ticker)
            processed.append({
                "ticker": ticker,
                "status": "error",
                "reason": f"{type(exc).__name__}: {exc}",
                "elapsed_ms": round((perf_counter() - item_started) * 1000.0, 3),
            })
            halted = True
        finally:
            # Full forecasts can retain sizable price/news object graphs.  Drop
            # each one before the next ticker so batch rechecks do not stack
            # memory on Streamlit Community Cloud.
            forecast = None
            gc.collect()

    state["completed_tickers"] = sorted(completed)
    state["updated"] = int(state.get("updated") or 0) + updated
    state["formal_written"] = int(state.get("formal_written") or 0) + formal_written
    state["context_updated"] = int(state.get("context_updated") or 0) + context_updated
    state["unchanged"] = int(state.get("unchanged") or 0) + unchanged
    state["waiting_tickers"] = sorted(waiting_tickers)
    state["error_tickers"] = sorted(error_tickers)
    state["waiting_institution"] = len(waiting_tickers)
    state["errors"] = len(error_tickers)
    state["last_run_time_tw"] = current.isoformat(timespec="seconds")
    save_close_recheck_state(state)

    next_plan = build_close_recheck_plan(now=current, max_tickers=max_tickers)
    remaining = len(next_plan.get("candidate_tickers") or [])
    status = "complete" if remaining == 0 else "partial"
    state["status"] = status
    state["remaining"] = remaining
    state["today_tickers"] = int(plan.get("today_unique_tickers") or 0)
    save_close_recheck_state(state)
    report = {
        "status": status,
        "trade_date": today,
        "today_tickers": int(plan.get("today_unique_tickers") or 0),
        "processed": len(processed),
        "updated": int(state.get("updated") or 0),
        "formal_written": int(state.get("formal_written") or 0),
        "context_updated": int(state.get("context_updated") or 0),
        "unchanged": int(state.get("unchanged") or 0),
        "waiting_institution": int(state.get("waiting_institution") or 0),
        "errors": int(state.get("errors") or 0),
        "batch_updated": updated,
        "batch_formal_written": formal_written,
        "batch_context_updated": context_updated,
        "batch_unchanged": unchanged,
        "batch_waiting_institution": waiting,
        "batch_errors": errors,
        "remaining": remaining,
        "items": processed,
        "total_ms": round((perf_counter() - started) * 1000.0, 3),
        "research_only": True,
        "decision_influence": False,
    }
    st.session_state["last_close_recheck_report"] = report

    if halted:
        retry_after = current + timedelta(minutes=_int_env("TINO_V13_CLOSE_RECHECK_RETRY_MIN", 15, 5, 120))
        report["retry_after_tw"] = retry_after.isoformat(timespec="minutes")
        st.session_state["last_close_recheck_report"] = report
        st.session_state["close_recheck_halted_until"] = retry_after.isoformat()
        return report

    st.session_state.pop("close_recheck_halted_until", None)
    if remaining > 0:
        st.session_state["close_recheck_in_progress"] = True
        st.rerun()

    st.session_state["close_recheck_in_progress"] = False
    return report
