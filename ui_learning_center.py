# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from memory_store import (
    MEMORY_DIR,
    PREDICTION_LOG,
    AUDIT_LOG,
    TICKER_PROFILE,
    read_prediction_log,
    read_audit_log,
    load_profiles,
)
from tino_persistent_store import DEFAULT_LEDGER_PATH, load_ledger, storage_status, ensure_memory_initialized_bootsafe
from auto_audit_scheduler import auto_audit_status_rows

TW_TZ = ZoneInfo("Asia/Taipei")


def _parse_dt(value: Any) -> Optional[datetime]:
    txt = str(value or "").strip()
    if not txt:
        return None
    try:
        dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TW_TZ)
        return dt.astimezone(TW_TZ)
    except Exception:
        try:
            d = datetime.fromisoformat(txt[:10])
            return d.replace(tzinfo=TW_TZ)
        except Exception:
            return None


def _is_recent(row: Dict[str, Any], keys: Tuple[str, ...], days: int = 30) -> bool:
    cutoff = datetime.now(TW_TZ) - timedelta(days=days)
    for k in keys:
        dt = _parse_dt(row.get(k))
        if dt:
            return dt >= cutoff
    # If old rows have no timestamp, keep them out of 30-day KPI to avoid fake stats.
    return False


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v in (None, ""):
            return None
        return float(v)
    except Exception:
        return None




def _row_identity(row: Dict[str, Any]) -> str:
    if not isinstance(row, dict):
        return ""
    for k in ("id", "audit_id"):
        if row.get(k):
            return f"{k}:{row.get(k)}"
    ticker = str(row.get("ticker") or "")
    ts = str(row.get("run_time_tw") or row.get("audit_time_tw") or row.get("logged_at_tw") or "")
    target = str(row.get("target") or row.get("target_kind") or row.get("target_trade_date") or "")
    return f"fallback:{ticker}:{target}:{ts}" if ticker and ts else ""


def _merge_memory_rows(primary: List[Dict[str, Any]], fallback: List[Dict[str, Any]], limit: int = 5000) -> List[Dict[str, Any]]:
    """Merge JSONL rows with ledger recovery rows without duplicates."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for row in list(primary or []) + list(fallback or []):
        if not isinstance(row, dict):
            continue
        ident = _row_identity(row)
        if ident and ident in seen:
            continue
        if ident:
            seen.add(ident)
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _ledger_recovery_rows() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Read compact recovery rows from ledger if JSONL was lost after reconnect."""
    try:
        ledger = load_ledger(DEFAULT_LEDGER_PATH, initialize_if_missing=False)
        preds = ledger.get("recent_predictions", []) if isinstance(ledger, dict) else []
        audits = ledger.get("recent_audits", []) if isinstance(ledger, dict) else []
        return (
            [r for r in preds if isinstance(r, dict)],
            [r for r in audits if isinstance(r, dict)],
        )
    except Exception:
        return [], []

def _fmt_num(v: Any, digits: int = 2) -> str:
    x = _safe_float(v)
    return "--" if x is None else f"{x:.{digits}f}"


def _latest_formal_samples(preds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One formal sample per ticker + target date + prediction type.

    Multiple analyses of the same stock on the same day are raw logs; for audit
    and accuracy stats, the latest run_time_tw wins.
    """
    ordered = sorted(preds, key=lambda r: str(r.get("run_time_tw") or ""))
    latest: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for r in ordered:
        ticker = str(r.get("ticker") or "").strip().upper()
        target_date = str(r.get("target_trade_date") or r.get("run_date_tw") or "").strip()
        target_kind = str(r.get("target_kind") or "T1_CLOSE_NEXT_SESSION").strip()
        if not ticker or not target_date:
            continue
        if r.get("valid_price_sample") is False or r.get("skipped") is True:
            continue
        if r.get("next_close_est") in (None, "") and r.get("today_close_est") in (None, ""):
            continue
        latest[(ticker, target_date, target_kind)] = r
    return list(latest.values())


def _file_status(path: Path) -> Dict[str, Any]:
    try:
        return {
            "file": path.name,
            "exists": path.exists(),
            "size_bytes": path.stat().st_size if path.exists() else 0,
            "path": str(path),
        }
    except Exception as exc:
        return {"file": path.name, "exists": False, "size_bytes": 0, "path": str(path), "error": f"{type(exc).__name__}: {exc}"}


def _df(st, rows: List[Dict[str, Any]], empty_text: str, height: Optional[int] = None) -> None:
    if not rows:
        st.caption(empty_text)
        return
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, height=height)


def _small_metric_cards(st, metrics: List[Tuple[str, Any, str]]) -> None:
    cols = st.columns(len(metrics), gap="small")
    for c, (label, value, help_text) in zip(cols, metrics):
        with c:
            st.metric(label, value, help=help_text)


def _kpi(preds_30: List[Dict[str, Any]], audits_30: List[Dict[str, Any]], formal_30: List[Dict[str, Any]]) -> Dict[str, Any]:
    price_audits = [a for a in audits_30 if a.get("target") in ("today", "next")]
    abs_errs = [abs(float(a.get("error_pct"))) for a in price_audits if _safe_float(a.get("error_pct")) is not None]
    bias_vals = [float(a.get("error_pct")) for a in price_audits if _safe_float(a.get("error_pct")) is not None]
    return {
        "total_analysis": len(preds_30),
        "formal_samples": len(formal_30),
        "audited_samples": len(price_audits),
        "avg_abs_error_pct": round(sum(abs_errs) / len(abs_errs), 3) if abs_errs else None,
        "avg_bias_pct": round(sum(bias_vals) / len(bias_vals), 3) if bias_vals else None,
    }


def _recent_t1_audits(audits: List[Dict[str, Any]], limit: int = 30) -> List[Dict[str, Any]]:
    rows = []
    for a in sorted(audits, key=lambda r: str(r.get("audit_time_tw") or ""), reverse=True):
        if a.get("target") != "next":
            continue
        err = _safe_float(a.get("error_pct"))
        rows.append({
            "ticker": a.get("ticker"),
            "target_date": a.get("target_trade_date"),
            "prediction_run_date": a.get("prediction_run_date_tw"),
            "prediction_run_time": str(a.get("prediction_run_time_tw") or "")[:16].replace("T", " "),
            "predicted_close": a.get("predicted_close"),
            "actual_close": a.get("actual_close"),
            "error_pct": None if err is None else round(err, 3),
            "result": "命中" if err is not None and abs(err) < 1.0 else "偏低" if err is not None and err > 0 else "偏高" if err is not None else "--",
            "source": a.get("source"),
        })
        if len(rows) >= limit:
            break
    return rows


def _formal_rows(rows: List[Dict[str, Any]], limit: int = 80) -> List[Dict[str, Any]]:
    out = []
    for r in sorted(rows, key=lambda x: str(x.get("run_time_tw") or ""), reverse=True)[:limit]:
        out.append({
            "ticker": r.get("ticker"),
            "run_time_tw": str(r.get("run_time_tw") or "")[:16].replace("T", " "),
            "target_trade_date": r.get("target_trade_date"),
            "target_kind": r.get("target_kind"),
            "today_close_est": r.get("today_close_est"),
            "next_close_est": r.get("next_close_est"),
            "confidence": r.get("confidence"),
            "session_mode": r.get("session_mode"),
            "id": r.get("id"),
        })
    return out


def _raw_rows(rows: List[Dict[str, Any]], limit: int = 100) -> List[Dict[str, Any]]:
    out = []
    for r in sorted(rows, key=lambda x: str(x.get("run_time_tw") or ""), reverse=True)[:limit]:
        out.append({
            "ticker": r.get("ticker"),
            "run_time_tw": str(r.get("run_time_tw") or "")[:16].replace("T", " "),
            "run_date_tw": r.get("run_date_tw"),
            "target_trade_date": r.get("target_trade_date"),
            "session_mode": r.get("session_mode"),
            "t0": r.get("today_close_est"),
            "t1": r.get("next_close_est"),
            "confidence": r.get("confidence"),
            "id": r.get("id"),
        })
    return out


def _profile_rows(limit: int = 80) -> List[Dict[str, Any]]:
    profiles = list(load_profiles().values())
    out = []
    for p in profiles[:limit]:
        out.append({
            "ticker": p.get("ticker"),
            "approved_bias": p.get("approved_bias"),
            "suggested_bias": p.get("suggested_bias"),
            "avg_abs_error_pct": p.get("avg_abs_error_pct"),
            "audit_count": p.get("audit_count") or p.get("foreign_audit_count"),
            "updated_at_tw": p.get("updated_at_tw") or p.get("approved_at_tw"),
        })
    return out


def _storage_rows() -> List[Dict[str, Any]]:
    init_report = ensure_memory_initialized_bootsafe(migrate=True)
    ss = storage_status(DEFAULT_LEDGER_PATH)
    files = [_file_status(PREDICTION_LOG), _file_status(AUDIT_LOG), _file_status(TICKER_PROFILE), _file_status(Path(DEFAULT_LEDGER_PATH))]
    remote_status = ss.get("remote_status") or ("PASS" if ss.get("remote_configured") else "LOCAL_ONLY")
    remote_error = ss.get("remote_error") or ""
    shrink_notes = ss.get("remote_shrink_warnings") or []
    if shrink_notes:
        remote_error = (remote_error + " | " if remote_error else "") + "; ".join([str(x) for x in shrink_notes[-3:]])
    rows = [{
        "item": "Memory Ledger",
        "status": "PASS" if ss.get("last_write_ok") and ss.get("last_verify_ok") else "待確認" if ss.get("exists") else "尚未建立",
        "path": ss.get("path"),
        "last_write": ss.get("last_write_at_tw"),
        "last_verify": ss.get("last_verify_at_tw"),
        "error": ss.get("last_error"),
    }, {
        "item": "Long-term Remote",
        "status": remote_status,
        "path": f"{ss.get('remote_backend')}://{ss.get('remote_repo') or '-'}#{ss.get('remote_branch') or '-'}:{ss.get('remote_memory_dir')}",
        "last_write": ss.get("remote_last_sync"),
        "last_verify": ss.get("remote_last_restore"),
        "error": remote_error or None,
        "imported_predictions": None,
        "imported_audits": None,
        "imported_profiles": None,
        "imported_watch_symbols": None,
    }, {
        "item": "Memory Migration",
        "status": ss.get("migration_status") or init_report.get("status"),
        "path": str(MEMORY_DIR),
        "last_write": ss.get("migration_at_tw"),
        "last_verify": None,
        "error": init_report.get("error"),
        "imported_predictions": ss.get("imported_predictions"),
        "imported_audits": ss.get("imported_audits"),
        "imported_profiles": ss.get("imported_profiles"),
        "imported_watch_symbols": ss.get("imported_watch_symbols"),
    }]
    rows.append({
        "item": "Memory Recovery Index",
        "status": "PASS" if int(ss.get("ledger_recent_predictions") or 0) or int(ss.get("ledger_recent_audits") or 0) else "EMPTY",
        "path": str(DEFAULT_LEDGER_PATH),
        "prediction_log_rows": ss.get("prediction_log_rows"),
        "audit_log_rows": ss.get("audit_log_rows"),
        "ledger_recent_predictions": ss.get("ledger_recent_predictions"),
        "ledger_recent_audits": ss.get("ledger_recent_audits"),
    })
    for f in files:
        rows.append({
            "item": f.get("file"),
            "status": "EXISTS" if f.get("exists") else "MISSING",
            "path": f.get("path"),
            "size_bytes": f.get("size_bytes"),
            "error": f.get("error"),
        })
    return rows


def render_learning_center(st) -> None:
    """Admin-only prediction learning dashboard.

    This page is deliberately not shown to public users, because bias/profile/audit
    state is engineering memory, not front-stage investment advice.
    """
    try:
        st.session_state["memory_init_report"] = ensure_memory_initialized_bootsafe(migrate=True)
    except Exception:
        pass
    if not bool(st.session_state.get("admin_authenticated", False)):
        st.warning("預測學習為 Admin-only 頁面。請先在側邊欄輸入 Admin Password。")
        return

    st.markdown("### 🧠 預測學習 / Learning Center")
    st.caption("只顯示在 Admin 登入後；前台使用者不會看到 Bias、Audit、Learning Weight 或工程記憶。正式樣本規則：同一股票 + 同一交易日 + 同一預測類型，只抓最後一次正式分析。")
    st.caption("RC24.1 Stable：Streamlit 僅讀寫本機 .tino_memory；GitHub Remote 還原/同步改由外部或手動維護程序執行，不在頁面 rerun 內啟動。")

    # JSONL is the raw log.  Ledger recent_* is the reconnect/relogin recovery
    # index.  Merge both so Recent T1 audits do not disappear when Streamlit
    # restarts with empty JSONL but the ledger survived/restored.
    ledger_preds, ledger_audits = _ledger_recovery_rows()
    preds = _merge_memory_rows(read_prediction_log(5000), ledger_preds, 5000)
    audits = _merge_memory_rows(read_audit_log(5000), ledger_audits, 5000)
    preds_30 = [p for p in preds if _is_recent(p, ("run_time_tw", "run_date_tw"), 30) and p.get("skipped") is not True]
    audits_30 = [a for a in audits if _is_recent(a, ("audit_time_tw", "audit_date_tw"), 30)]
    formal_30 = _latest_formal_samples(preds_30)
    kpi = _kpi(preds_30, audits_30, formal_30)

    _small_metric_cards(st, [
        ("總分析次數", kpi["total_analysis"], "每次按個股分析都算一次，包含同股重複查詢。"),
        ("正式預測樣本", kpi["formal_samples"], "同股同交易日同預測類型只保留最後一次。"),
        ("完成 Auto Audit", kpi["audited_samples"], "已有 actual close 可比對的 price audit 樣本。"),
        ("平均誤差", "--" if kpi["avg_abs_error_pct"] is None else f"{kpi['avg_abs_error_pct']:.2f}%", "近30日 price audit 平均絕對誤差。"),
        ("平均 Bias", "--" if kpi["avg_bias_pct"] is None else f"{kpi['avg_bias_pct']:+.2f}%", "actual - predicted 的平均方向。正值代表模型偏保守。"),
    ])

    st.divider()
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["昨測今收", "正式樣本", "Raw Log", "Bias History", "Storage Status"])
    with tab1:
        st.markdown("#### 昨測今收 / Recent T1 audits")
        _df(st, _recent_t1_audits(audits_30, 40), "近30日尚無昨測今收 Audit。", height=360)
    with tab2:
        st.markdown("#### 正式預測樣本（最後一次正式分析）")
        _df(st, _formal_rows(formal_30, 120), "近30日尚無正式預測樣本。", height=420)
    with tab3:
        st.markdown("#### Raw prediction log（全部分析紀錄）")
        _df(st, _raw_rows(preds_30, 150), "近30日尚無 raw prediction log。", height=460)
    with tab4:
        st.markdown("#### Bias / Ticker profile")
        _df(st, _profile_rows(120), "尚無 ticker profile。", height=420)
    with tab5:
        st.markdown("#### Storage Guard")
        st.caption(f"Memory path：{MEMORY_DIR}")
        _df(st, _storage_rows(), "尚無 storage 狀態。", height=360)
        st.markdown("#### Auto Audit Time Guard")
        _df(st, auto_audit_status_rows(), "尚無 Auto Audit 排程紀錄。台股 14:00、 美股 06:00 台灣時間會在下次 app rerun 時檢查。", height=240)
        try:
            ledger = load_ledger(DEFAULT_LEDGER_PATH, initialize_if_missing=False)
            wc = ledger.get("watch_center", {}) if isinstance(ledger, dict) else {}
            st.caption(f"Watch symbols：{len(wc.get('symbols', []) or [])}｜Hidden symbols：{len(wc.get('hidden_symbols', []) or [])}")
        except Exception as exc:
            st.warning(f"Ledger 讀取失敗：{type(exc).__name__}: {exc}")
