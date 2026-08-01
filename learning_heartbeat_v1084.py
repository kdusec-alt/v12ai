# -*- coding: utf-8 -*-
"""Lightweight AI Learning Heartbeat for TINO V1084.1.

The Learning Center intentionally reads only the newest 900 canonical rows for
interactive views.  That bounded window must never be presented as the lifetime
analysis count.  This module scans JSONL files as a streaming scalar operation,
uses remote row metadata when it is larger, and renders a compact health card.
It never changes predictions, audits, DNA, profiles, or formal weights.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import time
from typing import Any, Dict, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

from memory_store import (
    AUDIT_LOG,
    DEFAULT_VISIBLE_LOG_ROWS,
    PREDICTION_LOG,
    memory_diagnostics,
)
from tino_persistent_store import DEFAULT_LEDGER_PATH, storage_status


SCHEMA = "TINO_AI_LEARNING_HEARTBEAT_V1084_1"
TW_TZ = ZoneInfo("Asia/Taipei")
_CACHE: Dict[str, Any] = {"expires_at": 0.0, "value": None}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _parse_dt(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TW_TZ)
        return dt.astimezone(TW_TZ)
    except Exception:
        try:
            dt = datetime.strptime(text[:10], "%Y-%m-%d")
            return dt.replace(tzinfo=TW_TZ)
        except Exception:
            return None


def _row_dt(row: Mapping[str, Any], keys: Iterable[str]) -> Optional[datetime]:
    for key in keys:
        dt = _parse_dt(row.get(key))
        if dt is not None:
            return dt
    return None


def _scan_jsonl(path: Path, timestamp_keys: Iterable[str], *, cap: int = 200_000) -> Dict[str, Any]:
    now = datetime.now(TW_TZ)
    today = now.date()
    yesterday = today - timedelta(days=1)
    cutoff_7 = now - timedelta(days=7)
    cutoff_30 = now - timedelta(days=30)
    result: Dict[str, Any] = {
        "exists": path.exists(),
        "physical_rows": 0,
        "valid_rows": 0,
        "today": 0,
        "yesterday": 0,
        "days_7": 0,
        "days_30": 0,
        "last_time": "",
        "truncated": False,
    }
    if not path.exists() or path.is_dir():
        return result

    last_dt: Optional[datetime] = None
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for index, line in enumerate(handle, start=1):
                result["physical_rows"] = index
                if index >= cap:
                    result["truncated"] = True
                    break
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if not isinstance(row, dict):
                    continue
                result["valid_rows"] += 1
                dt = _row_dt(row, timestamp_keys)
                if dt is None:
                    continue
                if last_dt is None or dt > last_dt:
                    last_dt = dt
                if dt.date() == today:
                    result["today"] += 1
                if dt.date() == yesterday:
                    result["yesterday"] += 1
                if dt >= cutoff_7:
                    result["days_7"] += 1
                if dt >= cutoff_30:
                    result["days_30"] += 1
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    if last_dt is not None:
        result["last_time"] = last_dt.isoformat(timespec="seconds")
    return result


def _remote_file(status: Mapping[str, Any], name: str) -> Dict[str, Any]:
    files = status.get("remote_files") if isinstance(status.get("remote_files"), dict) else {}
    row = files.get(name) if isinstance(files, dict) else None
    return dict(row) if isinstance(row, dict) else {}


def _latest_text(value: Any) -> str:
    dt = _parse_dt(value)
    if dt is None:
        return "--"
    return dt.strftime("%m/%d %H:%M")


def learning_heartbeat_snapshot(*, ttl_seconds: int = 30, force: bool = False) -> Dict[str, Any]:
    now_epoch = time.monotonic()
    if not force and now_epoch < float(_CACHE.get("expires_at") or 0):
        cached = _CACHE.get("value")
        if isinstance(cached, dict):
            return dict(cached)

    prediction = _scan_jsonl(
        Path(PREDICTION_LOG),
        ("run_time_tw", "logged_at_tw", "created_at_tw", "created_at", "timestamp", "run_date_tw"),
    )
    audit = _scan_jsonl(
        Path(AUDIT_LOG),
        ("audit_time_tw", "logged_at_tw", "created_at_tw", "created_at", "timestamp", "audit_date_tw"),
    )
    try:
        diagnostics = memory_diagnostics(DEFAULT_VISIBLE_LOG_ROWS)
    except Exception as exc:
        diagnostics = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        persistent = storage_status(DEFAULT_LEDGER_PATH)
    except Exception as exc:
        persistent = {"last_error": f"{type(exc).__name__}: {exc}"}
    try:
        from learning_integrity_v1081 import learning_integrity_snapshot
        integrity = learning_integrity_snapshot(DEFAULT_VISIBLE_LOG_ROWS)
    except Exception as exc:
        integrity = {"status": "DEGRADED", "reason": f"{type(exc).__name__}: {exc}"}

    remote_prediction = _remote_file(persistent, "prediction_log.jsonl")
    remote_audit = _remote_file(persistent, "audit_log.jsonl")
    local_prediction_rows = _safe_int(prediction.get("physical_rows"))
    local_audit_rows = _safe_int(audit.get("physical_rows"))
    remote_prediction_rows = _safe_int(remote_prediction.get("rows"))
    remote_audit_rows = _safe_int(remote_audit.get("rows"))
    total_predictions = max(local_prediction_rows, remote_prediction_rows)
    total_audits = max(local_audit_rows, remote_audit_rows)

    sync = integrity.get("remote_sync") if isinstance(integrity.get("remote_sync"), dict) else {}
    remote_configured = bool(persistent.get("remote_configured") or sync.get("configured"))
    remote_failed = bool(
        str(sync.get("last_status") or "").lower() == "failed"
        or str(persistent.get("remote_status") or "").upper() in {"FAILED", "ERROR"}
        or persistent.get("remote_error")
    )
    missing_local = not prediction.get("exists") or total_predictions <= 0
    last_prediction_dt = _parse_dt(prediction.get("last_time"))
    age_hours = None
    if last_prediction_dt is not None:
        age_hours = max(0.0, (datetime.now(TW_TZ) - last_prediction_dt).total_seconds() / 3600.0)

    if missing_local:
        health_level = "red"
        health_label = "寫入異常"
        health_detail = "Prediction Log不存在或沒有有效紀錄。"
    elif remote_failed:
        health_level = "red"
        health_label = "遠端同步異常"
        health_detail = "本機仍保留資料，但最近一次長期Memory同步失敗。"
    elif age_hours is not None and age_hours <= 24:
        health_level = "green"
        health_label = "正常寫入"
        health_detail = "最近24小時內已有Prediction寫入。"
    elif age_hours is not None and age_hours <= 72:
        health_level = "yellow"
        health_label = "正常待命"
        health_detail = "近期無新分析；Log與Memory仍可讀取。"
    else:
        health_level = "yellow"
        health_label = "久未新增"
        health_detail = "資料仍存在，但建議重新分析一檔確認寫入心跳。"

    queue = _safe_int(integrity.get("pending_official_samples"))
    last_prediction_time = prediction.get("last_time") or integrity.get("last_prediction_time") or ""
    last_audit_time = audit.get("last_time") or integrity.get("last_audit_time") or ""
    visible_predictions = _safe_int(diagnostics.get("prediction_rows_visible"))
    visible_audits = _safe_int(diagnostics.get("audit_rows_visible"))

    value = {
        "schema": SCHEMA,
        "database_total_predictions": total_predictions,
        "database_total_audits": total_audits,
        "visible_limit": DEFAULT_VISIBLE_LOG_ROWS,
        "visible_predictions": visible_predictions,
        "visible_audits": visible_audits,
        "today_predictions": _safe_int(prediction.get("today")),
        "yesterday_predictions": _safe_int(prediction.get("yesterday")),
        "predictions_7d": _safe_int(prediction.get("days_7")),
        "predictions_30d": _safe_int(prediction.get("days_30")),
        "today_audits": _safe_int(audit.get("today")),
        "yesterday_audits": _safe_int(audit.get("yesterday")),
        "audits_7d": _safe_int(audit.get("days_7")),
        "audits_30d": _safe_int(audit.get("days_30")),
        "learning_queue": queue,
        "last_prediction_time": str(last_prediction_time or ""),
        "last_audit_time": str(last_audit_time or ""),
        "last_prediction_label": _latest_text(last_prediction_time),
        "last_audit_label": _latest_text(last_audit_time),
        "health_level": health_level,
        "health_label": health_label,
        "health_detail": health_detail,
        "remote_configured": remote_configured,
        "remote_prediction_rows": remote_prediction_rows,
        "remote_audit_rows": remote_audit_rows,
        "remote_verified": bool(remote_prediction.get("verified")),
        "remote_status": str(persistent.get("remote_status") or sync.get("last_status") or ""),
        "window_is_capped": total_predictions >= DEFAULT_VISIBLE_LOG_ROWS,
        "decision_influence": False,
        "formal_weights_changed": False,
    }
    _CACHE["value"] = dict(value)
    _CACHE["expires_at"] = now_epoch + max(5, min(int(ttl_seconds or 30), 120))
    return value


def render_learning_heartbeat(st, snapshot: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    row = dict(snapshot or learning_heartbeat_snapshot())
    level = str(row.get("health_level") or "yellow")
    color = {"green": "#25d88a", "yellow": "#ffd96a", "red": "#ff5574"}.get(level, "#ffd96a")
    icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(level, "🟡")
    st.markdown(
        "<div style='border:1px solid rgba(54,230,255,.24);border-left:5px solid " + color + ";"
        "border-radius:12px;background:#071727;padding:9px 12px;margin:8px 0 8px;color:#eef8ff'>"
        "<div style='font-size:16px;font-weight:950;color:#fff5c4'>💓 AI Learning Heartbeat｜"
        + icon + " " + str(row.get("health_label") or "待確認") + "</div>"
        "<div style='margin-top:2px;color:#ccecff;font-size:12px;font-weight:750'>"
        + str(row.get("health_detail") or "") + "</div></div>",
        unsafe_allow_html=True,
    )
    cols = st.columns(6, gap="small")
    metrics = (
        ("資料庫總分析", row.get("database_total_predictions", 0), "Prediction Log實際累積總筆數，不受900筆前台可視窗限制。"),
        ("今日新增", f"+{row.get('today_predictions', 0)}", "今天新增的Prediction紀錄。"),
        ("今日Audit", f"+{row.get('today_audits', 0)}", "今天新增的Audit紀錄。"),
        ("歷史待稽核", row.get("learning_queue", 0), "尚未完成正式收盤比對的正式樣本鍵。"),
        ("最後Prediction", row.get("last_prediction_label", "--"), "最後一次Prediction寫入時間（台北時間）。"),
        ("最後Audit", row.get("last_audit_label", "--"), "最後一次Audit寫入時間（台北時間）。"),
    )
    for column, (label, value, help_text) in zip(cols, metrics):
        with column:
            st.metric(label, value, help=help_text)
    remote_text = (
        f"遠端Prediction {row.get('remote_prediction_rows', 0)}筆"
        if row.get("remote_configured") else "遠端Memory未設定"
    )
    st.caption(
        f"前台可視窗 {row.get('visible_predictions', 0)}/{row.get('visible_limit', DEFAULT_VISIBLE_LOG_ROWS)}｜"
        f"昨日 +{row.get('yesterday_predictions', 0)}｜7日 +{row.get('predictions_7d', 0)}｜"
        f"30日 +{row.get('predictions_30d', 0)}｜{remote_text}｜Decision Influence FALSE"
    )
    return row
