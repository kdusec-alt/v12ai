# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import traceback
import time
import gc
import importlib
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
    .block-container{max-width:1920px;padding:.72rem .34rem .24rem!important;}
    [data-testid="stSidebar"]{background:#07101c!important;}
    [data-testid="stSidebar"] *{color:#eaf6ff!important;}
    .input-safe-spacer{height:46px;}
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
        font-weight:1000!important;font-size:16px!important;min-height:52px!important;box-shadow:0 10px 28px rgba(0,0,0,.24)!important;
        pointer-events:auto!important;opacity:1!important;
    }
    .stButton button:hover{border-color:#ffe78a!important;background:#17202b!important;transform:translateY(-1px);}
    .stButton button:active{transform:translateY(0);filter:brightness(1.12);}
    .stButton button:disabled{background:#17202b!important;color:#ffeaa3!important;border:1px solid rgba(255,217,106,.48)!important;opacity:1!important;}
    .v12bar{border:1px solid rgba(54,230,255,.23);border-radius:12px;padding:7px 12px;margin:2px 0 7px;background:#06101b;font-weight:1000;color:#dff5ff;}
    .bootbox{border:1px solid rgba(255,217,106,.35);border-radius:14px;background:#071727;padding:18px 20px;margin-top:12px;color:#eaf6ff;font-weight:850;line-height:1.6;}
    textarea{font-family:'Consolas','Microsoft JhengHei',monospace!important;color:#eaf6ff!important;background:#071727!important;border:1px solid #15506d!important;}
    .tino-nav-spacer{height:2px;}
    .tino-nav-note{color:#bfe6ff;font-size:12px;font-weight:850;margin:-2px 0 6px;}
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


try:
    fetch_news, fetch_price = _load_required("data_sources", "fetch_news", "fetch_price")
    orchestrate = _load_required("orchestrator", "orchestrate")

    render_admin = _load_required("ui_admin", "render_admin")
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
    log_prediction, prediction_signature, build_learning_signals = _load_optional(
        "learning",
        ("log_prediction", "prediction_signature", "build_learning_signals"),
        (_log_prediction_degraded, _prediction_signature_degraded, _build_learning_signals_degraded),
    )
    capture_prediction_seed = _load_optional(
        "v13_research.service", ("capture_prediction_seed",), _capture_prediction_seed_degraded
    )
except Exception as exc:
    trace = _log_exception("project_import_failed", exc)
    _theme()
    st.error("系統核心模組尚未完成同步，正式預測已安全停止。")
    _render_admin_trace(trace)
    st.stop()


def _analysis_once(symbol: str, macro: str, live_data: bool):
    """Run one foreground analysis without Streamlit data-cache duplication.

    The forecast already lives in session_state.  Caching the entire dataclass
    created an additional serialized copy at the exact end of a query, which is
    unnecessary and increases the post-render memory peak on Community Cloud.
    """
    if not live_data:
        os.environ["TINO_OFFLINE_TEST"] = "1"
    else:
        os.environ.pop("TINO_OFFLINE_TEST", None)
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
    # Main quote/render reruns never execute Auto Audit.  Controlled small-batch
    # execution lives only in the Admin Learning Center.
    st.session_state["auto_audit_time_guard"] = {
        "status": "guarded_admin_only",
        "reason": "Main render is read-only; Learning Center runs bounded Auto Audit",
    }
    if "forecast" not in st.session_state:
        st.session_state.forecast = None
    if "last_error" not in st.session_state:
        st.session_state.last_error = ""

    _boot_print("render_admin_start")
    macro, auto, live, debug = render_admin(st, st.session_state.forecast)
    _boot_print("render_admin_done")
    _boot_print("render_nav_start")
    main_view = _render_main_nav()
    _boot_print("render_nav_done", view=main_view)

    if main_view == "watch":
        render_watch_center(st)
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
        st.rerun()

    watch_autorun_symbol = str(st.session_state.pop("watch_autorun_symbol", "") or "").strip().upper()
    suppress_auto_once = bool(st.session_state.pop("suppress_auto_once", False))
    active_symbol = str(st.session_state.get("symbol", "") or "").strip().upper()
    typing_changed = bool(st.session_state.get("typing_changed", False))
    auto_ready = bool(auto and not suppress_auto_once and not typing_changed and st.session_state.forecast is None and symbol and active_symbol == symbol)
    watch_ready = bool(watch_autorun_symbol and symbol and watch_autorun_symbol == symbol)
    should_run = bool((analyze and symbol) or auto_ready or watch_ready)
    if should_run:
        try:
            with st.status("分析中：價格 / 法人 / 資券 / 模型", expanded=False):
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
                    if sig and st.session_state.get("last_logged_prediction_sig") != sig:
                        logged_row = log_prediction(st.session_state.forecast, macro=macro, live_data=live)
                        st.session_state.last_logged_prediction_sig = sig
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
