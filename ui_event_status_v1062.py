# -*- coding: utf-8 -*-
"""High-contrast Event Watch styling for the Streamlit front stage."""
from __future__ import annotations


def inject_event_status_css() -> None:
    try:
        import streamlit as st
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        if get_script_run_ctx() is None:
            return
        st.markdown(
            """
            <style>
            /* Normal five-minute Event Watch status: vivid green, not muted caption gray. */
            [data-testid="stAppViewContainer"] [data-testid="stCaptionContainer"],
            [data-testid="stAppViewContainer"] [data-testid="stCaptionContainer"] p,
            [data-testid="stAppViewContainer"] [data-testid="stCaptionContainer"] span,
            [data-testid="stAppViewContainer"] [data-testid="stCaptionContainer"] div {
                color:#52ff9a !important;
                opacity:1 !important;
                font-weight:900 !important;
                text-shadow:0 0 12px rgba(82,255,154,.38) !important;
            }
            [data-testid="stAppViewContainer"] [data-testid="stCaptionContainer"] {
                background:rgba(7,42,30,.42) !important;
                border-left:3px solid #52ff9a !important;
                border-radius:8px !important;
                padding:6px 10px !important;
                margin:4px 0 !important;
            }
            /* Recent event rows remain white; severity marker supplies yellow/red. */
            [data-testid="stExpander"] [data-testid="stCaptionContainer"],
            [data-testid="stExpander"] [data-testid="stCaptionContainer"] p,
            [data-testid="stExpander"] [data-testid="stCaptionContainer"] span,
            [data-testid="stExpander"] [data-testid="stCaptionContainer"] div {
                color:#f7fbff !important;
                opacity:1 !important;
                font-weight:900 !important;
                text-shadow:none !important;
            }
            [data-testid="stExpander"] [data-testid="stCaptionContainer"] {
                background:rgba(7,23,39,.88) !important;
                border-left:3px solid rgba(255,217,106,.90) !important;
            }
            /* Native warning/error banners keep yellow/red but gain contrast. */
            [data-testid="stAlert"] p,
            [data-testid="stAlert"] span,
            [data-testid="stAlert"] div {
                opacity:1 !important;
                font-weight:900 !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
    except Exception:
        return
