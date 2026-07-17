# -*- coding: utf-8 -*-
from __future__ import annotations

import gc
import hmac
import os
import html
from pathlib import Path
from typing import Any, Dict, List

from debug_trace import trace_to_text
from memory_store import MEMORY_DIR, PREDICTION_LOG, AUDIT_LOG, TICKER_PROFILE


_EVENT_WATCH_SESSION_KEYS = (
    "event_reassessment_queue",
    "event_news_baseline",
    "event_baseline_created_at",
    "event_reassessment_notice",
    "last_event_watch_report",
)


def _clear_event_watch_session(st) -> None:
    """Remove all event-watch state when the Admin session is closed."""
    for key in _EVENT_WATCH_SESSION_KEYS:
        st.session_state.pop(key, None)


def _secret_value(st, key: str) -> str:
    try:
        val = st.secrets.get(key, "")
    except Exception:
        val = ""
    return str(val or os.environ.get(key, ""))


def _admin_gate(st) -> bool:
    st.sidebar.title("Tino Admin Console")
    if "admin_authenticated" not in st.session_state:
        st.session_state.admin_authenticated = False

    configured = _secret_value(st, "ADMIN_PASSWORD")
    if not configured:
        st.sidebar.warning("Admin Password 尚未設定")
        with st.sidebar.expander("設定方式", expanded=False):
            st.code('ADMIN_PASSWORD = "請換成你的密碼"', language="toml")
            st.caption("請放在 Streamlit Secrets；未設定前後台功能保持鎖定。")
        return False

    if st.session_state.admin_authenticated:
        st.sidebar.success("Admin 已登入")
        if st.sidebar.button("登出 Admin", key="tino_admin_logout"):
            st.session_state.admin_authenticated = False
            st.session_state.pop("tino_admin_password", None)
            _clear_event_watch_session(st)
            return False
        return True

    pwd = st.sidebar.text_input("Admin Password", type="password", key="tino_admin_password")
    c1, c2 = st.sidebar.columns([1, 1])
    with c1:
        login = st.button("Login", key="tino_admin_login")
    with c2:
        st.caption("後台鎖定")

    if login:
        if hmac.compare_digest(str(pwd or ""), configured):
            st.session_state.admin_authenticated = True
            st.session_state.pop("tino_admin_password", None)
            return True
        st.sidebar.error("密碼錯誤")
    return False


def _html_table(st, rows: List[Dict[str, Any]], empty_text: str, limit: int = 12) -> None:
    """Small HTML table, avoiding pandas/pyarrow DataFrame in the sidebar."""
    if not rows:
        st.caption(empty_text)
        return

    rows = rows[:limit]
    keys: List[str] = []
    for r in rows:
        for k in (r or {}).keys():
            if k not in keys:
                keys.append(k)
        if len(keys) >= 8:
            break
    keys = keys[:8]

    header = "".join(
        f"<th style='padding:4px 6px;border-bottom:1px solid #31465d;color:#fff5c4;text-align:left'>{html.escape(str(k))}</th>"
        for k in keys
    )
    body = []
    for r in rows:
        body.append(
            "<tr>"
            + "".join(
                f"<td style='padding:4px 6px;border-bottom:1px solid #203144;color:#eaf6ff;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap'>{html.escape(str((r or {}).get(k, '')))}</td>"
                for k in keys
            )
            + "</tr>"
        )

    st.markdown(
        "<div style='background:#071727;border:1px solid #203144;border-radius:8px;overflow:auto;margin:6px 0;max-height:280px'>"
        "<table style='width:100%;border-collapse:collapse;font-size:11px;line-height:1.25'>"
        f"<thead><tr>{header}</tr></thead><tbody>{''.join(body)}</tbody>"
        "</table></div>",
        unsafe_allow_html=True,
    )


def _admin_debug_kv(st, payload: Dict[str, Any]):
    rows = []
    for k, v in (payload or {}).items():
        vv = "NULL" if v is None else str(v)
        rows.append(
            "<tr>"
            f"<td style='padding:4px 8px;border-bottom:1px solid #d9d9d9;font-weight:700;color:#111;background:#f7f7f7'>{html.escape(str(k))}</td>"
            f"<td style='padding:4px 8px;border-bottom:1px solid #d9d9d9;color:#111;background:#fff'>{html.escape(vv)}</td>"
            "</tr>"
        )
    st.markdown(
        "<div style='background:#ffffff;color:#111;border:1px solid #cfcfcf;border-radius:8px;overflow:hidden;margin:6px 0 10px 0;'>"
        "<table style='width:100%;border-collapse:collapse;font-size:12px;line-height:1.25;'>"
        + "".join(rows) +
        "</table></div>",
        unsafe_allow_html=True,
    )


def _file_line_count(path: Path, max_scan: int = 200000) -> int:
    try:
        if not path.exists():
            return 0
        n = 0
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for n, _ in enumerate(f, start=1):
                if n >= max_scan:
                    break
        return n
    except Exception:
        return 0


def _file_status_rows() -> List[Dict[str, Any]]:
    rows = []
    for p in (PREDICTION_LOG, AUDIT_LOG, TICKER_PROFILE):
        try:
            path = Path(p)
            rows.append({
                "file": path.name,
                "exists": path.exists(),
                "size_kb": round((path.stat().st_size if path.exists() else 0) / 1024, 1),
                "lines": _file_line_count(path) if path.suffix == ".jsonl" else "",
            })
        except Exception as exc:
            rows.append({"file": str(p), "error": f"{type(exc).__name__}: {exc}"})
    return rows


def _run_admin_auto_audit_maintenance(st) -> None:
    """Run a tiny close-time audit once per market/date in this Admin session.

    This intentionally has no worker, timer, pandas object or full-log cache.
    Before market close it performs only a clock check.  After close it scans a
    bounded JSONL tail and audits at most three tickers for the due market.
    Any failure is isolated to this sidebar status and can never take down the
    main Streamlit application.
    """
    attempted = st.session_state.get("tino_admin_auto_audit_attempted")
    if not isinstance(attempted, dict):
        attempted = {}
        st.session_state["tino_admin_auto_audit_attempted"] = attempted

    try:
        from auto_audit_scheduler import (
            execute_due_auto_audit_once,
            maybe_run_auto_audit_time_guard,
        )

        guard = maybe_run_auto_audit_time_guard(markets=["TW", "US"], execute=False)
        due_markets: List[str] = []
        due_keys: Dict[str, str] = {}
        for market, meta in (guard.get("markets") or {}).items():
            if not isinstance(meta, dict) or not bool(meta.get("ready")):
                continue
            trade_date = str(meta.get("trade_date") or "")
            key = f"{market}:{trade_date}"
            due_keys[str(market)] = key
            if not attempted.get(key):
                due_markets.append(str(market))

        if due_markets:
            st.sidebar.info("Learning 維護中：收盤後安全小批次…")
            result = execute_due_auto_audit_once(
                markets=due_markets,
                max_tickers_per_market=3,
                scan_limit=300,
                apply_safe_learning=True,
            )

            market_rows = result.get("markets") or {}
            audited = 0
            pending = 0
            errors = 0
            statuses: List[str] = []
            for market in due_markets:
                row = market_rows.get(market) if isinstance(market_rows, dict) else {}
                row = row if isinstance(row, dict) else {}
                status = str(row.get("status") or result.get("status") or "unknown")
                statuses.append(f"{market}:{status}")
                audited += int(row.get("audited_t1") or 0) + int(row.get("audited_today") or 0)
                pending += int(row.get("pending_t1") or 0) + int(row.get("pending_today") or 0)
                errors += int(row.get("errors") or 0)
                # Busy is transient; allow the next rerun to retry. Every other
                # result is marked once for this Admin browser session.
                if status != "busy":
                    attempted[due_keys.get(market, market)] = status

            st.session_state["tino_admin_auto_audit_summary"] = {
                "markets": ", ".join(statuses),
                "audited": audited,
                "pending": pending,
                "errors": errors,
                "attempt_at_tw": str(result.get("attempt_at_tw") or guard.get("attempt_at_tw") or ""),
            }
            # Do not retain actual-price snapshots or Prediction DNA rows in
            # session_state after the batch has completed.
            del market_rows
            del result
            gc.collect()

        summary = st.session_state.get("tino_admin_auto_audit_summary")
        if isinstance(summary, dict) and summary:
            audited = int(summary.get("audited") or 0)
            errors = int(summary.get("errors") or 0)
            markets = str(summary.get("markets") or "")
            if errors:
                st.sidebar.warning(f"Auto Audit 已安全完成｜新增 {audited}｜錯誤 {errors}｜{markets}")
            elif audited:
                st.sidebar.success(f"Auto Audit 完成｜新增 {audited} 筆｜{markets}")
            else:
                st.sidebar.caption(f"Auto Audit：目前無新增樣本｜{markets}")
        else:
            st.sidebar.caption("Auto Audit：登入後僅於收盤時段檢查；每市場最多3檔。")
    except Exception as exc:
        # Remember only a short scalar error. Never keep tracebacks or payloads
        # in session_state, and never let maintenance crash the application.
        st.session_state["tino_admin_auto_audit_summary"] = {
            "markets": "",
            "audited": 0,
            "pending": 0,
            "errors": 1,
            "attempt_at_tw": "",
        }
        st.sidebar.warning(f"Auto Audit 維護暫緩：{type(exc).__name__}")
        gc.collect()


def _learning_panel(st, forecast):
    """RC3.3 light Auto-Learning panel.

    The previous sidebar panel read JSONL logs and built several DataFrames on
    every rerun.  On Community Cloud this raised RSS and could crash the native
    Streamlit/PyArrow path with Segmentation fault.  Formal prediction logging
    remains in app.py; this panel is now a lightweight control/status surface.
    """
    st.sidebar.markdown("**Auto-Learning Audit｜輕量模式**")

    enabled = st.sidebar.checkbox(
        "啟用 Auto-Learning 記錄",
        value=bool(st.session_state.get("learning_log_enabled", True)),
        key="learning_log_enabled",
        help="開啟後，每次個股分析完成會由 app.py 寫入一次正式 Prediction Log。",
    )

    if enabled:
        st.sidebar.success("Auto-Learning：啟用")
    else:
        st.sidebar.info("Auto-Learning：暫停記錄")
        return

    if forecast and not getattr(forecast, "stopped", False):
        try:
            from learning import prediction_signature
            sig = prediction_signature(forecast)
            st.sidebar.caption(f"目前快照簽章：{sig}")
        except Exception as exc:
            st.sidebar.caption(f"快照簽章暫不可用：{type(exc).__name__}")
    else:
        st.sidebar.caption("尚未有 forecast；按『個股分析』後會自動寫入。")

    with st.sidebar.expander("Storage Status", expanded=False):
        _html_table(st, _file_status_rows(), "尚無 storage status。", limit=5)
        st.caption(f"Memory path：{MEMORY_DIR}")

    if st.sidebar.checkbox("載入手動補寫工具", value=False, key="load_manual_learning_tools"):
        if forecast and not getattr(forecast, "stopped", False):
            if st.sidebar.button("補寫目前預測快照", key="tino_safe_snapshot_write"):
                try:
                    from learning import log_prediction
                    row = log_prediction(forecast)
                    st.sidebar.success(f"已寫入：{row.get('id')}")
                except Exception as exc:
                    st.sidebar.error(f"補寫失敗：{type(exc).__name__}: {exc}")
        else:
            st.sidebar.caption("沒有 forecast，無法補寫。")

    if st.sidebar.checkbox("載入重型 Auto Audit 工具", value=False, key="load_heavy_auto_audit_tools"):
        st.sidebar.warning("這會讀取歷史紀錄並抓收盤資料；建議收盤後再使用。")
        use_market_foreign = st.sidebar.checkbox("同步開獎外資 V2", value=False, key="auto_all_use_foreign")
        market_foreign = st.sidebar.number_input("官方大盤外資買賣超（億）", value=0.0, step=10.0, key="auto_all_foreign_actual")
        auto_apply_learning = st.sidebar.checkbox("套用安全校正", value=True, key="auto_all_apply_learning")
        if st.sidebar.button("🚀 Auto Audit 小批次開獎", key="tino_auto_audit_safe_batch"):
            try:
                from auto_audit_scheduler import execute_due_auto_audit_once
                result = execute_due_auto_audit_once(
                    markets=["TW", "US"],
                    max_tickers_per_market=5,
                    apply_safe_learning=auto_apply_learning,
                    actual_foreign_billion=market_foreign if use_market_foreign else None,
                )
                market_rows = list((result.get("markets") or {}).values())
                t1_count = sum(int(row.get("audited_t1") or 0) for row in market_rows if isinstance(row, dict))
                today_count = sum(int(row.get("audited_today") or 0) for row in market_rows if isinstance(row, dict))
                st.sidebar.success(f"完成｜T1 {t1_count}｜Today {today_count}｜安全批次每市場最多5檔")
                errors = [row for row in market_rows if isinstance(row, dict) and row.get("status") == "failed_safe"]
                if errors:
                    _html_table(st, errors, "", limit=5)
            except Exception as exc:
                st.sidebar.error(f"Auto Audit 失敗：{type(exc).__name__}: {exc}")


def _mis_debug_panel(st, forecast):
    if not forecast or not getattr(forecast, "decision_card", None):
        st.sidebar.caption("MIS Debug：尚無 forecast。")
        return
    price_meta = (forecast.decision_card or {}).get("_price_meta") or {}
    mis_debug = price_meta.get("mis_debug") or {}
    with st.sidebar.expander("MIS Price Debug", expanded=True):
        st.caption("只顯示在 Admin Debug；前台不顯示工程字串。")
        st.markdown("**Selected price source**")
        _admin_debug_kv(st, {
            "price_source": price_meta.get("source", ""),
            "price_status": price_meta.get("status", ""),
            "price_label": price_meta.get("label", ""),
            "decision_blocked": bool(price_meta.get("decision_blocked", False)),
        })
        st.markdown("**TWSE/TPEX MIS trace**")
        if mis_debug:
            ordered = {
                "mis_tried": mis_debug.get("mis_tried"),
                "mis_market": mis_debug.get("mis_market"),
                "mis_symbol": mis_debug.get("mis_symbol"),
                "mis_http_status": mis_debug.get("mis_http_status"),
                "mis_raw_ok": mis_debug.get("mis_raw_ok"),
                "mis_raw_rows": mis_debug.get("mis_raw_rows"),
                "mis_parsed_last": mis_debug.get("mis_parsed_last"),
                "mis_parsed_high": mis_debug.get("mis_parsed_high"),
                "mis_parsed_low": mis_debug.get("mis_parsed_low"),
                "mis_parsed_time": mis_debug.get("mis_parsed_time"),
                "mis_reject_reason": mis_debug.get("mis_reject_reason"),
                "fallback_used": price_meta.get("source", ""),
            }
            _admin_debug_kv(st, ordered)
        else:
            st.warning("尚未收到 mis_debug。請重新按一次『開始分析』。")


def render_admin(st, forecast):
    authed = _admin_gate(st)
    if not authed:
        return "neutral", False, True, False

    _run_admin_auto_audit_maintenance(st)

    macro = st.sidebar.selectbox("Macro 手動偏壓", ["neutral", "bullish", "bearish"], index=0)
    auto = st.sidebar.checkbox("Auto Analyze", value=False, help="預設關閉，避免開頁就抓外部資料。")
    live = st.sidebar.checkbox("Live Data / News", value=True, help="關閉時使用離線樣本，方便先確認系統可開啟。")

    if forecast:
        load_diagnostics = st.sidebar.checkbox(
            "載入 Prediction Trace / Truth Guard",
            value=False,
            key="load_admin_diagnostics",
            help="預設關閉；需要除錯時才載入。",
        )
        if load_diagnostics:
            with st.sidebar.expander("Prediction Trace", expanded=True):
                try:
                    if getattr(forecast, "raw", None):
                        st.code(trace_to_text(
                            forecast.trace.steps,
                            forecast.trace.raw_t1 or 0,
                            forecast.trace.final_t1 or 0,
                        ))
                    _html_table(st, forecast.trace.to_rows(), "尚無 trace。", limit=10)
                except Exception as exc:
                    st.warning(f"Trace 暫時無法載入：{type(exc).__name__}: {exc}")
            with st.sidebar.expander("Dashboard Truth Guard", expanded=False):
                try:
                    truths = [x.__dict__ for x in (getattr(forecast, "data_truths", None) or [])]
                    _html_table(st, truths, "尚無資料真實性紀錄。", limit=10)
                except Exception as exc:
                    st.warning(f"Truth Guard 暫時無法載入：{type(exc).__name__}: {exc}")
        else:
            st.sidebar.caption("Trace / Truth Guard 已降載。")

    show_learning_admin = st.sidebar.checkbox(
        "開啟 Auto-Learning 管理面板",
        value=False,
        key="show_learning_admin_panel",
        help="輕量版：不自動讀取大型 JSONL / DataFrame。",
    )
    if show_learning_admin:
        _learning_panel(st, forecast)
    else:
        st.sidebar.caption("Auto-Learning 正式快照會在每次分析完成後自動寫入。")

    debug = st.sidebar.checkbox("Debug Mode", value=False)
    if debug:
        _mis_debug_panel(st, forecast)
    return macro, auto, live, debug
