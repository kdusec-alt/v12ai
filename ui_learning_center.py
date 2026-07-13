# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo


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
try:
    from auto_audit_scheduler import auto_audit_status_rows, execute_due_auto_audit_once
except Exception:
    def auto_audit_status_rows():
        return []
    def execute_due_auto_audit_once(*args, **kwargs):
        return {"status": "disabled", "reason": "auto_audit_module_unavailable"}

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
    # Pandas/PyArrow is loaded only when the Admin explicitly opens a table.
    # App startup and normal stock analysis therefore stay on the RC3.3 light path.
    import pandas as pd
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
            "direction_hit": a.get("direction_hit"),
            "close_in_range": a.get("close_in_predicted_range"),
            "predicted_low": a.get("predicted_low"),
            "actual_low": a.get("actual_low"),
            "downside_tail_breach_pct": a.get("downside_tail_breach_pct"),
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


def _dna_rows(rows: List[Dict[str, Any]], limit: int = 80) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    ordered = sorted(rows, key=lambda x: str(x.get("run_time_tw") or ""), reverse=True)
    for row in ordered[:limit]:
        dna = row.get("prediction_dna") if isinstance(row.get("prediction_dna"), dict) else {}
        calibration = dna.get("learning_calibration") if isinstance(dna.get("learning_calibration"), dict) else {}
        out.append({
            "ticker": row.get("ticker"),
            "target_date": row.get("target_trade_date"),
            "direction": dna.get("direction") or row.get("predicted_direction"),
            "direction_score": dna.get("direction_score") or row.get("direction_score"),
            "dominant_force": dna.get("dominant_force"),
            "dominant_contribution": dna.get("dominant_contribution"),
            "dominant_share": dna.get("dominant_share"),
            "risk_total": dna.get("risk_total"),
            "quality": dna.get("data_quality") or row.get("direction_quality"),
            "conflict": dna.get("conflict") or row.get("direction_conflict"),
            "learning_delta": calibration.get("delta"),
            "learning_gate": calibration.get("gate"),
            "schema": dna.get("schema") or row.get("learning_schema"),
        })
    return out


def _profile_rows(limit: int = 80) -> List[Dict[str, Any]]:
    profiles = list(load_profiles().values())
    out = []
    for p in profiles[:limit]:
        family_learning = p.get("family_learning") if isinstance(p.get("family_learning"), dict) else {}
        active = []
        for name, row in family_learning.items():
            if not isinstance(row, dict) or int(row.get("count") or 0) < 8:
                continue
            active.append(f"{name}:{float(row.get('active_multiplier') or 1.0):.3f}")
        out.append({
            "ticker": p.get("ticker"),
            "approved_bias": p.get("approved_bias"),
            "suggested_bias": p.get("suggested_bias"),
            "avg_abs_error_pct": p.get("avg_abs_error_pct"),
            "audit_count": p.get("audit_count") or p.get("foreign_audit_count"),
            "direction_audits": p.get("direction_audit_count"),
            "direction_hit_rate": p.get("direction_hit_rate"),
            "learning_maturity": p.get("learning_maturity"),
            "active_family_count": p.get("active_family_count"),
            "active_family_multipliers": "｜".join(active[:6]),
            "updated_at_tw": p.get("updated_at_tw") or p.get("approved_at_tw"),
        })
    return out


def _storage_rows() -> List[Dict[str, Any]]:
    init_report = ensure_memory_initialized_bootsafe(migrate=False)
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
    """Admin-only, memory-safe learning dashboard.

    Only the selected section is computed/rendered. Streamlit tabs execute all
    tab bodies on every rerun, which previously multiplied JSONL/Pandas/PyArrow
    memory and could crash Community Cloud when switching pages.
    """
    try:
        st.session_state["memory_init_report"] = ensure_memory_initialized_bootsafe(migrate=False)
    except Exception:
        pass
    if not bool(st.session_state.get("admin_authenticated", False)):
        st.warning("預測學習為 Admin-only 頁面。請先在側邊欄輸入 Admin Password。")
        return

    st.markdown("### 🧠 預測學習 / Learning Center")
    st.caption("正式預測由個股分析完成後立即寫入一次；本頁只負責查閱與 Audit，不會在切頁時重複寫入。")

    view = st.radio(
        "檢視區塊",
        ["總覽", "昨測今收", "正式樣本", "Prediction DNA", "Raw Log", "Bias History", "Storage Status"],
        horizontal=True,
        key="learning_center_view",
    )

    # Load only the bounded data needed by the selected view.  Storage/Bias pages
    # never read large JSONL files, preserving the RC3.3 rerun/crash guard.
    preds: List[Dict[str, Any]] = []
    audits: List[Dict[str, Any]] = []
    ledger_preds: List[Dict[str, Any]] = []
    ledger_audits: List[Dict[str, Any]] = []
    if view in {"總覽", "正式樣本", "Prediction DNA", "Raw Log"}:
        ledger_preds, _ = _ledger_recovery_rows()
        preds = _merge_memory_rows(read_prediction_log(1200), ledger_preds, 1200)
    if view in {"總覽", "昨測今收"}:
        _, ledger_audits = _ledger_recovery_rows()
        audits = _merge_memory_rows(read_audit_log(1200), ledger_audits, 1200)
    preds_30 = [p for p in preds if _is_recent(p, ("run_time_tw", "run_date_tw"), 30) and p.get("skipped") is not True]
    audits_30 = [a for a in audits if _is_recent(a, ("audit_time_tw", "audit_date_tw"), 30)]
    formal_30 = _latest_formal_samples(preds_30) if preds_30 else []

    if view == "總覽":
        kpi = _kpi(preds_30, audits_30, formal_30)
        _small_metric_cards(st, [
            ("總分析次數", kpi["total_analysis"], "近30日全部分析。"),
            ("正式預測樣本", kpi["formal_samples"], "同股同交易日同預測類型只保留最後一次。"),
            ("完成 Auto Audit", kpi["audited_samples"], "已有 actual close 的樣本。"),
            ("平均誤差", "--" if kpi["avg_abs_error_pct"] is None else f"{kpi['avg_abs_error_pct']:.2f}%", "近30日平均絕對誤差。"),
            ("平均 Bias", "--" if kpi["avg_bias_pct"] is None else f"{kpi['avg_bias_pct']:+.2f}%", "正值代表模型偏保守。"),
        ])
        st.info("選擇上方區塊後才載入詳細表格，避免切頁時同時建立多個大型 DataFrame。")
        return

    if view == "昨測今收":
        st.markdown("#### 昨測今收 / Recent T1 audits")
        _df(st, _recent_t1_audits(audits_30, 40), "近30日尚無昨測今收 Audit。", height=360)
    elif view == "正式樣本":
        st.markdown("#### 正式預測樣本（最後一次正式分析）")
        _df(st, _formal_rows(formal_30, 100), "近30日尚無正式預測樣本。", height=420)
    elif view == "Prediction DNA":
        st.markdown("#### Prediction DNA / 因子主導力")
        st.caption("只顯示精簡 DNA 摘要；完整因子快照保留在 prediction_log.jsonl。")
        _df(st, _dna_rows(formal_30, 100), "近30日尚無 Prediction DNA。", height=440)
    elif view == "Raw Log":
        st.markdown("#### Raw prediction log")
        _df(st, _raw_rows(preds_30, 100), "近30日尚無 raw prediction log。", height=460)
    elif view == "Bias History":
        st.markdown("#### Bias / Ticker profile")
        _df(st, _profile_rows(100), "尚無 ticker profile。", height=420)
    elif view == "Storage Status":
        st.markdown("#### Storage Guard")
        st.caption(f"Memory path：{MEMORY_DIR}")
        _df(st, _storage_rows(), "尚無 storage 狀態。", height=360)
        st.markdown("#### Auto Audit Time Guard")
        st.caption("主畫面維持零背景工作；僅在此處由 Admin 小批次執行，避免 Streamlit rerun 疊加。")
        c1, c2 = st.columns([0.34, 0.66], gap="small")
        with c1:
            run_audit = st.button(
                "執行安全 Auto Audit（每市場最多5檔）",
                key="learning_safe_auto_audit",
                use_container_width=True,
            )
        if run_audit:
            with st.spinner("正在比對正式收盤，完成前不切換頁面…"):
                try:
                    st.session_state["learning_safe_auto_audit_result"] = execute_due_auto_audit_once(
                        markets=["TW", "US"], max_tickers_per_market=5
                    )
                except Exception as exc:
                    st.session_state["learning_safe_auto_audit_result"] = {
                        "status": "failed_safe", "reason": f"{type(exc).__name__}: {exc}", "markets": {}
                    }
        result = st.session_state.get("learning_safe_auto_audit_result")
        if isinstance(result, dict):
            market_rows = list((result.get("markets") or {}).values())
            done = sum(int(r.get("audited_t1") or 0) + int(r.get("audited_today") or 0) for r in market_rows if isinstance(r, dict))
            if result.get("status") == "done":
                st.success(f"安全 Auto Audit 完成：新增 {done} 筆稽核。")
            elif result.get("status") == "busy":
                st.warning("已有一批 Auto Audit 執行中，未重複啟動。")
            else:
                st.warning(str(result.get("reason") or "Auto Audit 已安全停止。"))
        _df(st, auto_audit_status_rows(), "尚無 Auto Audit 排程紀錄。", height=240)
        try:
            ledger = load_ledger(DEFAULT_LEDGER_PATH, initialize_if_missing=False)
            wc = ledger.get("watch_center", {}) if isinstance(ledger, dict) else {}
            st.caption(f"Watch symbols：{len(wc.get('symbols', []) or [])}｜Hidden symbols：{len(wc.get('hidden_symbols', []) or [])}")
        except Exception as exc:
            st.warning(f"Ledger 讀取失敗：{type(exc).__name__}: {exc}")
