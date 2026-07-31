# -*- coding: utf-8 -*-
"""Prediction/Audit integrity diagnostics for TINO V1081.

This module never changes a forecast or a learned weight.  It verifies the
append-only chain:
formal prediction -> official target session -> verified actual close -> Audit
-> bounded profile update.  It also records remote-memory sync success/failure
without allowing persistence outages to crash analysis.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import os
import threading
from typing import Any, Dict, Mapping


SCHEMA = "TINO_LEARNING_INTEGRITY_V1081"
_SYNC_LOCK = threading.RLock()
_SYNC_STATE: Dict[str, Any] = {
    "installed": False,
    "last_status": "never_observed",
    "last_path": "",
    "last_error": "",
    "success_count": 0,
    "failure_count": 0,
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _positive_number(value: Any) -> bool:
    try:
        return float(value or 0) > 0
    except Exception:
        return False


def _is_official_prediction(row: Mapping[str, Any]) -> bool:
    return bool(
        row.get("target_kind") == "T1_CLOSE_NEXT_SESSION"
        and row.get("next_close_est") is not None
        and row.get("valid_price_sample") is not False
        and row.get("price_sample_quality") in {"verified", "reference_limited", None}
    )


def _is_verified_t1_audit(row: Mapping[str, Any]) -> bool:
    return bool(
        str(row.get("target") or "").lower() == "next"
        and row.get("actual_valid") is True
        and _positive_number(row.get("predicted_close"))
        and _positive_number(row.get("actual_close"))
        and _positive_number(row.get("anchor_close"))
    )


def _latest_timestamp(rows: list[Mapping[str, Any]], *keys: str) -> str:
    values = []
    for row in rows:
        for key in keys:
            value = _text(row.get(key))
            if value:
                values.append(value)
                break
    return max(values, default="")


def install_persistence_health_guard() -> Dict[str, Any]:
    """Wrap remote sync for observability; preserve the existing fail-safe."""
    try:
        import tino_persistent_store as store
    except Exception as exc:
        with _SYNC_LOCK:
            _SYNC_STATE.update({
                "installed": False,
                "last_status": "module_unavailable",
                "last_error": type(exc).__name__,
            })
        return dict(_SYNC_STATE)
    if getattr(store, "_v1081_sync_health_installed", False):
        with _SYNC_LOCK:
            _SYNC_STATE["installed"] = True
            return dict(_SYNC_STATE)
    original = getattr(store, "_sync_file_to_remote", None)
    if not callable(original):
        with _SYNC_LOCK:
            _SYNC_STATE.update({"installed": False, "last_status": "sync_function_unavailable"})
        return dict(_SYNC_STATE)

    def sync_file_to_remote_v1081(path, *args, **kwargs):
        try:
            result = original(path, *args, **kwargs)
            with _SYNC_LOCK:
                _SYNC_STATE.update({
                    "installed": True,
                    "last_status": "success",
                    "last_path": str(path or ""),
                    "last_error": "",
                    "success_count": int(_SYNC_STATE.get("success_count") or 0) + 1,
                })
            return result
        except Exception as exc:
            with _SYNC_LOCK:
                _SYNC_STATE.update({
                    "installed": True,
                    "last_status": "failed",
                    "last_path": str(path or ""),
                    "last_error": f"{type(exc).__name__}: {exc}",
                    "failure_count": int(_SYNC_STATE.get("failure_count") or 0) + 1,
                })
            raise

    store._sync_file_to_remote = sync_file_to_remote_v1081
    store._v1081_sync_health_installed = True
    with _SYNC_LOCK:
        _SYNC_STATE["installed"] = True
        return dict(_SYNC_STATE)


def remote_sync_health() -> Dict[str, Any]:
    with _SYNC_LOCK:
        output = dict(_SYNC_STATE)
    output["configured"] = os.environ.get("TINO_INLINE_REMOTE_SYNC", "0").strip() == "1"
    return output


def learning_integrity_snapshot(limit: int = 1200) -> Dict[str, Any]:
    """Bounded, read-only consistency check for Admin/Trace."""
    try:
        from memory_store import (
            PREDICTION_LOG, AUDIT_LOG, TICKER_PROFILE,
            read_prediction_log, read_audit_log,
        )
    except Exception as exc:
        return {
            "schema": SCHEMA,
            "status": "DEGRADED",
            "reason": f"memory_store_unavailable:{type(exc).__name__}",
            "decision_influence": False,
        }

    max_rows = max(100, min(int(limit or 1200), 2000))
    predictions = [dict(row) for row in read_prediction_log(max_rows) if isinstance(row, Mapping)]
    audits = [dict(row) for row in read_audit_log(max_rows) if isinstance(row, Mapping)]
    official = [row for row in predictions if _is_official_prediction(row)]
    verified_audits = [row for row in audits if _is_verified_t1_audit(row)]

    official_keys = [
        _text(row.get("official_sample_key"))
        or "|".join((
            _text(row.get("market")), _text(row.get("ticker")),
            _text(row.get("target_trade_date")), "T1_CLOSE_NEXT_SESSION",
        ))
        for row in official
    ]
    audited_keys = {
        _text(row.get("official_sample_key"))
        or "|".join((
            _text(row.get("market")), _text(row.get("ticker")),
            _text(row.get("target_trade_date")), "T1_CLOSE_NEXT_SESSION",
        ))
        for row in verified_audits
    }
    rows_by_key: Dict[str, list[Dict[str, Any]]] = {}
    for row, key in zip(official, official_keys):
        if key:
            rows_by_key.setdefault(key, []).append(row)
    revision_groups = 0
    duplicate_official_keys = 0
    for rows in rows_by_key.values():
        if len(rows) <= 1:
            continue
        if any(
            bool(row.get("event_revision"))
            or bool(row.get("event_bundle_id"))
            or str(row.get("revision_type") or "").strip()
            for row in rows
        ):
            revision_groups += 1
        else:
            duplicate_official_keys += 1
    pending_keys = sorted({key for key in official_keys if key and key not in audited_keys})

    prediction_ids = {_text(row.get("id")) for row in predictions if _text(row.get("id"))}
    orphan_audits = sum(
        1 for row in verified_audits
        if _text(row.get("prediction_id")) and _text(row.get("prediction_id")) not in prediction_ids
    )
    invalid_actual_rows = sum(
        1 for row in audits
        if str(row.get("target") or "").lower() == "next" and row.get("actual_valid") is not True
    )

    files: Dict[str, Dict[str, Any]] = {}
    for name, value in (
        ("prediction_log", PREDICTION_LOG),
        ("audit_log", AUDIT_LOG),
        ("ticker_profile", TICKER_PROFILE),
    ):
        path = Path(value)
        try:
            files[name] = {
                "exists": path.exists(),
                "size_bytes": path.stat().st_size if path.exists() else 0,
                "mtime": path.stat().st_mtime if path.exists() else None,
            }
        except Exception:
            files[name] = {"exists": False, "size_bytes": 0, "mtime": None}

    warnings: list[str] = []
    if duplicate_official_keys:
        warnings.append(f"非事件修正版的正式樣本鍵重複 {duplicate_official_keys} 組")
    if orphan_audits:
        warnings.append(f"Audit找不到目前可見Prediction {orphan_audits} 筆")
    if invalid_actual_rows:
        warnings.append(f"無效正式收盤Audit {invalid_actual_rows} 筆")
    if len(pending_keys) > 40:
        warnings.append(f"待稽核正式樣本偏多 {len(pending_keys)} 筆")

    sync = remote_sync_health()
    if sync.get("configured") and sync.get("last_status") == "failed":
        warnings.append("最近一次遠端Memory同步失敗")

    status = "OK" if not warnings else "WARNING"
    return {
        "schema": SCHEMA,
        "status": status,
        "warnings": warnings,
        "prediction_rows": len(predictions),
        "official_prediction_rows": len(official),
        "verified_t1_audits": len(verified_audits),
        "pending_official_samples": len(pending_keys),
        "pending_sample_keys": pending_keys[:20],
        "duplicate_official_keys": duplicate_official_keys,
        "event_revision_groups": revision_groups,
        "orphan_audits": orphan_audits,
        "invalid_actual_rows": invalid_actual_rows,
        "last_prediction_time": _latest_timestamp(predictions, "run_time_tw"),
        "last_audit_time": _latest_timestamp(audits, "audit_time_tw", "audit_date_tw"),
        "files": files,
        "remote_sync": sync,
        "decision_influence": False,
        "formal_weights_changed": False,
    }


def compact_learning_health(limit: int = 600) -> Dict[str, Any]:
    row = learning_integrity_snapshot(limit)
    if row.get("status") == "DEGRADED":
        return {"level": "warning", "text": "預測學習檢核暫時不可用", "raw": row}
    pending = int(row.get("pending_official_samples") or 0)
    audited = int(row.get("verified_t1_audits") or 0)
    warnings = list(row.get("warnings") or [])
    if warnings:
        return {
            "level": "warning",
            "text": f"預測學習需檢查｜已稽核 {audited}｜待處理 {pending}｜{'；'.join(warnings[:2])}",
            "raw": row,
        }
    return {
        "level": "ok",
        "text": f"預測學習正常｜已稽核 {audited}｜待處理 {pending}｜昨測今收鏈完整",
        "raw": row,
    }


def recommended_audit_batch(pending_count: int, *, hard_cap: int = 2) -> int:
    """Small adaptive batch; never exceeds the Cloud-safe hard cap."""
    pending = max(0, int(pending_count or 0))
    cap = max(1, min(int(hard_cap or 2), 2))
    return cap if pending >= 2 else 1
