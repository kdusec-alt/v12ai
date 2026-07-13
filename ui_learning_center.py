# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from memory_store import MEMORY_DIR, read_prediction_log, read_audit_log
from tino_persistent_store import (
    DEFAULT_LEDGER_PATH,
    load_ledger,
    storage_status,
    ensure_memory_initialized_bootsafe,
)
from learning_center_core import (
    _MAX_LOG_ROWS,
    _normalize_prediction,
    _normalize_audit,
    _is_recent,
    _latest_formal_samples,
    _kpi,
    _metric_cards,
    _html_table,
    _recent_t1_audits,
    _formal_rows,
    _dna_rows,
    _raw_rows,
    _profile_rows,
    _storage_rows,
    _safe_int,
    auto_audit_status_rows,
    execute_due_auto_audit_once,
)


_PREDICTION_VIEWS = {"總覽", "正式樣本", "Prediction DNA", "Raw Log"}
_AUDIT_VIEWS = {"總覽", "昨測今收"}


def _inject_learning_css(st) -> None:
    st.markdown(
        """
        <style>
        [data-testid="stRadio"] label, [data-testid="stRadio"] p,
        [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {
            color:#d8e9f7 !important; opacity:1 !important;
        }
        [data-testid="stMetricLabel"], [data-testid="stMetricValue"] {
            color:#eaf6ff !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_admin_error(st, title: str, exc: Exception) -> None:
    st.error(title)
    with st.expander("Admin 診斷", expanded=False):
        st.code(f"{type(exc).__name__}: {exc}")


def _render_section_error(st, view: str, exc: Exception) -> None:
    """Stop only the selected Learning Center section."""
    st.error(f"{view} 區塊暫時無法讀取；其他頁籤與主分析不受影響。")
    with st.expander(f"{view} 診斷", expanded=False):
        st.code(f"{type(exc).__name__}: {exc}")


def _load_learning_data(view: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Read each log from its canonical repository exactly once per selected view.

    The ledger is a recovery index only.  Boot initialization restores it into
    JSONL when the active file is empty, so merging it again during rendering
    would create a second count/de-duplication path.
    """
    predictions = read_prediction_log(_MAX_LOG_ROWS) if view in _PREDICTION_VIEWS else []
    audits = read_audit_log(_MAX_LOG_ROWS) if view in _AUDIT_VIEWS else []
    return predictions, audits


def _normalize_rows(rows: List[Dict[str, Any]], normalizer) -> List[Dict[str, Any]]:
    """Skip one malformed historical row without failing the selected tab."""
    output: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            normalized = normalizer(row)
        except Exception:
            continue
        if isinstance(normalized, dict) and normalized:
            output.append(normalized)
    return output


def _render_learning_view(st, view: str) -> None:
    predictions, audits = _load_learning_data(view)
    normalized_predictions = _normalize_rows(predictions, _normalize_prediction)
    normalized_audits = _normalize_rows(audits, _normalize_audit)
    recent_predictions = [
        row for row in normalized_predictions
        if _is_recent(row, 30) and row.get("skipped") is not True
    ]
    recent_audits = [row for row in normalized_audits if _is_recent(row, 30)]
    formal_all = _latest_formal_samples(normalized_predictions)
    formal_recent = [row for row in formal_all if _is_recent(row, 30)]

    if view == "總覽":
        kpi = _kpi(recent_predictions, recent_audits, formal_recent)
        _metric_cards(st, [
            ("總分析次數", kpi["total_analysis"], "近30日全部分析。"),
            ("正式預測樣本", kpi["formal_samples"], "同股同交易日同類型只保留最後一次。"),
            ("Prediction DNA", kpi["dna_samples"], "具有可稽核因子快照的正式樣本。"),
            ("完成 Auto Audit", kpi["audited_samples"], "已有正式收盤結果的樣本。"),
            ("平均誤差", "--" if kpi["avg_abs_error_pct"] is None else f"{kpi['avg_abs_error_pct']:.2f}%", "近30日平均絕對誤差。"),
            ("平均 Bias", "--" if kpi["avg_bias_pct"] is None else f"{kpi['avg_bias_pct']:+.2f}%", "正值代表模型偏保守。"),
        ])
        if not normalized_predictions:
            st.warning("目前讀不到 Prediction Log。請到 Storage Status 確認記憶路徑與長期儲存狀態。")
        elif not formal_recent and formal_all:
            st.info(f"已找到 {len(formal_all)} 筆歷史正式樣本，但不在近30日範圍。")
        else:
            st.info("各明細區塊只在點選後載入，且完全不建立 Pandas / PyArrow DataFrame。")
        return

    if view == "昨測今收":
        st.markdown("#### 昨測今收 / Recent T1 audits")
        _html_table(
            st,
            _recent_t1_audits(recent_audits, 40),
            "近30日尚無昨測今收 Audit。",
            columns=[
                "ticker", "target_date", "prediction_time", "predicted_close", "actual_close",
                "error_pct", "direction_hit", "close_in_range", "tail_breach_pct", "result", "source",
            ],
            height=420,
        )
        return

    if view == "正式樣本":
        st.markdown("#### 正式預測樣本（最後一次正式分析）")
        display = formal_recent or formal_all
        if not formal_recent and formal_all:
            st.caption("近30日無樣本，以下顯示目前仍可恢復的最近歷史樣本。")
        _html_table(
            st,
            _formal_rows(display, 80),
            "目前沒有可讀取的正式預測樣本。",
            columns=[
                "ticker", "run_time_tw", "target_trade_date", "target_kind", "today_close_est",
                "next_close_est", "direction", "direction_score", "confidence", "session_mode", "id",
            ],
            height=440,
        )
        return

    if view == "Prediction DNA":
        st.markdown("#### Prediction DNA / 因子主導力")
        st.caption("顯示精簡、純量化 DNA 摘要；完整因子快照仍保留在 prediction_log.jsonl。")
        display = formal_recent or formal_all
        _html_table(
            st,
            _dna_rows(display, 80),
            "目前沒有可讀取的 Prediction DNA。舊版樣本仍可在 Raw Log 查看。",
            columns=[
                "ticker", "target_date", "direction", "direction_score", "dominant_force",
                "dominant_value", "dominant_share", "top_factors", "family_force", "risk_total",
                "learning_delta", "learning_gate",
            ],
            height=470,
        )
        return

    if view == "Raw Log":
        st.markdown("#### Raw prediction log")
        _html_table(
            st,
            _raw_rows(recent_predictions or normalized_predictions, 80),
            "目前沒有可讀取的 raw prediction log。",
            columns=[
                "ticker", "run_time_tw", "target_trade_date", "session_mode", "t0", "t1",
                "direction", "score", "confidence", "schema", "id",
            ],
            height=470,
        )
        return

    if view == "Bias History":
        st.markdown("#### Bias / Ticker profile")
        _html_table(
            st,
            _profile_rows(80),
            "尚無 ticker profile；需先完成正式 T+1 Audit 才會累積。",
            columns=[
                "ticker", "audit_count", "direction_audits", "direction_hit_rate", "avg_abs_error_pct",
                "learning_maturity", "active_family_count", "active_multipliers", "suggested_bias",
                "approved_bias", "updated_at_tw",
            ],
            height=450,
        )
        return

    if view == "Storage Status":
        st.markdown("#### Storage Guard")
        st.caption(f"Active memory path：{MEMORY_DIR}")
        _html_table(
            st,
            _storage_rows(),
            "尚無 storage 狀態。",
            columns=["item", "status", "value", "note"],
            height=360,
            limit=20,
        )
        try:
            status = storage_status(DEFAULT_LEDGER_PATH)
            if not status.get("remote_configured"):
                st.warning("目前為 LOCAL_ONLY：重新部署或容器重建後，本地學習紀錄可能消失。若要跨部署保留，需設定 TINO_GITHUB_TOKEN 與 TINO_GITHUB_REPO。")
        except Exception:
            pass

        st.markdown("#### Auto Audit Time Guard")
        st.caption("主畫面維持零背景工作；只在此處由 Admin 小批次執行，避免 Streamlit rerun 疊加。")
        left, _right = st.columns([0.34, 0.66], gap="small")
        with left:
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
                        "status": "failed_safe",
                        "reason": f"{type(exc).__name__}: {exc}",
                        "markets": {},
                    }

        result = st.session_state.get("learning_safe_auto_audit_result")
        if isinstance(result, dict):
            market_rows = [row for row in (result.get("markets") or {}).values() if isinstance(row, dict)]
            done = sum(_safe_int(row.get("audited_t1")) + _safe_int(row.get("audited_today")) for row in market_rows)
            if result.get("status") == "done":
                st.success(f"安全 Auto Audit 完成：新增 {done} 筆稽核。")
            elif result.get("status") == "busy":
                st.warning("已有一批 Auto Audit 執行中，未重複啟動。")
            else:
                st.warning(str(result.get("reason") or "Auto Audit 已安全停止。"))

        try:
            audit_rows = auto_audit_status_rows()
        except Exception as exc:
            audit_rows = [{"market": "ALL", "status": "failed_safe", "reason": f"{type(exc).__name__}: {exc}"}]
        _html_table(
            st,
            audit_rows,
            "尚無 Auto Audit 排程紀錄。",
            columns=[
                "market", "trade_date", "status", "attempt_at_tw", "pending_t1", "pending_today",
                "audited_t1", "audited_today", "reason",
            ],
            height=260,
            limit=10,
        )
        try:
            ledger = load_ledger(DEFAULT_LEDGER_PATH, initialize_if_missing=False)
            watch = ledger.get("watch_center", {}) if isinstance(ledger, dict) else {}
            st.caption(
                f"Watch symbols：{len(watch.get('symbols', []) or [])}｜"
                f"Hidden symbols：{len(watch.get('hidden_symbols', []) or [])}"
            )
        except Exception as exc:
            st.warning(f"Ledger 暫時無法讀取：{type(exc).__name__}")
        return


def _render_learning_center_impl(st) -> None:
    _inject_learning_css(st)
    if not bool(st.session_state.get("admin_authenticated", False)):
        st.warning("預測學習為 Admin-only 頁面。請先在側邊欄輸入 Admin Password。")
        return

    st.markdown("### 🧠 預測學習 / Learning Center")
    st.caption("正式預測由個股分析完成後立即寫入一次；本頁只查閱與稽核，不會在切頁時重複寫入。")

    view = st.radio(
        "檢視區塊",
        ["總覽", "昨測今收", "正式樣本", "Prediction DNA", "Raw Log", "Bias History", "Storage Status"],
        horizontal=True,
        key="learning_center_view",
    )

    try:
        _render_learning_view(st, view)
    except Exception as exc:
        _render_section_error(st, view, exc)


def render_learning_center(st) -> None:
    """RC4.7 crash-isolated Learning Center.

    A malformed row or one failed selected section is contained inside that
    section.  It cannot take the main stock-analysis app offline.
    """
    try:
        if "memory_init_report" not in st.session_state:
            try:
                st.session_state["memory_init_report"] = ensure_memory_initialized_bootsafe(migrate=False)
            except Exception:
                st.session_state["memory_init_report"] = {"status": "DEGRADED"}
        _render_learning_center_impl(st)
    except Exception as exc:
        _render_admin_error(st, "預測學習外框已安全停止；個股分析與即時股價不受影響。", exc)
