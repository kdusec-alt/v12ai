# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timedelta
import html
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from memory_store import (
    MEMORY_DIR,
    PREDICTION_LOG,
    AUDIT_LOG,
    TICKER_PROFILE,
    read_prediction_log,
    read_audit_log,
    load_profiles,
    memory_diagnostics,
)
from tino_persistent_store import (
    DEFAULT_LEDGER_PATH,
    load_ledger,
    storage_status,
    ensure_memory_initialized_bootsafe,
)

try:
    from auto_audit_scheduler import auto_audit_status_rows, execute_due_auto_audit_once
except Exception:
    def auto_audit_status_rows():
        return []

    def execute_due_auto_audit_once(*args, **kwargs):
        return {"status": "disabled", "reason": "auto_audit_module_unavailable", "markets": {}}


TW_TZ = ZoneInfo("Asia/Taipei")
_MAX_VIEW_ROWS = 80
_MAX_LOG_ROWS = 900


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


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


def _row_dt(row: Dict[str, Any]) -> Optional[datetime]:
    for key in (
        "run_time_tw",
        "audit_time_tw",
        "logged_at_tw",
        "created_at_tw",
        "created_at",
        "timestamp",
        "run_date_tw",
        "audit_date_tw",
        "target_trade_date",
    ):
        dt = _parse_dt(row.get(key))
        if dt is not None:
            return dt
    return None


def _is_recent(row: Dict[str, Any], days: int = 30) -> bool:
    dt = _row_dt(row)
    if dt is None:
        return False
    return dt >= datetime.now(TW_TZ) - timedelta(days=days)


def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _normalize_prediction(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize RC3/RC4 and nested legacy prediction schemas.

    The Learning UI must survive additive model fields and historical rows.  It
    therefore reads aliases instead of indexing one fixed schema.
    """
    if not isinstance(raw, dict):
        return {}
    row = dict(raw)
    for container_key in ("prediction", "forecast", "snapshot", "payload"):
        nested = row.get(container_key)
        if isinstance(nested, dict):
            merged = dict(nested)
            merged.update(row)
            row = merged

    ticker = _coalesce(row.get("ticker"), row.get("symbol"), row.get("resolved_symbol"))
    run_time = _coalesce(
        row.get("run_time_tw"), row.get("logged_at_tw"), row.get("created_at_tw"),
        row.get("created_at"), row.get("timestamp"), row.get("run_date_tw"),
    )
    target_date = _coalesce(
        row.get("target_trade_date"), row.get("target_date"), row.get("trade_date"),
        row.get("run_date_tw"),
    )
    next_close = _coalesce(
        row.get("next_close_est"), row.get("t1"), row.get("final_t1"),
        row.get("predicted_close"), row.get("next_close"),
    )
    today_close = _coalesce(
        row.get("today_close_est"), row.get("t0"), row.get("final_t0"),
        row.get("today_close"),
    )
    dna = _as_dict(row.get("prediction_dna"))
    if not dna:
        dna = _as_dict(row.get("dna"))

    out = dict(row)
    out.update({
        "ticker": str(ticker or "").strip().upper(),
        "run_time_tw": str(run_time or ""),
        "target_trade_date": str(target_date or ""),
        "target_kind": str(_coalesce(row.get("target_kind"), "T1_CLOSE_NEXT_SESSION") or ""),
        "today_close_est": today_close,
        "next_close_est": next_close,
        "confidence": _coalesce(row.get("confidence"), row.get("direction_confidence")),
        "session_mode": _coalesce(row.get("session_mode"), row.get("session"), "unknown"),
        "predicted_direction": _coalesce(
            row.get("predicted_direction"), row.get("direction"), dna.get("direction")
        ),
        "direction_score": _coalesce(row.get("direction_score"), dna.get("direction_score")),
        "prediction_dna": dna,
    })
    return out


def _normalize_audit(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    row = dict(raw)
    nested = row.get("audit")
    if isinstance(nested, dict):
        merged = dict(nested)
        merged.update(row)
        row = merged
    return row


def _row_identity(row: Dict[str, Any]) -> str:
    for key in ("id", "audit_id", "prediction_id"):
        if row.get(key) not in (None, ""):
            return f"{key}:{row.get(key)}"
    ticker = str(row.get("ticker") or row.get("symbol") or "")
    stamp = str(_coalesce(
        row.get("run_time_tw"), row.get("audit_time_tw"), row.get("logged_at_tw"),
        row.get("created_at"), row.get("run_date_tw"), row.get("audit_date_tw")
    ) or "")
    target = str(_coalesce(row.get("target_trade_date"), row.get("target"), row.get("target_kind")) or "")
    return f"fallback:{ticker}:{target}:{stamp}" if ticker and stamp else ""


def _merge_rows(primary: Iterable[Dict[str, Any]], fallback: Iterable[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen = set()
    for row in list(primary or []) + list(fallback or []):
        if not isinstance(row, dict):
            continue
        ident = _row_identity(row)
        if ident and ident in seen:
            continue
        if ident:
            seen.add(ident)
        rows.append(row)
    rows.sort(key=lambda r: str(_coalesce(
        r.get("run_time_tw"), r.get("audit_time_tw"), r.get("logged_at_tw"),
        r.get("created_at"), r.get("run_date_tw"), r.get("audit_date_tw")
    ) or ""))
    return rows[-max(1, int(limit)):]


def _ledger_recovery_rows() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    try:
        ledger = load_ledger(DEFAULT_LEDGER_PATH, initialize_if_missing=False)
        if not isinstance(ledger, dict):
            return [], []
        predictions = [x for x in (ledger.get("recent_predictions") or []) if isinstance(x, dict)]
        audits = [x for x in (ledger.get("recent_audits") or []) if isinstance(x, dict)]
        return predictions, audits
    except Exception:
        return [], []


def _latest_formal_samples(predictions: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    latest: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    normalized = [_normalize_prediction(row) for row in predictions if isinstance(row, dict)]
    normalized.sort(key=lambda row: str(row.get("run_time_tw") or ""))
    for row in normalized:
        ticker = str(row.get("ticker") or "").strip().upper()
        target_date = str(row.get("target_trade_date") or "").strip()
        target_kind = str(row.get("target_kind") or "T1_CLOSE_NEXT_SESSION").strip()
        if not ticker or not target_date:
            continue
        if row.get("valid_price_sample") is False or row.get("skipped") is True:
            continue
        if row.get("next_close_est") in (None, "") and row.get("today_close_est") in (None, ""):
            continue
        latest[(ticker, target_date, target_kind)] = row
    return list(latest.values())


def _fmt(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    if number is None:
        return "--" if value in (None, "") else str(value)
    return f"{number:.{digits}f}"


def _compact_scalar(value: Any, max_len: int = 110) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        text = f"{value:.4f}".rstrip("0").rstrip(".")
    elif isinstance(value, (dict, list, tuple, set)):
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        except Exception:
            text = str(value)
    else:
        text = str(value)
    text = text.replace("\n", " ").replace("\r", " ")
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def _html_table(
    st,
    rows: Sequence[Dict[str, Any]],
    empty_text: str,
    *,
    columns: Optional[Sequence[str]] = None,
    height: int = 420,
    limit: int = _MAX_VIEW_ROWS,
) -> None:
    """Render a bounded scalar-only HTML table.

    No Pandas, PyArrow or Arrow schema conversion is used.  This is the RC4.7
    crash guard for Community Cloud page switching.
    """
    clean_rows = [row for row in list(rows or []) if isinstance(row, dict)][: max(1, int(limit))]
    if not clean_rows:
        st.caption(empty_text)
        return

    if columns is None:
        keys: List[str] = []
        for row in clean_rows[:10]:
            for key in row.keys():
                if key not in keys:
                    keys.append(str(key))
                if len(keys) >= 12:
                    break
            if len(keys) >= 12:
                break
    else:
        keys = [str(key) for key in columns]
    keys = keys[:12]

    header = "".join(
        "<th style='position:sticky;top:0;z-index:2;padding:8px 10px;"
        "background:#102237;color:#fff5c4;border-bottom:1px solid #2b526c;"
        "text-align:left;white-space:nowrap;font-size:12px'>"
        + html.escape(key)
        + "</th>"
        for key in keys
    )
    body_parts: List[str] = []
    for row in clean_rows:
        cells = []
        for key in keys:
            text = _compact_scalar(row.get(key, ""))
            cells.append(
                "<td title='" + html.escape(text, quote=True) + "' style='padding:7px 10px;"
                "border-bottom:1px solid #203144;color:#eaf6ff;max-width:240px;"
                "overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px'>"
                + html.escape(text)
                + "</td>"
            )
        body_parts.append("<tr>" + "".join(cells) + "</tr>")

    st.markdown(
        "<div style='background:#071727;border:1px solid #24445b;border-radius:12px;"
        f"overflow:auto;max-height:{int(height)}px;margin:6px 0 10px 0'>"
        "<table style='width:100%;border-collapse:collapse;min-width:980px'>"
        f"<thead><tr>{header}</tr></thead><tbody>{''.join(body_parts)}</tbody>"
        "</table></div>",
        unsafe_allow_html=True,
    )


def _metric_cards(st, metrics: Sequence[Tuple[str, Any, str]]) -> None:
    cols = st.columns(len(metrics), gap="small")
    for column, (label, value, help_text) in zip(cols, metrics):
        with column:
            st.metric(label, value, help=help_text)


def _kpi(predictions: Sequence[Dict[str, Any]], audits: Sequence[Dict[str, Any]], formal: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    price_audits = [row for row in audits if row.get("target") in ("today", "next")]
    errors = [abs(number) for number in (_safe_float(row.get("error_pct")) for row in price_audits) if number is not None]
    biases = [number for number in (_safe_float(row.get("error_pct")) for row in price_audits) if number is not None]
    dna_count = sum(1 for row in formal if isinstance(row.get("prediction_dna"), dict) and row.get("prediction_dna"))
    return {
        "total_analysis": len(predictions),
        "formal_samples": len(formal),
        "dna_samples": dna_count,
        "audited_samples": len(price_audits),
        "avg_abs_error_pct": round(sum(errors) / len(errors), 3) if errors else None,
        "avg_bias_pct": round(sum(biases) / len(biases), 3) if biases else None,
    }


def _formal_rows(rows: Sequence[Dict[str, Any]], limit: int = 80) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    ordered = sorted(rows, key=lambda row: str(row.get("run_time_tw") or ""), reverse=True)
    for row in ordered[:limit]:
        output.append({
            "ticker": row.get("ticker"),
            "run_time_tw": str(row.get("run_time_tw") or "")[:16].replace("T", " "),
            "target_trade_date": row.get("target_trade_date"),
            "target_kind": row.get("target_kind"),
            "today_close_est": row.get("today_close_est"),
            "next_close_est": row.get("next_close_est"),
            "direction": row.get("predicted_direction"),
            "direction_score": row.get("direction_score"),
            "confidence": row.get("confidence"),
            "session_mode": row.get("session_mode"),
            "id": row.get("id"),
        })
    return output


def _raw_rows(rows: Sequence[Dict[str, Any]], limit: int = 80) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    normalized = [_normalize_prediction(row) for row in rows]
    normalized.sort(key=lambda row: str(row.get("run_time_tw") or ""), reverse=True)
    for row in normalized[:limit]:
        output.append({
            "ticker": row.get("ticker"),
            "run_time_tw": str(row.get("run_time_tw") or "")[:16].replace("T", " "),
            "target_trade_date": row.get("target_trade_date"),
            "session_mode": row.get("session_mode"),
            "t0": row.get("today_close_est"),
            "t1": row.get("next_close_est"),
            "direction": row.get("predicted_direction"),
            "score": row.get("direction_score"),
            "confidence": row.get("confidence"),
            "schema": _as_dict(row.get("prediction_dna")).get("schema") or row.get("learning_schema"),
            "id": row.get("id"),
        })
    return output


def _sorted_contributions(value: Any, limit: int = 6) -> List[Tuple[str, float]]:
    if not isinstance(value, dict):
        return []
    rows: List[Tuple[str, float]] = []
    for name, raw in value.items():
        number = _safe_float(raw)
        if number is not None:
            rows.append((str(name), number))
    rows.sort(key=lambda item: abs(item[1]), reverse=True)
    return rows[:limit]


def _contribution_text(value: Any, limit: int = 6) -> str:
    return "｜".join(f"{name} {number:+.1f}" for name, number in _sorted_contributions(value, limit))


def _dna_rows(rows: Sequence[Dict[str, Any]], limit: int = 80) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    ordered = sorted(rows, key=lambda row: str(row.get("run_time_tw") or ""), reverse=True)
    for raw in ordered[:limit]:
        row = _normalize_prediction(raw)
        dna = _as_dict(row.get("prediction_dna"))
        factors = _as_dict(_coalesce(dna.get("factor_contributions"), row.get("direction_factor_contributions")))
        families = _as_dict(_coalesce(dna.get("family_contributions"), row.get("direction_family_contributions")))
        risks = _as_dict(_coalesce(dna.get("risk_contributions"), row.get("direction_risk_contributions")))
        calibration = _as_dict(_coalesce(dna.get("learning_calibration"), row.get("direction_learning_calibration")))

        dominant_force = _coalesce(dna.get("dominant_force"), row.get("dominant_force"))
        dominant_contribution = _coalesce(dna.get("dominant_contribution"), row.get("dominant_contribution"))
        if not dominant_force:
            top = _sorted_contributions(factors, 1)
            if top:
                dominant_force, dominant_contribution = top[0]

        risk_total = _safe_float(dna.get("risk_total"))
        if risk_total is None:
            risk_total = sum(max(0.0, number) for _, number in _sorted_contributions(risks, 999))

        output.append({
            "ticker": row.get("ticker"),
            "target_date": row.get("target_trade_date"),
            "direction": _coalesce(dna.get("direction"), row.get("predicted_direction")),
            "direction_score": _coalesce(dna.get("direction_score"), row.get("direction_score")),
            "dominant_force": dominant_force,
            "dominant_value": dominant_contribution,
            "dominant_share": dna.get("dominant_share"),
            "top_factors": _contribution_text(factors, 6),
            "family_force": _contribution_text(families, 5),
            "risk_total": risk_total,
            "learning_delta": calibration.get("delta"),
            "learning_gate": calibration.get("gate"),
            "quality": _coalesce(dna.get("data_quality"), row.get("direction_quality")),
            "conflict": _coalesce(dna.get("conflict"), row.get("direction_conflict")),
            "schema": _coalesce(dna.get("schema"), row.get("learning_schema")),
        })
    return output


def _recent_t1_audits(audits: Sequence[Dict[str, Any]], limit: int = 40) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    normalized = [_normalize_audit(row) for row in audits]
    normalized.sort(key=lambda row: str(row.get("audit_time_tw") or ""), reverse=True)
    for row in normalized:
        if row.get("target") != "next":
            continue
        error = _safe_float(row.get("error_pct"))
        rows.append({
            "ticker": row.get("ticker"),
            "target_date": row.get("target_trade_date"),
            "prediction_time": str(row.get("prediction_run_time_tw") or "")[:16].replace("T", " "),
            "predicted_close": row.get("predicted_close"),
            "actual_close": row.get("actual_close"),
            "error_pct": None if error is None else round(error, 3),
            "direction_hit": row.get("direction_hit"),
            "close_in_range": row.get("close_in_predicted_range"),
            "tail_breach_pct": row.get("downside_tail_breach_pct"),
            "result": (
                "命中" if error is not None and abs(error) < 1.0
                else "偏低" if error is not None and error > 0
                else "偏高" if error is not None
                else "--"
            ),
            "source": row.get("source"),
        })
        if len(rows) >= limit:
            break
    return rows


def _profile_rows(limit: int = 80) -> List[Dict[str, Any]]:
    profiles = load_profiles()
    values = list(profiles.values()) if isinstance(profiles, dict) else []
    values.sort(key=lambda row: str((row or {}).get("updated_at_tw") or ""), reverse=True)
    output: List[Dict[str, Any]] = []
    for profile in values[:limit]:
        if not isinstance(profile, dict):
            continue
        family_learning = _as_dict(profile.get("family_learning"))
        active: List[str] = []
        for name, raw in family_learning.items():
            if not isinstance(raw, dict) or _safe_int(raw.get("count")) < 8:
                continue
            multiplier = _safe_float(raw.get("active_multiplier"))
            active.append(f"{name}:{(multiplier if multiplier is not None else 1.0):.3f}")
        output.append({
            "ticker": profile.get("ticker"),
            "audit_count": profile.get("audit_count") or profile.get("foreign_audit_count"),
            "direction_audits": profile.get("direction_audit_count"),
            "direction_hit_rate": profile.get("direction_hit_rate"),
            "avg_abs_error_pct": profile.get("avg_abs_error_pct"),
            "learning_maturity": profile.get("learning_maturity"),
            "active_family_count": profile.get("active_family_count"),
            "active_multipliers": "｜".join(active[:6]),
            "suggested_bias": profile.get("suggested_bias"),
            "approved_bias": profile.get("approved_bias"),
            "updated_at_tw": profile.get("updated_at_tw") or profile.get("approved_at_tw"),
        })
    return output


def _file_status(path: Path) -> Dict[str, Any]:
    try:
        return {
            "file": path.name,
            "status": "EXISTS" if path.exists() else "MISSING",
            "size_kb": round((path.stat().st_size if path.exists() else 0) / 1024.0, 2),
            "path": str(path),
        }
    except Exception as exc:
        return {
            "file": path.name,
            "status": "ERROR",
            "size_kb": 0,
            "path": str(path),
            "note": f"{type(exc).__name__}: {exc}",
        }


def _storage_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        diagnostics = memory_diagnostics()
    except Exception as exc:
        diagnostics = {"memory_dir": str(MEMORY_DIR), "files": [], "error": f"{type(exc).__name__}: {exc}"}

    try:
        status = storage_status(DEFAULT_LEDGER_PATH)
    except Exception as exc:
        status = {"path": str(DEFAULT_LEDGER_PATH), "last_error": f"{type(exc).__name__}: {exc}"}

    rows.append({
        "item": "Active Memory",
        "status": "PASS",
        "value": diagnostics.get("memory_dir"),
        "note": diagnostics.get("error"),
    })
    rows.append({
        "item": "Visible Predictions",
        "status": diagnostics.get("prediction_rows_visible", 0),
        "value": "merged active/legacy tail",
        "note": None,
    })
    rows.append({
        "item": "Visible Audits",
        "status": diagnostics.get("audit_rows_visible", 0),
        "value": "merged active/legacy tail",
        "note": None,
    })
    rows.append({
        "item": "Ticker Profiles",
        "status": diagnostics.get("profile_count", 0),
        "value": str(TICKER_PROFILE),
        "note": None,
    })
    rows.append({
        "item": "Memory Ledger",
        "status": "PASS" if status.get("last_write_ok") and status.get("last_verify_ok") else "LOCAL",
        "value": status.get("path"),
        "note": status.get("last_error"),
    })
    rows.append({
        "item": "Long-term Remote",
        "status": status.get("remote_status") or ("PASS" if status.get("remote_configured") else "LOCAL_ONLY"),
        "value": (
            f"{status.get('remote_backend') or 'none'}://{status.get('remote_repo') or '-'}"
            f"#{status.get('remote_branch') or '-'}:{status.get('remote_memory_dir') or '.tino_memory'}"
        ),
        "note": status.get("remote_error"),
    })
    for file_row in diagnostics.get("files", []) or []:
        if not isinstance(file_row, dict):
            continue
        rows.append({
            "item": file_row.get("file"),
            "status": "EXISTS" if file_row.get("exists") else "MISSING",
            "value": f"{round(_safe_float(file_row.get('size_bytes')) or 0.0, 0):.0f} bytes｜rows {file_row.get('rows', '--')}",
            "note": file_row.get("path") or file_row.get("error"),
        })
    rows.append({
        "item": "Memory Recovery Index",
        "status": "PASS" if _safe_int(status.get("ledger_recent_predictions")) or _safe_int(status.get("ledger_recent_audits")) else "EMPTY",
        "value": f"pred {status.get('ledger_recent_predictions', 0)}｜audit {status.get('ledger_recent_audits', 0)}",
        "note": str(DEFAULT_LEDGER_PATH),
    })
    return rows


