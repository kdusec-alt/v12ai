# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence, Tuple

_INSTALLED = False


def _sort_stamp(row: Dict[str, Any]) -> str:
    return str(
        row.get("audit_time_tw")
        or row.get("prediction_run_time_tw")
        or row.get("run_time_tw")
        or row.get("logged_at_tw")
        or ""
    )


def latest_t1_audit_records(rows: Sequence[Dict[str, Any]], limit: int = 40) -> List[Dict[str, Any]]:
    """Keep one visible T1 audit per ticker and target date.

    Raw audit JSONL remains append-only.  This function is only the Learning
    Center's latest-state projection so repeated intraday predictions do not
    produce repeated visible rows for the same official close.
    """
    ordered = [dict(row) for row in rows or [] if isinstance(row, dict)]
    ordered.sort(key=_sort_stamp, reverse=True)
    selected: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str, str]] = set()
    for row in ordered:
        if str(row.get("target") or "") != "next":
            continue
        ticker = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
        target_date = str(row.get("target_trade_date") or row.get("target_date") or "").strip()
        market = str(row.get("market") or "").strip().upper()
        if ticker and target_date:
            key = (market, ticker, target_date)
        else:
            # Historical malformed rows must not collapse into one another.
            key = ("ROW", str(row.get("audit_id") or row.get("prediction_id") or id(row)), _sort_stamp(row))
        if key in seen:
            continue
        seen.add(key)
        selected.append(row)
        if len(selected) >= max(1, int(limit)):
            break
    return selected


def _install_learning_latest_view() -> None:
    import learning_center_core as core

    if getattr(core, "_v1068_latest_view_installed", False):
        return
    original = core._recent_t1_audits

    def _recent_t1_audits_v1068(audits, limit: int = 40):
        normalized = [core._normalize_audit(row) for row in audits or [] if isinstance(row, dict)]
        return original(latest_t1_audit_records(normalized, limit), limit)

    core._recent_t1_audits = _recent_t1_audits_v1068
    core._v1068_latest_view_installed = True


def _install_fragment_safe_admin_ack() -> None:
    import event_lifecycle as lifecycle

    if getattr(lifecycle, "_v1068_admin_ack_installed", False):
        return
    original = lifecycle.acknowledge_global_event

    def acknowledge_global_event_v1068(event_id: str):
        completed = bool(original(event_id))
        if not completed:
            return False
        # The button is rendered inside Streamlit's five-minute fragment.  A
        # full-app st.rerun from the caller can raise a RuntimeError even though
        # the acknowledgement was already persisted.  Refresh only the active
        # fragment when supported; otherwise return a falsey value so the old
        # full-app rerun branch is skipped.  The next natural fragment render
        # reads the already-updated lifecycle state.
        try:
            import streamlit as st
            st.session_state.pop("event_reassessment_notice", None)
            st.session_state.pop("event_reassessment_notice_severity", None)
            try:
                st.session_state["global_event_view"] = lifecycle.get_global_event_view()
            except Exception:
                pass
            try:
                st.rerun(scope="fragment")
            except (RuntimeError, TypeError):
                pass
        except Exception:
            pass
        return False

    lifecycle.acknowledge_global_event = acknowledge_global_event_v1068
    lifecycle._v1068_admin_ack_installed = True


def research_status_text(close_report: Dict[str, Any], scheduler_report: Dict[str, Any]) -> Dict[str, str]:
    close = dict(close_report or {})
    scheduler = dict(scheduler_report or {})
    status = str(close.get("status") or scheduler.get("status") or "waiting").strip().lower()
    waiting = int(float(close.get("waiting_institution") or 0))
    errors = int(float(close.get("errors") or 0))
    today = int(float(close.get("today_tickers") or 0))
    if errors:
        return {"level": "error", "label": "研究資料異常", "detail": f"目前有 {errors} 筆錯誤待處理；V12 Decision 仍保持隔離。"}
    if status in {"running", "working", "processing"}:
        return {"level": "running", "label": "研究重檢執行中", "detail": f"今日已查 {today} 檔，研究層正在更新。"}
    if waiting:
        return {"level": "waiting", "label": "等待法人／收盤資料", "detail": f"尚有 {waiting} 檔等待正式資料；不是系統故障。"}
    if status in {"done", "written", "completed", "success"}:
        return {"level": "ok", "label": "研究重檢完成", "detail": "研究紀錄已更新，Decision Influence 維持 FALSE。"}
    return {"level": "idle", "label": "等待下一次研究任務", "detail": "目前沒有待處理標的；Research Lab 正常待命，且不影響 V12 Decision。"}


def install_v1068_runtime_patches() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_learning_latest_view()
    _install_fragment_safe_admin_ack()
    _INSTALLED = True
