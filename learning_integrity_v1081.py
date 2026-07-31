# -*- coding: utf-8 -*-
"""Prediction/Audit integrity diagnostics for TINO V1084.

This module never changes a forecast or a learned weight.  It verifies the
append-only chain:
formal prediction -> official target session -> verified actual close -> Audit
-> bounded profile update.  It also records the real result of remote-memory
sync without allowing persistence outages to crash analysis.

V1084 additionally distinguishes normal query revisions from corrupt duplicate
rows, reconciles Audit links by official sample key, and quarantines historical
US T1 rows whose target date incorrectly points to the same market session.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import threading
from typing import Any, Dict, Mapping
from zoneinfo import ZoneInfo


SCHEMA = "TINO_LEARNING_INTEGRITY_V1084"
_NY = ZoneInfo("America/New_York")
_TW = ZoneInfo("Asia/Taipei")
_SYNC_LOCK = threading.RLock()
_SYNC_STATE: Dict[str, Any] = {
    "installed": False,
    "last_status": "never_observed",
    "last_path": "",
    "last_error": "",
    "last_checked_at": "",
    "last_success_at": "",
    "last_failure_at": "",
    "success_count": 0,
    "failure_count": 0,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _positive_number(value: Any) -> bool:
    try:
        return float(value or 0) > 0
    except Exception:
        return False


def _official_key(row: Mapping[str, Any]) -> str:
    explicit = _text(row.get("official_sample_key"))
    if explicit:
        return explicit
    return "|".join((
        _text(row.get("market")).upper(),
        _text(row.get("ticker")).upper(),
        _text(row.get("target_trade_date"))[:10],
        _text(row.get("target_kind")) or "T1_CLOSE_NEXT_SESSION",
    ))


def _parse_datetime(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=_TW)
    except Exception:
        return None


def _prediction_market_run_date(row: Mapping[str, Any]) -> str:
    market = _text(row.get("market")).upper()
    parsed = _parse_datetime(row.get("run_time_tw"))
    if parsed is not None:
        zone = _NY if market == "US" else _TW
        return parsed.astimezone(zone).date().isoformat()
    return _text(row.get("run_date_tw"))[:10]


def _invalid_next_session_target(row: Mapping[str, Any]) -> bool:
    if _text(row.get("market")).upper() != "US":
        return False
    if _text(row.get("target_kind")) != "T1_CLOSE_NEXT_SESSION":
        return False
    target = _text(row.get("target_trade_date"))[:10]
    run_date = _prediction_market_run_date(row)
    return bool(target and run_date and target <= run_date)


def _is_official_prediction(row: Mapping[str, Any]) -> bool:
    return bool(
        row.get("target_kind") == "T1_CLOSE_NEXT_SESSION"
        and row.get("next_close_est") is not None
        and row.get("valid_price_sample") is not False
        and row.get("price_sample_quality") in {"verified", "reference_limited", None}
    )


def _is_formal_t1_audit(row: Mapping[str, Any]) -> bool:
    return bool(
        str(row.get("target") or "").lower() == "next"
        and row.get("actual_valid") is True
        and _positive_number(row.get("predicted_close"))
        and _positive_number(row.get("actual_close"))
        and _positive_number(row.get("anchor_close"))
    )


def _is_learning_eligible_t1_audit(row: Mapping[str, Any]) -> bool:
    return bool(
        _is_formal_t1_audit(row)
        and row.get("price_sample_quality") == "verified"
        and str(row.get("actual_direction") or "") in {"UP", "DOWN", "NEUTRAL"}
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


def _physical_line_count(path: Path, cap: int = 200_000) -> int:
    count = 0
    try:
        if not path.exists() or path.is_dir():
            return 0
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for count, _ in enumerate(handle, start=1):
                if count >= cap:
                    break
    except Exception:
        return 0
    return count


def _interpret_sync_result(result: Any) -> tuple[bool, str]:
    """Normalize the persistent-store contract without assuming exceptions.

    ``_sync_file_to_remote`` returns ``(ok, error)`` on normal failures.  A
    wrapper that treats any return as success creates a false-green health card.
    """
    if isinstance(result, tuple):
        ok = bool(result[0]) if result else False
        error = _text(result[1]) if len(result) > 1 else ""
        return ok, error
    if isinstance(result, Mapping):
        status = _text(result.get("status")).upper()
        ok_value = result.get("ok")
        if ok_value is not None:
            return bool(ok_value), _text(result.get("error") or result.get("reason"))
        return status in {"OK", "PASS", "SUCCESS", "DONE"}, _text(
            result.get("error") or result.get("reason")
        )
    if isinstance(result, bool):
        return result, "" if result else "sync_returned_false"
    if result is None:
        return False, "sync_returned_none"
    return True, ""


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
                "last_checked_at": _now_iso(),
            })
        return dict(_SYNC_STATE)
    if getattr(store, "_v1081_sync_health_installed", False):
        with _SYNC_LOCK:
            _SYNC_STATE["installed"] = True
            return dict(_SYNC_STATE)
    original = getattr(store, "_sync_file_to_remote", None)
    if not callable(original):
        with _SYNC_LOCK:
            _SYNC_STATE.update({
                "installed": False,
                "last_status": "sync_function_unavailable",
                "last_checked_at": _now_iso(),
            })
        return dict(_SYNC_STATE)

    def sync_file_to_remote_v1081(path, *args, **kwargs):
        checked_at = _now_iso()
        try:
            result = original(path, *args, **kwargs)
            ok, error = _interpret_sync_result(result)
            with _SYNC_LOCK:
                _SYNC_STATE.update({
                    "installed": True,
                    "last_status": "success" if ok else "failed",
                    "last_path": str(path or ""),
                    "last_error": "" if ok else (error or "remote_sync_failed"),
                    "last_checked_at": checked_at,
                })
                if ok:
                    _SYNC_STATE["success_count"] = int(_SYNC_STATE.get("success_count") or 0) + 1
                    _SYNC_STATE["last_success_at"] = checked_at
                else:
                    _SYNC_STATE["failure_count"] = int(_SYNC_STATE.get("failure_count") or 0) + 1
                    _SYNC_STATE["last_failure_at"] = checked_at
            return result
        except Exception as exc:
            with _SYNC_LOCK:
                _SYNC_STATE.update({
                    "installed": True,
                    "last_status": "failed",
                    "last_path": str(path or ""),
                    "last_error": f"{type(exc).__name__}: {exc}",
                    "last_checked_at": checked_at,
                    "last_failure_at": checked_at,
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
            DEFAULT_VISIBLE_LOG_ROWS,
            PREDICTION_LOG,
            AUDIT_LOG,
            TICKER_PROFILE,
            read_prediction_log,
            read_audit_log,
        )
    except Exception as exc:
        return {
            "schema": SCHEMA,
            "status": "DEGRADED",
            "reason": f"memory_store_unavailable:{type(exc).__name__}",
            "decision_influence": False,
        }

    requested = max(100, min(int(limit or 1200), 2000))
    max_rows = max(int(DEFAULT_VISIBLE_LOG_ROWS or 900), requested)
    max_rows = min(max_rows, 2000)
    predictions = [dict(row) for row in read_prediction_log(max_rows) if isinstance(row, Mapping)]
    audits = [dict(row) for row in read_audit_log(max_rows) if isinstance(row, Mapping)]

    official_all = [row for row in predictions if _is_official_prediction(row)]
    official_all_keys = {_official_key(row) for row in official_all if _official_key(row)}
    invalid_target_rows = [row for row in official_all if _invalid_next_session_target(row)]
    invalid_target_keys = {_official_key(row) for row in invalid_target_rows if _official_key(row)}
    official = [row for row in official_all if _official_key(row) not in invalid_target_keys]

    formal_audits_all = [row for row in audits if _is_formal_t1_audit(row)]
    quarantined_target_audits = [
        row for row in formal_audits_all if _official_key(row) in invalid_target_keys
    ]
    formal_audits = [
        row for row in formal_audits_all if _official_key(row) not in invalid_target_keys
    ]
    learning_audits = [row for row in formal_audits if _is_learning_eligible_t1_audit(row)]

    official_keys = [_official_key(row) for row in official if _official_key(row)]
    audited_keys = {_official_key(row) for row in formal_audits if _official_key(row)}
    rows_by_key: Dict[str, list[Dict[str, Any]]] = {}
    for row, key in zip(official, official_keys):
        rows_by_key.setdefault(key, []).append(row)

    event_revision_groups = 0
    query_revision_groups = 0
    duplicate_official_keys = 0
    for rows in rows_by_key.values():
        if len(rows) <= 1:
            continue
        ids = [_text(row.get("id")) for row in rows]
        if not all(ids) or len(set(ids)) != len(ids):
            duplicate_official_keys += 1
        elif any(
            bool(row.get("event_revision"))
            or bool(row.get("event_bundle_id"))
            or _text(row.get("revision_type")).upper().startswith("EVENT")
            for row in rows
        ):
            event_revision_groups += 1
        else:
            # Normal Analyze refreshes share one official sample key and are
            # intentionally collapsed to the latest row by Auto Audit.
            query_revision_groups += 1

    pending_keys = sorted({key for key in official_keys if key not in audited_keys})

    prediction_ids = {_text(row.get("id")) for row in predictions if _text(row.get("id"))}
    revision_link_audits = 0
    unresolved_links = 0
    for row in formal_audits:
        prediction_id = _text(row.get("prediction_id"))
        if not prediction_id or prediction_id in prediction_ids:
            continue
        if _official_key(row) in official_all_keys:
            revision_link_audits += 1
        else:
            unresolved_links += 1

    prediction_physical_rows = _physical_line_count(Path(PREDICTION_LOG))
    prediction_window_truncated = prediction_physical_rows > max_rows
    audit_links_outside_window = unresolved_links if prediction_window_truncated else 0
    orphan_audits = 0 if prediction_window_truncated else unresolved_links

    invalid_actual_rows = sum(
        1 for row in audits
        if str(row.get("target") or "").lower() == "next" and row.get("actual_valid") is not True
    )
    limited_audits = sum(
        1 for row in formal_audits if row.get("price_sample_quality") != "verified"
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
                "physical_rows": _physical_line_count(path) if path.suffix == ".jsonl" else None,
            }
        except Exception:
            files[name] = {"exists": False, "size_bytes": 0, "mtime": None, "physical_rows": None}

    warnings: list[str] = []
    if duplicate_official_keys:
        warnings.append(f"正式樣本存在相同ID或缺ID重複 {duplicate_official_keys} 組")
    if orphan_audits:
        warnings.append(f"Audit無法由Prediction ID或正式樣本鍵對回 {orphan_audits} 筆")
    if invalid_actual_rows:
        warnings.append(f"無效正式收盤Audit {invalid_actual_rows} 筆")
    if invalid_target_keys:
        warnings.append(
            f"美股T1目標日錯置 {len(invalid_target_keys)} 組，已隔離"
            f" Prediction {len(invalid_target_rows)}／Audit {len(quarantined_target_audits)}"
        )
    if len(pending_keys) > 40:
        warnings.append(f"歷史待稽核正式樣本偏多 {len(pending_keys)} 筆")

    sync = remote_sync_health()
    if sync.get("configured") and sync.get("last_status") == "failed":
        warnings.append("最近一次遠端Memory同步失敗")
    if sync.get("configured") and sync.get("last_status") == "never_observed":
        warnings.append("遠端Memory同步尚未觀測到結果")

    status = "OK" if not warnings else "WARNING"
    return {
        "schema": SCHEMA,
        "status": status,
        "warnings": warnings,
        "scan_limit": max_rows,
        "prediction_rows": len(predictions),
        "official_prediction_rows": len(official),
        "official_prediction_rows_total": len(official_all),
        "formal_t1_audits": len(formal_audits),
        "formal_t1_audits_total": len(formal_audits_all),
        "learning_eligible_t1_audits": len(learning_audits),
        "verified_t1_audits": len(formal_audits),
        "reference_limited_audits": limited_audits,
        "pending_official_samples": len(pending_keys),
        "pending_sample_keys": pending_keys[:20],
        "duplicate_official_keys": duplicate_official_keys,
        "event_revision_groups": event_revision_groups,
        "query_revision_groups": query_revision_groups,
        "revision_link_audits": revision_link_audits,
        "orphan_audits": orphan_audits,
        "audit_links_outside_window": audit_links_outside_window,
        "invalid_actual_rows": invalid_actual_rows,
        "invalid_target_session_groups": len(invalid_target_keys),
        "invalid_target_session_predictions": len(invalid_target_rows),
        "quarantined_target_session_audits": len(quarantined_target_audits),
        "last_prediction_time": _latest_timestamp(predictions, "run_time_tw"),
        "last_audit_time": _latest_timestamp(audits, "audit_time_tw", "audit_date_tw"),
        "files": files,
        "remote_sync": sync,
        "decision_influence": False,
        "formal_weights_changed": False,
    }


def compact_learning_health(limit: int = 900) -> Dict[str, Any]:
    row = learning_integrity_snapshot(limit)
    if row.get("status") == "DEGRADED":
        return {"level": "warning", "text": "預測學習檢核暫時不可用", "raw": row}

    pending = int(row.get("pending_official_samples") or 0)
    formal = int(row.get("formal_t1_audits") or row.get("verified_t1_audits") or 0)
    eligible = int(row.get("learning_eligible_t1_audits") or 0)
    query_revisions = int(row.get("query_revision_groups") or 0)
    revision_links = int(row.get("revision_link_audits") or 0)
    outside_window = int(row.get("audit_links_outside_window") or 0)
    warnings = list(row.get("warnings") or [])

    context = []
    if query_revisions:
        context.append(f"查詢修正版 {query_revisions}組已合併")
    if revision_links:
        context.append(f"Audit修正版連結 {revision_links}筆已對回")
    if outside_window:
        context.append(f"視窗外連結 {outside_window}筆不列孤兒")

    base = f"正式比對 {formal}｜可學習 {eligible}｜歷史待稽核 {pending}"
    if warnings:
        detail = "；".join((warnings + context)[:3])
        return {
            "level": "warning",
            "text": f"預測學習需檢查｜{base}｜{detail}",
            "raw": row,
        }
    detail = f"｜{'；'.join(context[:2])}" if context else ""
    return {
        "level": "ok",
        "text": f"預測學習正常｜{base}{detail}",
        "raw": row,
    }


def recommended_audit_batch(pending_count: int, *, hard_cap: int = 2) -> int:
    """Small adaptive batch; never exceeds the Cloud-safe hard cap."""
    pending = max(0, int(pending_count or 0))
    cap = max(1, min(int(hard_cap or 2), 2))
    return cap if pending >= 2 else 1
