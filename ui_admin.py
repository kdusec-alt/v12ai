# -*- coding: utf-8 -*-
from __future__ import annotations

import hmac
import os
import html
from typing import Any, Dict

import pandas as pd
from debug_trace import trace_to_text
from learning import (
    approve_profile_bias,
    audit_latest_prediction_for_ticker,
    get_profile,
    log_prediction,
    prediction_signature,
    recent_learning_tables,
    reset_profile_bias,
    suggest_from_forecast,
    today_prediction_vs_actual,
    t1_prediction_vs_actual,
    two_click_close_audit,
    prediction_audit_dashboard,
    pending_auto_audit_summary,
    auto_audit_queried_predictions,
)
from memory_store import MEMORY_DIR


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
        if st.sidebar.button("登出 Admin"):
            st.session_state.admin_authenticated = False
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


def _df(st, rows, empty_text: str):
    if not rows:
        st.caption(empty_text)
        return
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def _admin_debug_kv(st, payload: Dict[str, Any]):
    """Light card with black text for Admin Debug readability in dark Streamlit theme."""
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


def _learning_panel(st, forecast):
    with st.sidebar.expander("Auto-Learning Audit", expanded=False):
        enabled = st.checkbox(
            "啟用 Auto-Learning 記錄",
            value=bool(st.session_state.get("learning_log_enabled", True)),
            key="learning_log_enabled",
            help="只記錄預測快照與 Audit，不會自動改主程式；需 Tino Approve 才會影響下次分析。",
        )
        if not enabled:
            st.info("Auto-Learning Log 目前關閉。開啟後才會寫入 prediction snapshot。")
            return

        logged_row = None
        if forecast and not getattr(forecast, "stopped", False):
            try:
                sig = prediction_signature(forecast)
                # app.py writes the formal snapshot in the same Analyze click.
                # The Admin panel is read-only here to avoid duplicate I/O on every rerun.
                st.success(f"Learning 已啟用｜目前預測快照：{sig}")
            except Exception as exc:
                st.warning(f"Learning snapshot 暫時無法寫入：{type(exc).__name__}: {exc}")
        else:
            st.caption("尚未有 forecast。請先按『開始分析』，系統才會產生 prediction log。")

        suggestions = suggest_from_forecast(forecast) if forecast else []
        _df(st, [x.__dict__ for x in suggestions], "目前沒有可顯示的建議。")
        st.caption("流程已簡化：你查過的標的會自動寫入 Prediction Log；收盤後按一次 Auto Audit，系統會批次開獎、寫入 Audit、校正 profile。")

        st.markdown("**Auto Audit｜查過就入帳**")
        try:
            pending = pending_auto_audit_summary(1200)
            st.caption(f"已記錄 {pending.get('prediction_count', 0)} 筆｜待昨測今收 {pending.get('pending_t1_count', 0)}｜待今日VS實際 {pending.get('pending_today_count', 0)}")
            if pending.get("pending_tickers"):
                st.caption("待開獎：" + ", ".join(pending.get("pending_tickers", [])[:20]))
        except Exception as exc:
            st.warning(f"Pending summary 暫時無法讀取：{type(exc).__name__}: {exc}")
        use_market_foreign = st.checkbox("同步開獎外資 V2（需填官方大盤外資億）", value=False, key="auto_all_use_foreign")
        market_foreign = st.number_input("官方大盤外資買賣超（億；賣超請填負數）", value=0.0, step=10.0, key="auto_all_foreign_actual")
        auto_apply_learning = st.checkbox("Auto Audit 後自動套用安全校正", value=True, key="auto_all_apply_learning", help="只套用 capped profile/Foreign Flow calibration；不改 V9 UI 與主程式。")
        if st.button("🚀 Auto Audit：開獎所有已查股票", key="tino_auto_audit_all_queried"):
            try:
                result = auto_audit_queried_predictions(
                    limit=1200,
                    max_tickers=80,
                    apply_safe_learning=auto_apply_learning,
                    actual_foreign_billion=market_foreign if use_market_foreign else None,
                )
                st.success(
                    f"Auto Audit 完成｜昨測今收 {result.get('audited_t1_count',0)}｜今日VS實際 {result.get('audited_today_count',0)}｜外資 {result.get('audited_foreign_count',0)}｜已抓收盤 {result.get('fetched_ticker_count',0)} 檔"
                )
                if result.get("errors"):
                    _df(st, result.get("errors"), "")
            except Exception as exc:
                st.error(f"Auto Audit 失敗：{type(exc).__name__}: {exc}")

        if forecast and not getattr(forecast, "stopped", False):
            st.markdown("**手動單檔補寫（備用）**")
            st.caption("一般情況不用按；只有單檔需要手動補正式收盤價時才用。")
            if st.button("① 確認/寫入目前預測快照", key="tino_two_click_snapshot"):
                try:
                    row = log_prediction(forecast)
                    st.success(f"快照已確認：{row.get('id')}｜Target {row.get('target_trade_date')}")
                except Exception as exc:
                    st.error(f"快照寫入失敗：{type(exc).__name__}: {exc}")

            actual_default = float((forecast.decision_card or {}).get("現價", 0.0) or getattr(forecast, "final_t0", 0.0) or 0.0)
            actual = st.number_input("正式收盤價", min_value=0.0, value=actual_default, step=0.01, key="two_click_actual_close")
            official_foreign = st.number_input("大盤外資實際買賣超（億，可空白用 0；賣超請填負數）", value=0.0, step=10.0, key="two_click_foreign_actual")
            use_foreign = st.checkbox("同步開獎外資 V2", value=True, key="two_click_use_foreign")
            apply_learning = st.checkbox("同步套用安全校正權重", value=True, key="two_click_apply_learning", help="個股 bias 建議上限 ±2%，且需至少20筆驗證樣本；外資金額倍率上限 0.75~1.30，不會改 UI。")
            if st.button("② 一鍵開獎＋寫入 Audit＋校正", key="tino_two_click_close_audit"):
                try:
                    result = two_click_close_audit(
                        forecast,
                        actual,
                        actual_foreign_billion=official_foreign if use_foreign else None,
                        apply_safe_learning=apply_learning,
                    )
                    t1 = result.get("t1_audit", {}) or {}
                    td = result.get("today_audit", {}) or {}
                    ff = result.get("foreign_flow_audit", {}) or {}
                    msg = []
                    if t1.get("status") == "audited":
                        hit = "方向命中" if t1.get("direction_hit") else "方向未命中"
                        msg.append(f"昨測今收 {float(t1.get('error_pct',0)):+.2f}%｜{hit}")
                    if td.get("status") == "audited": msg.append(f"今日VS實際 {float(td.get('error_pct',0)):+.2f}%")
                    if ff: msg.append(f"外資方向 {'命中' if ff.get('direction_hit') else '未命中'}｜級距 {ff.get('predicted_tier')}→{ff.get('actual_tier')}")
                    st.success("已寫入：" + "｜".join(msg or ["snapshot/audit/profile"]))
                except Exception as exc:
                    st.error(f"一鍵開獎失敗：{type(exc).__name__}: {exc}")

            st.markdown("**Prediction Audit Dashboard**")
            dash = prediction_audit_dashboard(300)
            _df(st, [{
                "Price Audits": dash.get("price_audit_count"),
                "T1 Audits": dash.get("t1_audit_count"),
                "T1 Avg Abs %": dash.get("t1_avg_abs_error_pct"),
                "T1 Direction Hit %": dash.get("t1_direction_hit_rate"),
                "T1 Direction Brier": dash.get("t1_direction_brier"),
                "Today Avg Abs %": dash.get("today_avg_abs_error_pct"),
                "Foreign Audits": dash.get("foreign_flow_audit_count"),
                "Foreign Dir Hit %": dash.get("foreign_direction_hit_rate"),
                "Foreign Tier Hit %": dash.get("foreign_tier_hit_rate"),
                "Foreign Avg Err 億": dash.get("foreign_avg_abs_error_billion"),
            }], "尚無 audit dashboard。")
            try:
                t1_cmp = t1_prediction_vs_actual(forecast, actual_default)
                if t1_cmp.get("display"):
                    st.caption(t1_cmp.get("display"))
                cmp = today_prediction_vs_actual(forecast, actual_default)
                if cmp.get("display"):
                    st.caption(cmp.get("display"))
            except Exception:
                pass

            profile = get_profile(forecast.ticker.resolved_symbol)
            if profile:
                st.markdown("**Ticker profile**")
                _df(st, [profile], "尚未建立個股 profile。")
                c1, c2 = st.columns([1, 1])
                with c1:
                    if st.button("Approve bias", key="approve_learning_bias"):
                        p = approve_profile_bias(forecast.ticker.resolved_symbol)
                        st.success(f"已核准偏壓：{float(p.get('approved_bias', 0.0)):+.2%}")
                with c2:
                    if st.button("Reset bias", key="reset_learning_bias"):
                        p = reset_profile_bias(forecast.ticker.resolved_symbol)
                        st.info(f"已歸零：{float(p.get('approved_bias', 0.0)):+.2%}")

        tables = recent_learning_tables(50)
        st.markdown("**Recent predictions**")
        _df(st, tables.get("predictions", [])[-10:], "尚無 prediction log。")
        st.markdown("**Recent audits**")
        _df(st, tables.get("audits", [])[-10:], "尚無 audit log。")
        st.markdown("**Storage status**")
        st.caption(f"Memory path：{MEMORY_DIR}")
        st.caption(f"Predictions：{len(tables.get('predictions', []))}｜Audits：{len(tables.get('audits', []))}｜Profiles：{len(tables.get('profiles', []))}")


def _mis_debug_panel(st, forecast):
    """Admin-only MIS diagnostics. Never render engineering strings on V9 front stage."""
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
            st.warning("尚未收到 mis_debug。請重新按一次『開始分析』，或確認已替換 v8.6 價格資料檔。")

def render_admin(st, forecast):
    authed = _admin_gate(st)
    if not authed:
        return "neutral", False, True, False
    macro = st.sidebar.selectbox("Macro 手動偏壓", ["neutral", "bullish", "bearish"], index=0)
    auto = st.sidebar.checkbox("Auto Analyze", value=False, help="預設關閉，避免開頁就抓外部資料。")
    live = st.sidebar.checkbox("Live Data / News", value=True, help="關閉時使用離線樣本，方便先確認系統可開啟。")
    # Collapsed expanders still execute their body in Streamlit. Heavy
    # diagnostics must be loaded only when explicitly requested.
    if forecast:
        load_diagnostics = st.sidebar.checkbox(
            "載入 Prediction Trace / Truth Guard",
            value=False,
            key="load_admin_diagnostics",
            help="預設關閉；需要除錯時才載入，避免每次重跑建立 DataFrame。",
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
                    _df(st, forecast.trace.to_rows(), "尚無 trace。")
                except Exception as exc:
                    st.warning(f"Trace 暫時無法載入：{type(exc).__name__}: {exc}")
            with st.sidebar.expander("Dashboard Truth Guard", expanded=False):
                try:
                    truths = [x.__dict__ for x in (getattr(forecast, "data_truths", None) or [])]
                    _df(st, truths, "尚無資料真實性紀錄。")
                except Exception as exc:
                    st.warning(f"Truth Guard 暫時無法載入：{type(exc).__name__}: {exc}")
        else:
            st.sidebar.caption("Trace / Truth Guard 已降載。")
    show_learning_admin = st.sidebar.checkbox(
        "開啟 Auto-Learning 管理面板",
        value=False,
        key="show_learning_admin_panel",
        help="預設關閉以降低每次 rerun 的記憶體與 JSONL/DataFrame 負擔；正式預測仍由 app.py 一次寫入。",
    )
    if show_learning_admin:
        _learning_panel(st, forecast)
    else:
        st.sidebar.caption("Auto-Learning 正式快照仍會在每次分析完成後自動寫入。")
    debug = st.sidebar.checkbox("Debug Mode", value=False)
    if debug:
        _mis_debug_panel(st, forecast)
    return macro, auto, live, debug
