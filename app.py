# -*- coding: utf-8 -*-
from __future__ import annotations

import html
import os
import traceback
import time
import gc
import importlib
from datetime import datetime
from zoneinfo import ZoneInfo
import streamlit as st


def _boot_print(stage: str, **meta):
    """Always-visible startup marker for Streamlit Cloud diagnostics."""
    try:
        payload = " ".join(f"{k}={v}" for k, v in meta.items())
        print(f"[TINO_BOOT] {stage}" + (f" | {payload}" if payload else ""), flush=True)
    except Exception:
        pass


def _diagnostics_allowed() -> bool:
    """Technical traces are restricted to authenticated Admin/debug sessions."""
    try:
        return bool(
            st.session_state.get("admin_authenticated", False)
            or str(os.environ.get("TINO_DEBUG_UI") or "").strip() == "1"
        )
    except Exception:
        return False


def _log_exception(stage: str, exc: Exception) -> str:
    """Write the full trace to server logs and return it for Admin diagnostics."""
    trace = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
    _boot_print(stage, error=f"{type(exc).__name__}: {exc}")
    try:
        print(trace, flush=True)
    except Exception:
        pass
    return trace


def _render_admin_trace(trace: str) -> None:
    if not trace or not _diagnostics_allowed():
        return
    with st.expander("Admin 診斷", expanded=False):
        st.code(trace)


_boot_print("script_enter", python=os.sys.version.split()[0])

# RC24.2 Post-Render Crash Guard
# Streamlit render path must not leave delayed workers or perform layered memory mirrors.
os.environ.setdefault("TINO_FUND_DEEP_CROSSCHECK", "0")
os.environ.setdefault("TINO_INLINE_REMOTE_SYNC", "1")
os.environ.setdefault("TINO_INLINE_MEMORY_MIRROR", "0")
os.environ.setdefault("TINO_V13_RESEARCH", "1")
os.environ.setdefault("TINO_V13_CLOSE_RECHECK", "1")
os.environ.setdefault("TINO_EVENT_REASSESSMENT", "1")
os.environ.setdefault("TINO_EVENT_POLL_INTERVAL", "5m")

st.set_page_config(page_title="系統化分析", layout="wide", initial_sidebar_state="collapsed")
_boot_print("page_config_done", streamlit=getattr(st, "__version__", "unknown"))


def _theme():
    st.markdown("""
    <style>
    :root{--bg:#02070c;--panel:#071727;--cyan:#36e6ff;--gold:#ffd96a;--text:#ecf6ff;}
    html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"], [data-testid="stMainBlockContainer"]{
        background:#02070c !important;
        color:var(--text)!important;
    }
    body::before{content:"";position:fixed;inset:0;background:#02070c;z-index:-999999;}
    [data-testid="stHeader"], header, [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stTopNav"], [data-testid="stBottomBlockContainer"]{
        background:#02070c !important;
        color:#eaf6ff!important;
    }
    [data-testid="stToolbar"]{z-index:1000000!important;}
    /* Keep the first navigation row below Streamlit's fixed toolbar without
       restoring the old oversized empty band. */
    .block-container{max-width:1920px;padding:3.15rem .34rem .24rem!important;}
    [data-testid="stSidebar"]{background:#07101c!important;}
    [data-testid="stSidebar"] *{color:#eaf6ff!important;}
    .input-safe-spacer{height:6px;}
    .stTextInput input{
        background:#071727!important;color:#eaf6ff!important;border:1px solid #1d6f95!important;border-radius:12px!important;
        font-weight:1000!important;font-size:17px!important;min-height:46px!important;box-shadow:0 0 0 1px rgba(54,230,255,.10) inset!important;
    }
    .stTextInput input:focus{border-color:#82e8ff!important;box-shadow:0 0 0 2px rgba(54,230,255,.22)!important;}
    /* RC2.1: Streamlit native inputs/tables must remain readable in dark theme. */
    [data-baseweb="input"] input, [data-baseweb="textarea"] textarea{
        color:#eaf6ff!important;background:#071727!important;
    }
    [data-baseweb="select"] div, [data-baseweb="select"] span{
        color:#111827!important;
    }
    [data-testid="stMetric"]{
        background:transparent!important;border:0!important;box-shadow:none!important;
        padding:4px 0 8px!important;
    }
    [data-testid="stMetricValue"]{font-size:2.05rem!important;line-height:1.08!important;color:#eaf6ff!important;}
    [data-testid="stMetricLabel"]{font-weight:800!important;color:#eaf6ff!important;}
    [data-baseweb="select"]>div{background:#071727!important;border-color:#1d6f95!important;color:#eaf6ff!important;}
    [data-baseweb="select"] span,[data-baseweb="select"] input{color:#eaf6ff!important;}
    [data-baseweb="tag"]{background:#ff4b5c!important;color:#ffffff!important;}
    [data-baseweb="tag"] span{color:#ffffff!important;}
    [data-testid="stDataFrame"], [data-testid="stDataFrame"] *{
        color:inherit;
    }
    .stDataFrame, .stDataFrame *{font-family:'Microsoft JhengHei',Arial,sans-serif!important;}
    /* RC2.2 final: Streamlit tab labels were too dark on dark theme. */
    [data-testid="stTabs"]{
        background:#02070c!important;
        color:#eaf6ff!important;
    }
    [data-testid="stTabs"] [role="tablist"]{
        background:#02070c!important;
        border-bottom:1px solid rgba(54,230,255,.14)!important;
        gap:6px!important;
    }
    [data-testid="stTabs"] button[role="tab"]{
        background:#06101b!important;
        color:#eaf6ff!important;
        border:1px solid rgba(54,230,255,.20)!important;
        border-radius:12px 12px 0 0!important;
        padding:10px 18px!important;
        min-height:44px!important;
        opacity:1!important;
        font-weight:1000!important;
    }
    [data-testid="stTabs"] button[role="tab"] *{
        color:inherit!important;
        opacity:1!important;
        font-weight:1000!important;
    }
    [data-testid="stTabs"] button[role="tab"][aria-selected="true"]{
        background:#0a1c2d!important;
        color:#fff5c4!important;
        border-color:rgba(255,217,106,.55)!important;
        box-shadow:inset 0 -3px 0 #ff4b5c!important;
    }
    [data-testid="stTabs"] button[role="tab"]:hover{
        background:#10263a!important;
        color:#ffffff!important;
        border-color:rgba(54,230,255,.45)!important;
    }
    .stButton{position:relative;z-index:9999!important;}
    .stButton button{
        background:#11151d!important;color:#fff5c4!important;border:1px solid rgba(255,217,106,.45)!important;border-radius:12px!important;
        font-weight:1000!important;font-size:15px!important;min-height:44px!important;box-shadow:0 6px 18px rgba(0,0,0,.20)!important;
        pointer-events:auto!important;opacity:1!important;
    }
    .stButton button:hover{border-color:#ffe78a!important;background:#17202b!important;transform:translateY(-1px);}
    .stButton button:active{transform:translateY(0);filter:brightness(1.12);}
    .stButton button:disabled{background:#17202b!important;color:#ffeaa3!important;border:1px solid rgba(255,217,106,.48)!important;opacity:1!important;}
    .v12bar{border:1px solid rgba(54,230,255,.23);border-radius:12px;padding:7px 12px;margin:2px 0 7px;background:#06101b;font-weight:1000;color:#dff5ff;}
    .bootbox{border:1px solid rgba(255,217,106,.35);border-radius:14px;background:#071727;padding:18px 20px;margin-top:12px;color:#eaf6ff;font-weight:850;line-height:1.6;}
    textarea{font-family:'Consolas','Microsoft JhengHei',monospace!important;color:#eaf6ff!important;background:#071727!important;border:1px solid #15506d!important;}
    .tino-nav-spacer{height:0;}
    [data-testid="stVerticalBlock"]{gap:.62rem!important;}
    .tino-nav-note{color:#bfe6ff;font-size:12px;font-weight:850;margin:-2px 0 6px;}
    .market-command{border:1px solid rgba(54,230,255,.28);border-left:5px solid #36e6ff;border-radius:12px;background:#061827;padding:8px 12px;margin:4px 0 8px;color:#eaf6ff;line-height:1.35}
    .market-command .mc-head{font-weight:1000;color:#fff5c4}.market-command .mc-head span{margin-left:10px;color:#eaf6ff}
    .market-command .mc-facts,.market-command .mc-reason{font-size:12px;color:#bfe6ff;margin-top:2px}
    .market-command .mc-action{font-size:13px;font-weight:900;margin-top:3px}.market-command small{float:right;color:#9bdcff}
    .market-crash{border-left-color:#ff4b5c}.market-sell_off{border-left-color:#ff9f43}.market-caution{border-left-color:#ffd96a}.market-normal{border-left-color:#25d88a}
    </style>
    """, unsafe_allow_html=True)

def _load_required(module_name: str, *attributes: str):
    """Load one required module with an exact server-log breadcrumb."""
    _boot_print("project_import_start", module=module_name)
    try:
        module = importlib.import_module(module_name)
        values = tuple(getattr(module, name) for name in attributes)
    except Exception as exc:
        _boot_print("project_import_failed", module=module_name, error=f"{type(exc).__name__}: {exc}")
        raise ImportError(f"required module unavailable: {module_name}") from exc
    _boot_print("project_import_done", module=module_name)
    return values[0] if len(values) == 1 else values


def _load_optional(module_name: str, attributes: tuple[str, ...], default_value):
    """Load an additive module without taking the stable analysis path offline."""
    _boot_print("optional_import_start", module=module_name)
    try:
        module = importlib.import_module(module_name)
        values = tuple(getattr(module, name) for name in attributes)
        _boot_print("optional_import_done", module=module_name)
        return values[0] if len(values) == 1 else values
    except Exception as exc:
        _log_exception(f"optional_import_failed:{module_name}", exc)
        return default_value


def _learning_center_unavailable(st_module) -> None:
    st_module.warning("預測學習模組暫時停用；個股正式分析仍可正常使用。")


def _research_lab_unavailable(st_module) -> None:
    st_module.warning("AI Research Lab 暫時無法載入；V12 個股分析與預測學習仍可正常使用。")


def _ensure_memory_degraded(*args, **kwargs):
    return {"status": "DEGRADED", "reason": "memory_module_unavailable"}


def _prediction_signature_degraded(*args, **kwargs) -> str:
    return ""


def _log_prediction_degraded(*args, **kwargs):
    return None


def _build_learning_signals_degraded(*args, **kwargs):
    return []


def _capture_prediction_seed_degraded(*args, **kwargs):
    return {"status": "disabled", "reason": "v13_research_module_unavailable"}


def _forecast_snapshot_degraded(*args, **kwargs):
    return {}


def _run_close_recheck_degraded(*args, **kwargs):
    return {"status": "disabled", "reason": "close_recheck_module_unavailable"}


def _assess_event_delta_degraded(*args, **kwargs):
    return {"status": "disabled", "needs_reassessment": False, "reason": "event_reassessment_module_unavailable"}


def _event_watch_display_degraded(*args, **kwargs):
    return {"level": "warning", "text": "事件監測模組暫時無法載入"}


def _global_event_update_degraded(*args, **kwargs):
    return {"dominant": None, "active_count": 0, "recent": []}


def _global_event_ack_degraded(*args, **kwargs):
    return False


def _global_event_display_degraded(*args, **kwargs):
    return {"level": "caption", "text": ""}

def _market_command_degraded(*args, **kwargs):
    return {"market": "", "code": "WAIT_CONFIRM", "label": "⚪ 大盤資料等待確認",
            "score": 0, "confidence": 0, "action": "維持原策略，不以缺失資料推論",
            "facts": [], "event_reason": ""}


def _market_proxy_degraded(*args, **kwargs):
    return {"accepted": False}


try:
    fetch_news, fetch_price = _load_required("data_sources", "fetch_news", "fetch_price")
    orchestrate = _load_required("orchestrator", "orchestrate")

    render_admin, run_admin_auto_audit_cycle = _load_required(
        "ui_admin", "render_admin", "run_admin_auto_audit_cycle"
    )
    render_battle_panel = _load_required("ui_v9_battle_panel", "render_battle_panel")
    render_deep_report = _load_required("ui_v9_deep_report", "render_deep_report")
    render_input = _load_required("ui_v9_input", "render_input")
    render_radar = _load_required("ui_v9_radar", "render_radar")
    render_watch_center = _load_required("ui_watch_center", "render_watch_center")
    mark_runtime_stage = _load_required("runtime_guard", "mark_runtime_stage")

    render_learning_center = _load_optional(
        "ui_learning_center", ("render_learning_center",), _learning_center_unavailable
    )
    render_research_lab = _load_optional(
        "v13_research.ui", ("render_research_lab",), _research_lab_unavailable
    )
    ensure_memory_initialized_bootsafe = _load_optional(
        "tino_persistent_store", ("ensure_memory_initialized_bootsafe",), _ensure_memory_degraded
    )
    log_prediction, prediction_signature, build_learning_signals, forecast_snapshot = _load_optional(
        "learning",
        ("log_prediction", "prediction_signature", "build_learning_signals", "forecast_snapshot"),
        (
            _log_prediction_degraded,
            _prediction_signature_degraded,
            _build_learning_signals_degraded,
            _forecast_snapshot_degraded,
        ),
    )
    capture_prediction_seed = _load_optional(
        "v13_research.service", ("capture_prediction_seed",), _capture_prediction_seed_degraded
    )
    run_login_close_recheck = _load_optional(
        "v13_research.close_recheck", ("run_login_close_recheck",), _run_close_recheck_degraded
    )
    assess_event_delta, event_watch_display = _load_optional(
        "event_reassessment",
        ("assess_event_delta", "event_watch_display"),
        (_assess_event_delta_degraded, _event_watch_display_degraded),
    )
    (
        update_global_event_state,
        get_global_event_view,
        acknowledge_global_event,
        global_event_display,
    ) = _load_optional(
        "event_lifecycle",
        (
            "update_global_event_state",
            "get_global_event_view",
            "acknowledge_global_event",
            "global_event_display",
        ),
        (
            _global_event_update_degraded,
            _global_event_update_degraded,
            _global_event_ack_degraded,
            _global_event_display_degraded,
        ),
    )
    assess_market_command = _load_optional(
        "market_command_v1081", ("assess_market_command",), _market_command_degraded
    )
    conference_watch_display = _load_optional(
        "conference_intelligence_v1097",
        ("conference_watch_display",),
        lambda *args, **kwargs: {"level": "caption", "text": ""},
    )
    fetch_market_proxy_context = _load_optional(
        "quantum_market_context", ("fetch_market_proxy_context",), _market_proxy_degraded
    )
except Exception as exc:
    trace = _log_exception("project_import_failed", exc)
    _theme()
    st.error("系統核心模組尚未完成同步，正式預測已安全停止。")
    _render_admin_trace(trace)
    st.stop()


try:
    from analysis_speed_v1081 import (
        run_analysis_pipeline as _run_analysis_pipeline_v1081,
        cached_market_proxy_context as _cached_market_proxy_context_v1081,
        seed_news_cache as _seed_news_cache_v1081,
        install_analysis_speed_guards as _install_analysis_speed_guards_v1081,
    )
    from news_reassessment_priority_v1081 import (
        prioritize_reassessment_plan as _prioritize_reassessment_plan_v1081,
    )
    from session_truth_v1081 import attach_session_truth as _attach_session_truth_v1081
    from learning_integrity_v1081 import (
        compact_learning_health as _compact_learning_health_v1081,
        install_persistence_health_guard as _install_persistence_health_guard_v1081,
    )
    _install_analysis_speed_guards_v1081()
    _install_persistence_health_guard_v1081()
except Exception as _v1081_integration_exc:
    _log_exception("v1081_integration_degraded", _v1081_integration_exc)
    _run_analysis_pipeline_v1081 = None
    _cached_market_proxy_context_v1081 = None
    _seed_news_cache_v1081 = lambda symbol, rows: {}
    _prioritize_reassessment_plan_v1081 = lambda plan: dict(plan or {})
    _attach_session_truth_v1081 = lambda forecast, proxies: {}
    _compact_learning_health_v1081 = lambda limit=600: {}


def _analysis_once(symbol: str, macro: str, live_data: bool):
    """Run one foreground analysis without a second serialized forecast copy."""
    if not live_data:
        os.environ["TINO_OFFLINE_TEST"] = "1"
    else:
        os.environ.pop("TINO_OFFLINE_TEST", None)
    if callable(_run_analysis_pipeline_v1081):
        return _run_analysis_pipeline_v1081(
            symbol,
            macro,
            live_data,
            fetch_price=fetch_price,
            fetch_news=fetch_news,
            build_learning_signals=build_learning_signals,
            orchestrate=orchestrate,
            mark_runtime_stage=mark_runtime_stage,
        )
    # Fail-safe: preserve the exact pre-V1081 stable path.
    mark_runtime_stage("analysis_fetch_price_start", symbol=symbol)
    price = fetch_price(symbol)
    mark_runtime_stage("analysis_fetch_price_done", symbol=symbol)
    news = fetch_news(symbol)
    mark_runtime_stage("analysis_fetch_news_done", symbol=symbol)
    extra_signals = build_learning_signals(symbol)
    forecast = orchestrate(price, macro, news_items=news, extra_signals=extra_signals)
    mark_runtime_stage("analysis_orchestrate_done", symbol=symbol)
    return forecast


def run_analysis(symbol: str, macro: str, live_data: bool):
    # Manual analysis is already guarded by the Analyze button/session state.
    # Do not retain a second forecast copy in st.cache_data.
    return _analysis_once(symbol.strip(), macro, live_data)


def _event_reassessment_enabled() -> bool:
    return str(os.environ.get("TINO_EVENT_REASSESSMENT", "1") or "1").strip().lower() not in {
        "0", "false", "off", "disabled", "no",
    }


_EVENT_WATCH_SESSION_KEYS = (
    "event_reassessment_queue",
    "event_news_baseline",
    "event_baseline_created_at",
    "event_reassessment_notice",
    "event_reassessment_notice_severity",
    "last_event_watch_report",
    "global_event_view",
    "global_event_lifecycle_error",
)


def _clear_event_watch_state() -> None:
    for key in _EVENT_WATCH_SESSION_KEYS:
        st.session_state.pop(key, None)


def _event_checked_at_tw() -> str:
    try:
        return datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _event_watch_body() -> None:
    """Poll only the active forecast and request a full, traceable rebuild.

    The fragment never mutates the current forecast or writes Memory.  It only
    compares headline identities, stores a bounded plan in session_state and
    asks the normal V12 analysis path to run again.
    """
    # Defence in depth: the poller is an Admin operational feature.  Public or
    # logged-out sessions must never spend news/API capacity or queue a rebuild.
    if not bool(st.session_state.get("admin_authenticated", False)):
        return
    if not _event_reassessment_enabled():
        return
    if str(st.session_state.get("main_view") or "analysis") != "analysis":
        return
    forecast = st.session_state.get("forecast")
    if forecast is None or bool(getattr(forecast, "stopped", False)):
        return
    if st.session_state.get("event_reassessment_queue"):
        return
    symbol = str(getattr(getattr(forecast, "ticker", None), "resolved_symbol", "") or "").strip().upper()
    market = str(getattr(getattr(forecast, "ticker", None), "market", "") or "").strip().upper()
    if not symbol:
        return
    previous_news = st.session_state.get("event_news_baseline")
    if not isinstance(previous_news, list):
        st.session_state["event_news_baseline"] = list(getattr(forecast, "news_items", []) or [])
        st.session_state["event_baseline_created_at"] = time.time()
        st.session_state["last_event_watch_report"] = {
            "status": "baseline",
            "ticker": symbol,
            "checked_at_tw": "",
        }
        return
    try:
        latest_news = fetch_news(symbol, force_refresh=True)
        _seed_news_cache_v1081(symbol, latest_news)
        plan = assess_event_delta(
            previous_news,
            latest_news,
            ticker=symbol,
            market=market,
            revision_of=str(
                st.session_state.get("last_logged_prediction_id")
                or prediction_signature(forecast)
                or ""
            ),
            not_before_epoch=float(st.session_state.get("event_baseline_created_at") or time.time()),
        )
        plan = _prioritize_reassessment_plan_v1081(plan)
        st.session_state["event_news_baseline"] = list(latest_news or [])
        report = dict(plan or {})
        report["checked_at_tw"] = _event_checked_at_tw()
        st.session_state["last_event_watch_report"] = report
        # Yellow/red operational state is compact and cross-ticker.  It is not
        # Prediction Memory and cannot influence V12/V13 decisions directly.
        try:
            st.session_state["global_event_view"] = update_global_event_state(
                plan,
                ticker=symbol,
                market=market,
            )
        except Exception as lifecycle_exc:
            st.session_state["global_event_lifecycle_error"] = (
                f"{type(lifecycle_exc).__name__}: {lifecycle_exc}"
            )
        if bool((plan or {}).get("needs_reassessment")):
            st.session_state["event_reassessment_queue"] = dict(plan)
            st.session_state["event_reassessment_notice"] = (
                f"重大事件重新評估｜{(plan or {}).get('event_title') or (plan or {}).get('reassessment_reason')}"
            )
            st.session_state["event_reassessment_notice_severity"] = int(
                (plan or {}).get("event_severity") or 0
            )
            st.rerun()
    except Exception as exc:
        # News polling is optional.  A network/parser failure must never clear
        # or mutate the last valid V12 forecast.
        st.session_state["last_event_watch_report"] = {
            "status": "degraded",
            "needs_reassessment": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "ticker": symbol,
            "checked_at_tw": _event_checked_at_tw(),
        }


def _render_event_watch_status(forecast) -> None:
    """Show Admin-only liveness and a severity-coloured reassessment alert."""
    if not bool(st.session_state.get("admin_authenticated", False)):
        return
    if not _event_reassessment_enabled():
        st.caption("⚪ 自動事件監測已停用｜按「個股分析」仍會更新當下新聞與事件風險")
        return
    symbol = str(getattr(getattr(forecast, "ticker", None), "resolved_symbol", "") or "").strip().upper()
    # V1097: industry conferences are purple forward context.  They are not
    # red/yellow risk alerts and must not cast a directional vote pre-event.
    industry_payload = dict(conference_watch_display() or {})
    industry_text = str(industry_payload.get("text") or "").strip()
    if industry_text:
        st.info(industry_text)
        industry_sessions = list(industry_payload.get("sessions") or [])[:8]
        if industry_sessions:
            with st.expander("AI Industry Calendar｜產業會議議程", expanded=False):
                for row in industry_sessions:
                    tickers = [*(row.get("direct_tickers") or ()), *(row.get("supply_chain_tickers") or ())]
                    st.caption(
                        f"{row.get('start_taipei')}｜{row.get('company')}｜{row.get('title')}｜"
                        f"{row.get('stars')}｜曝險：{', '.join(tickers) or '待映射'}｜{row.get('lifecycle')}"
                    )
        source_health = dict(industry_payload.get("source_health") or {})
        if source_health:
            with st.expander("Admin｜產業日曆來源狀態", expanded=False):
                st.caption(
                    f"整體：{source_health.get('status', 'UNKNOWN')}｜"
                    f"有效議程：{source_health.get('session_count', 0)}｜"
                    f"最後更新：{source_health.get('saved_at') or '尚無成功快取'}"
                )
                for source, detail in sorted(dict(source_health.get("sources") or {}).items()):
                    detail = dict(detail or {})
                    st.caption(f"{source}｜{detail.get('status', 'UNKNOWN')}｜{detail.get('sessions', 0)} 場")
    try:
        global_view = get_global_event_view()
        st.session_state["global_event_view"] = global_view
    except Exception as lifecycle_exc:
        global_view = dict(st.session_state.get("global_event_view") or {})
        st.session_state["global_event_lifecycle_error"] = (
            f"{type(lifecycle_exc).__name__}: {lifecycle_exc}"
        )
    dominant = dict((global_view or {}).get("dominant") or {})
    if dominant:
        global_payload = global_event_display(dominant)
        global_level = str((global_payload or {}).get("level") or "warning")
        global_message = str((global_payload or {}).get("text") or "").strip()
        banner_slot = st.empty()
        with banner_slot.container():
            if global_message:
                if global_level == "error":
                    st.error(global_message)
                else:
                    st.warning(global_message)
            event_id = str(dominant.get("event_id") or "")
            if event_id and st.button(
                "Admin 已讀並關閉此警示",
                key=f"ack_global_event_{event_id}",
                type="secondary",
            ):
                if acknowledge_global_event(event_id):
                    st.session_state.pop("event_reassessment_notice", None)
                    st.session_state.pop("event_reassessment_notice_severity", None)
                    try:
                        global_view = get_global_event_view()
                        st.session_state["global_event_view"] = global_view
                    except Exception:
                        global_view = {"dominant": None, "recent": []}
                    dominant = {}
                    banner_slot.empty()
                    st.toast("警訊已讀並關閉；稽核紀錄仍保留。", icon="✅")

    notice = str(st.session_state.get("event_reassessment_notice") or "")
    report = dict(st.session_state.get("last_event_watch_report") or {})
    # The compact global lifecycle owns the persistent banner.  Session notice
    # remains a compatibility fallback when that module is unavailable.
    local_notice = "" if dominant else notice
    if local_notice:
        report["event_severity"] = int(
            st.session_state.get("event_reassessment_notice_severity")
            or report.get("event_severity")
            or 0
        )
    payload = event_watch_display(
        report,
        notice=local_notice,
        ticker=symbol,
        interval_label=str(os.environ.get("TINO_EVENT_POLL_INTERVAL", "5m") or "5m"),
    )
    level = str((payload or {}).get("level") or "caption")
    message = str((payload or {}).get("text") or "").strip()
    if not message:
        return
    if level == "error":
        st.error(message)
    elif level == "warning":
        st.warning(message)
    elif level == "info":
        st.info(message)
    else:
        st.caption(message)

    recent = list((global_view or {}).get("recent") or [])[:5]
    if recent:
        with st.expander("近期黃／紅事件（最多 5 筆）", expanded=False):
            for row in recent:
                severity = int(row.get("severity") or 0)
                status = str(row.get("status") or "")
                marker = "🔴" if severity >= 3 else "🟡" if status != "resolved" else "⚪"
                scope = "全市場" if str(row.get("scope") or "") == "global" else "/".join(
                    str(value) for value in (row.get("source_tickers") or [])[-2:]
                )
                st.caption(
                    f"{marker} {scope or '個股'}｜Severity {severity}｜"
                    f"{str(row.get('title') or row.get('transition_reason') or '')}"
                )


def _event_watch_fragment_body() -> None:
    """Poll and render inside the same fragment so liveness is never stale."""
    _event_watch_body()
    forecast = st.session_state.get("forecast")
    if forecast is not None and not bool(getattr(forecast, "stopped", False)):
        _render_event_watch_status(forecast)


def _market_command_fragment_body() -> None:
    """Render ticker-independent TW/US market judgement on every analysis."""
    forecast = st.session_state.get("forecast")
    if forecast is not None and not bool(getattr(forecast, "stopped", False)):
        _render_market_command(forecast)


def _render_market_command(forecast) -> None:
    """Compact market judgement refreshed with the five-minute watcher."""
    ticker = getattr(forecast, "ticker", None)
    market = str(getattr(ticker, "market", "") or "").upper()
    try:
        if callable(_cached_market_proxy_context_v1081):
            proxies = _cached_market_proxy_context_v1081(
                fetch_market_proxy_context,
                str(getattr(forecast, "price_date", "") or ""),
                market=market,
            )
        else:
            proxies = fetch_market_proxy_context(str(getattr(forecast, "price_date", "") or ""))
        proxies = dict(proxies or {})
        session_truth = _attach_session_truth_v1081(forecast, proxies)
        proxies["_session_truth_v1081"] = dict(session_truth or {})
        result = assess_market_command(
            market, proxies, list(getattr(forecast, "news_items", []) or []),
            dict(getattr(forecast, "radar", {}) or {}),
        )
        try:
            card = dict(getattr(forecast, "decision_card", {}) or {})
            card["_market_command_v1081"] = dict(result or {})
            forecast.decision_card = card
        except Exception:
            pass
    except Exception:
        result = _market_command_degraded()
    market_label = "台股" if market == "TW" else "美股"
    facts = "｜".join(str(x) for x in (result.get("facts") or [])) or "市場資料同步中"
    event = str(result.get("event_reason") or "").strip()
    event_line = f"<div class='mc-reason'>事件：{html.escape(event)}</div>" if event else ""
    thesis = str(result.get("thesis") or "").strip()
    thesis_line = f"<div class='mc-reason'>判讀：{html.escape(thesis)}</div>" if thesis else ""
    coverage = int(result.get("coverage") or result.get("confidence") or 0)
    st.markdown(
        f"""<div class="market-command market-{html.escape(str(result.get('code') or '').lower())}">
        <div class="mc-head">🌐 大盤風險判讀｜{market_label}<span>{html.escape(str(result.get('label') or '等待確認'))}</span></div>
        <div class="mc-facts">{html.escape(facts)}</div>{event_line}{thesis_line}
        <div class="mc-action">現在建議：{html.escape(str(result.get('action') or '等待確認'))}
        <small>市場資料覆蓋度 {coverage}%｜非方向命中率</small></div></div>""",
        unsafe_allow_html=True,
    )


if hasattr(st, "fragment"):
    _event_watch_fragment = st.fragment(
        run_every=str(os.environ.get("TINO_EVENT_POLL_INTERVAL", "5m") or "5m")
    )(_event_watch_fragment_body)
    _market_command_fragment = st.fragment(
        run_every=str(os.environ.get("TINO_EVENT_POLL_INTERVAL", "5m") or "5m")
    )(_market_command_fragment_body)
else:
    def _event_watch_fragment() -> None:
        # Streamlit < fragment support: no timer, but keep Admin status honest.
        forecast = st.session_state.get("forecast")
        if forecast is not None and not bool(getattr(forecast, "stopped", False)):
            _render_event_watch_status(forecast)

    def _market_command_fragment() -> None:
        # Streamlit < fragment support: still render once per full app run.
        _market_command_fragment_body()


def _is_fragment_rerun() -> bool:
    """Return True only for a real partial fragment rerun.

    A decorated fragment is also called once during every full-app render.
    Heavy maintenance must never run in that initial call because Streamlit
    removes not-yet-rendered navigation and disables the page until it returns.
    """
    try:
        try:
            from streamlit.runtime.scriptrunner_utils.script_run_context import get_script_run_ctx
        except ImportError:
            from streamlit.runtime.scriptrunner.script_run_context import get_script_run_ctx
        try:
            ctx = get_script_run_ctx(suppress_warning=True)
        except TypeError:
            ctx = get_script_run_ctx()
        # Streamlit 1.40.x records standalone partial runs here.  Keep the
        # older attribute fallback for compatibility with earlier fragment
        # internals, but never infer a fragment rerun merely because this
        # function is wrapped by @st.fragment during a full app run.
        fragment_ids = getattr(ctx, "fragment_ids_this_run", None)
        if fragment_ids:
            return True
        return bool(getattr(ctx, "current_fragment_id", None))
    except Exception:
        return False


def _admin_maintenance_fragment_body() -> None:
    """Run one timed learning/close batch without blocking the full UI."""
    if not bool(st.session_state.get("admin_authenticated", False)):
        return
    if not _is_fragment_rerun():
        # Full render: arm the timer but perform zero disk/network work.
        return

    # The close-time guards inside Auto Audit and Close Recheck remain the
    # authority for when work is due.  Do not postpone a due daily calibration
    # merely because the Admin is viewing a forecast, Learning Center or
    # Research Lab; this fragment is already isolated from the full-page render.

    phase = int(st.session_state.get("tino_background_maintenance_phase") or 0)
    st.session_state["tino_background_maintenance_phase"] = phase + 1
    try:
        if phase % 2 == 0:
            report = run_admin_auto_audit_cycle(st, max_tickers_per_market=2)
            try:
                _health = _compact_learning_health_v1081(600)
                if isinstance(_health, dict):
                    st.session_state["learning_integrity_v1081"] = {
                        "level": str(_health.get("level") or ""),
                        "text": str(_health.get("text") or ""),
                    }
            except Exception:
                pass
            st.session_state["last_background_maintenance"] = {
                "task": "auto_audit",
                "status": "done",
                "audited": int((report or {}).get("audited") or 0),
                "remaining": int((report or {}).get("remaining") or 0),
                "errors": int((report or {}).get("errors") or 0),
            }
        else:
            close_report = run_login_close_recheck(
                st,
                analyzer=run_analysis,
                snapshot_builder=forecast_snapshot,
                log_writer=log_prediction,
                research_capture=capture_prediction_seed,
                macro=str(st.session_state.get("tino_admin_macro") or "neutral"),
                live_data=bool(st.session_state.get("tino_admin_live_data", True)),
                request_rerun=False,
                batch_size_override=1,
                time_budget_override=15.0,
            )
            st.session_state["last_close_recheck_report"] = close_report
            st.session_state["last_background_maintenance"] = {
                "task": "close_recheck",
                "status": str((close_report or {}).get("status") or "unknown"),
                "remaining": int((close_report or {}).get("remaining") or 0),
                "errors": int((close_report or {}).get("errors") or 0),
            }
    except Exception as exc:
        st.session_state["last_background_maintenance"] = {
            "task": "auto_audit" if phase % 2 == 0 else "close_recheck",
            "status": "degraded",
            "reason": f"{type(exc).__name__}: {exc}",
        }
        _log_exception("background_maintenance_failed_safe", exc)


if hasattr(st, "fragment"):
    _admin_maintenance_fragment = st.fragment(
        run_every=str(os.environ.get("TINO_ADMIN_MAINTENANCE_INTERVAL", "2m") or "2m")
    )(_admin_maintenance_fragment_body)
else:
    def _admin_maintenance_fragment() -> None:
        # Older Streamlit builds have no safe partial-rerun isolation. Keep the
        # database intact and defer work instead of blocking the whole page.
        return


def _render_forecast(forecast):
    """Render forecast with crash-forensics checkpoints.

    RC25.1 keeps the V9 layout unchanged while recording the exact render
    boundary.  These checkpoints are intentionally lightweight and do not
    start workers or external I/O.
    """
    symbol = getattr(getattr(forecast, "ticker", None), "resolved_symbol", "")
    left, right = st.columns([1.03, 0.97], gap="small")
    mark_runtime_stage("render_battle_start", symbol=symbol)
    with left:
        render_battle_panel(st, forecast)
    mark_runtime_stage("render_battle_done", symbol=symbol)

    mark_runtime_stage("render_radar_start", symbol=symbol)
    with right:
        render_radar(st, forecast)
    mark_runtime_stage("render_radar_done", symbol=symbol)

    mark_runtime_stage("render_deep_start", symbol=symbol)
    render_deep_report(st, forecast)
    mark_runtime_stage("render_deep_done", symbol=symbol)


def _set_main_view(view: str) -> None:
    """Switch pages and release the heavy forecast before table-heavy views."""
    target = str(view or "analysis")
    st.session_state["main_view"] = target
    if target in {"watch", "learning", "research"}:
        st.session_state["forecast"] = None
        st.session_state["last_error"] = ""
        gc.collect()

def _render_main_nav():
    """Stable visible navigation without nested rerun loops."""
    is_admin = bool(st.session_state.get("admin_authenticated", False))
    if "main_view" not in st.session_state:
        st.session_state["main_view"] = "analysis"
    if st.session_state.get("main_view") in {"learning", "research"} and not is_admin:
        st.session_state["main_view"] = "analysis"

    st.markdown("<div class='tino-nav-spacer'></div>", unsafe_allow_html=True)
    if is_admin:
        n1, n2, n3, n4, n5 = st.columns([0.16, 0.16, 0.16, 0.18, 0.34], gap="small")
    else:
        n1, n2, n5 = st.columns([0.18, 0.18, 0.64], gap="small")
        n3 = n4 = None

    with n1:
        st.button("🎯 個股分析", use_container_width=True, key="nav_analysis",
                  on_click=_set_main_view, args=("analysis",))
    with n2:
        st.button("📊 即時股價", use_container_width=True, key="nav_watch",
                  on_click=_set_main_view, args=("watch",))
    if is_admin and n3 is not None and n4 is not None:
        with n3:
            st.button("🧠 預測學習", use_container_width=True, key="nav_learning",
                      on_click=_set_main_view, args=("learning",))
        with n4:
            st.button("🔬 AI Research Lab", use_container_width=True, key="nav_research",
                      on_click=_set_main_view, args=("research",))
    return st.session_state.get("main_view", "analysis")


def main():
    _boot_print("main_enter")
    mark_runtime_stage("main_enter")
    _theme()
    # RC4.8 Memory Persistence Guard: restore GitHub memory once per process,
    # then keep all Streamlit reruns local.  Remote failures remain diagnostic-only.
    if "memory_init_report" not in st.session_state:
        try:
            st.session_state["memory_init_report"] = ensure_memory_initialized_bootsafe(migrate=False)
        except Exception as _mem_exc:
            st.session_state["memory_init_report"] = {"status": "FAIL", "error": f"{type(_mem_exc).__name__}: {_mem_exc}"}
    # RC4.2 Stability Contract:
    # Main quote/render reruns never execute Auto Audit. Controlled one-ticker
    # cycles live only in the Admin maintenance fragment.
    st.session_state["auto_audit_time_guard"] = {
        "status": "fragment_admin_only",
        "reason": "Main render is read-only; idle Admin fragment runs bounded Auto Audit",
    }
    if "forecast" not in st.session_state:
        st.session_state.forecast = None
    if "last_error" not in st.session_state:
        st.session_state.last_error = ""

    _boot_print("render_admin_start")
    macro, auto, live, debug = render_admin(st, st.session_state.forecast)
    _boot_print("render_admin_done")
    # Store only scalar controls for the idle maintenance fragment.  Auto Audit
    # and Close Recheck must never execute before navigation/input render.
    st.session_state["tino_admin_macro"] = macro
    st.session_state["tino_admin_live_data"] = bool(live)

    _boot_print("render_nav_start")
    main_view = _render_main_nav()
    _boot_print("render_nav_done", view=main_view)

    if main_view == "watch":
        render_watch_center(st)
        _admin_maintenance_fragment()
        return
    if main_view == "learning":
        # RC4.7 Learning Core isolation: a malformed historical memory row or
        # Admin-only widget must never take the main analysis application down.
        try:
            render_learning_center(st)
        except Exception as _learning_exc:
            _learning_trace = _log_exception("learning_center_failed_safe", _learning_exc)
            st.error("預測學習暫時無法載入；個股分析與即時股價仍可正常使用。")
            if bool(st.session_state.get("admin_authenticated", False)):
                with st.expander("Admin 診斷", expanded=False):
                    st.code(_learning_trace)
        _admin_maintenance_fragment()
        return
    if main_view == "research":
        # V13 Research isolation: bounded local reads only.  Any research UI
        # failure is contained and can never block the V12 analysis kernel.
        try:
            render_research_lab(st)
        except Exception as _research_ui_exc:
            _research_ui_trace = _log_exception("research_lab_failed_safe", _research_ui_exc)
            st.error("AI Research Lab 暫時無法載入；V12 正式分析仍可正常使用。")
            if bool(st.session_state.get("admin_authenticated", False)):
                with st.expander("Admin 診斷", expanded=False):
                    st.code(_research_ui_trace)
        _admin_maintenance_fragment()
        return

    _boot_print("render_input_start")
    symbol, analyze, clear = render_input(st)
    _boot_print("render_input_done")

    if clear:
        try:
            st.cache_data.clear()
        except Exception:
            pass
        st.session_state.forecast = None
        st.session_state.last_error = ""
        st.session_state.symbol = ""
        st.session_state.suppress_auto_once = True
        st.session_state.input_was_cleared = True
        _clear_event_watch_state()
        st.rerun()

    # Logged-out sessions must discard any stale event state before analysis.
    if not bool(st.session_state.get("admin_authenticated", False)):
        _clear_event_watch_state()

    watch_autorun_symbol = str(st.session_state.pop("watch_autorun_symbol", "") or "").strip().upper()
    suppress_auto_once = bool(st.session_state.pop("suppress_auto_once", False))
    active_symbol = str(st.session_state.get("symbol", "") or "").strip().upper()
    typing_changed = bool(st.session_state.get("typing_changed", False))
    auto_ready = bool(auto and not suppress_auto_once and not typing_changed and st.session_state.forecast is None and symbol and active_symbol == symbol)
    watch_ready = bool(watch_autorun_symbol and symbol and watch_autorun_symbol == symbol)
    event_revision_meta = dict(st.session_state.get("event_reassessment_queue") or {})
    event_ready = bool(
        event_revision_meta
        and symbol
        and str(event_revision_meta.get("ticker") or "").upper() == str(symbol).upper()
    )
    if analyze and not event_ready:
        st.session_state.pop("event_reassessment_notice", None)
        st.session_state.pop("event_reassessment_notice_severity", None)
    should_run = bool((analyze and symbol) or auto_ready or watch_ready or event_ready)
    if should_run:
        try:
            with st.status("分析中：價格 / 當下新聞 / 法人 / 資券 / 模型", expanded=False):
                if not symbol:
                    st.session_state.forecast = None
                    st.session_state.last_error = ""
                    st.stop()
                # Release previous forecast before building the next one.
                # Otherwise old and new full object graphs overlap in memory.
                previous_forecast = st.session_state.get("forecast")
                st.session_state.forecast = None
                if previous_forecast is not None:
                    del previous_forecast
                gc.collect()
                mark_runtime_stage("previous_forecast_released", symbol=symbol)

                st.session_state.symbol = symbol
                st.session_state.input_was_cleared = False
                st.session_state.forecast = run_analysis(symbol, macro, live)
                mark_runtime_stage("forecast_session_state_set", symbol=symbol)
                # RC3.3: invalid/stopped price forecasts must not enter Learning memory.
                # The sidebar checkbox controls whether a formal snapshot is written.
                if (
                    bool(st.session_state.get("learning_log_enabled", True))
                    and st.session_state.forecast
                    and not bool(getattr(st.session_state.forecast, "stopped", False))
                ):
                    sig = prediction_signature(st.session_state.forecast)
                    if sig and (st.session_state.get("last_logged_prediction_sig") != sig or event_ready):
                        logged_row = log_prediction(
                            st.session_state.forecast,
                            macro=macro,
                            live_data=live,
                            revision_meta=event_revision_meta if event_ready else None,
                        )
                        st.session_state.last_logged_prediction_sig = sig
                        if isinstance(logged_row, dict) and not bool(logged_row.get("skipped")):
                            st.session_state["last_logged_prediction_id"] = str(logged_row.get("id") or "")
                        mark_runtime_stage("prediction_log_done", symbol=symbol)
                        # V13 Phase 0 sidecar: consume only the already-persisted
                        # formal V12 row.  This hook is disabled by default and
                        # may never interrupt the analysis/render path.
                        try:
                            research_report = capture_prediction_seed(logged_row)
                            st.session_state["last_v13_research_report"] = research_report
                            mark_runtime_stage(
                                "v13_research_seed_done",
                                symbol=symbol,
                                status=str((research_report or {}).get("status") or "unknown"),
                            )
                        except Exception as _research_exc:
                            st.session_state["last_v13_research_report"] = {
                                "status": "degraded",
                                "reason": f"{type(_research_exc).__name__}: {_research_exc}",
                            }
                st.session_state["event_news_baseline"] = list(
                    getattr(st.session_state.forecast, "news_items", []) or []
                )
                st.session_state["event_baseline_created_at"] = time.time()
                if event_ready:
                    st.session_state.pop("event_reassessment_queue", None)
                else:
                    st.session_state["last_event_watch_report"] = {
                        "status": "baseline",
                        "ticker": str(symbol).upper(),
                        "checked_at_tw": "",
                    }
                st.session_state.last_error = ""
        except Exception as exc:
            st.session_state.forecast = None
            st.session_state.last_error = _log_exception("analysis_failed", exc)

    if st.session_state.last_error:
        st.error("分析流程暫時中止，已安全保留畫面。請重新整理後再試。")
        if debug or _diagnostics_allowed():
            _render_admin_trace(st.session_state.last_error)

    forecast = st.session_state.forecast
    if forecast:
        # Market command is a core TW/US analysis feature and must not depend
        # on Admin-only news polling.  Each fragment refreshes independently.
        if bool(st.session_state.get("admin_authenticated", False)):
            _event_watch_fragment()
        _market_command_fragment()
        mark_runtime_stage("render_forecast_start", symbol=getattr(getattr(forecast, "ticker", None), "resolved_symbol", ""))
        _render_forecast(forecast)
        mark_runtime_stage("render_forecast_done", symbol=getattr(getattr(forecast, "ticker", None), "resolved_symbol", ""))
        gc.collect()
        mark_runtime_stage("render_gc_done", symbol=getattr(getattr(forecast, "ticker", None), "resolved_symbol", ""))
    else:
        st.markdown("""
        <div class="bootbox">
        系統已啟動。請輸入股票 / ETF 後按「🚀 個股分析」。<br>
        Watch Center 可放自選股，只跑輕量股價快照；點卡片「分析」會切回本頁並啟動完整 TINO。
        </div>
        """, unsafe_allow_html=True)

    if debug:
        st.caption("Debug：主畫面不顯示工程字串；錯誤只在此區或 Admin Console 顯示。")
    _admin_maintenance_fragment()

# Streamlit executes this file as a script.  Call main unconditionally so a
# runner-specific __name__ value can never leave the page blank.
try:
    main()
except Exception as exc:
    _trace = _log_exception("main_failed", exc)
    try:
        _theme()
        st.error("TINO 啟動流程暫時中止，已安全攔截白屏。")
        _render_admin_trace(_trace)
    except Exception:
        raise
