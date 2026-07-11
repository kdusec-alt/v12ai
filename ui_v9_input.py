# -*- coding: utf-8 -*-
from __future__ import annotations


def _clear_input_state(st):
    """Clear must only clear state; it must not become a new search trigger."""
    st.session_state["ticker_text_input"] = ""
    st.session_state["symbol"] = ""
    st.session_state["typed_symbol"] = ""
    st.session_state["forecast"] = None
    st.session_state["last_error"] = ""
    st.session_state["suppress_auto_once"] = True
    st.session_state["input_was_cleared"] = True
    st.session_state["typing_changed"] = False


def _on_input_change(st):
    """Typing a new ticker must not resurrect the previous forecast.

    The text box owns `ticker_text_input`; active analysis ticker stays in `symbol`
    only after the Analyze button is clicked. This prevents the UI from flashing
    the previous ticker and forcing the user to type twice.
    """
    typed = str(st.session_state.get("ticker_text_input", "") or "").strip().upper()
    active = str(st.session_state.get("symbol", "") or "").strip().upper()
    st.session_state["typed_symbol"] = typed
    if typed != active:
        st.session_state["forecast"] = None
        st.session_state["last_error"] = ""
        st.session_state["typing_changed"] = bool(typed)
        st.session_state["suppress_auto_once"] = True


def render_input(st):
    """V9-style stable input row.

    First load is empty. The text input never receives `value=session_state.symbol`,
    because that caused Streamlit to rebuild the widget with the previous ticker.
    """
    st.markdown("<div class='input-safe-spacer'></div>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([0.62, 0.19, 0.19], gap="medium")

    if "ticker_text_input" not in st.session_state:
        st.session_state["ticker_text_input"] = ""
    if "symbol" not in st.session_state:
        st.session_state["symbol"] = ""

    with c1:
        symbol = st.text_input(
            "股票 / ETF 輸入框",
            label_visibility="collapsed",
            placeholder="輸入 6770 / 6586 / 2454 / 00919 / MRVL / MU / ONDS",
            key="ticker_text_input",
            on_change=_on_input_change,
            args=(st,),
        ).strip().upper()
    with c2:
        analyze = st.button("🚀 個股分析", use_container_width=True, key="analyze_button")
    with c3:
        clear = st.button(
            "🧹 清除",
            use_container_width=True,
            key="clear_button",
            on_click=_clear_input_state,
            args=(st,),
        )

    if analyze and symbol:
        st.session_state.symbol = symbol
        st.session_state.typed_symbol = symbol
        st.session_state.input_was_cleared = False
        st.session_state.typing_changed = False

    return symbol, analyze, clear
