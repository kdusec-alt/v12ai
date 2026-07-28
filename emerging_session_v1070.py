# -*- coding: utf-8 -*-
"""V1070 Taiwan session routing for emerging-market quotes.

The stable TW price module historically used the listed/TPEx main-board
09:00-13:30 session for every Taiwan symbol. Emerging shares trade until 15:00,
so a 14:xx quote must remain intraday rather than being labelled closed.

This additive patch changes session/freshness semantics only. It does not alter
prices, Direction, T0/T1, High/Low, confidence, Prediction DNA or audit data.
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import time
from functools import wraps
from typing import Callable


_ACTIVE_TW_SYMBOL: ContextVar[str] = ContextVar("tino_active_tw_symbol", default="")
_INSTALLED = False


def _is_emerging_symbol(module, symbol: str) -> bool:
    try:
        return bool(module._is_emerging_symbol(symbol))
    except Exception:
        code = str(symbol or "").upper().split(".")[0]
        return code in set(getattr(module, "EMERGING_PRICE_CODES", set()) or set())


def install_emerging_session_v1070(fetch_tw_price: Callable):
    """Patch data_sources_tw session helpers and return a context-safe fetch wrapper."""
    global _INSTALLED
    import data_sources_tw as tw

    if not _INSTALLED:
        original_phase = tw._tw_session_phase

        def session_phase(now=None) -> str:
            now = now or tw._tw_now()
            symbol = _ACTIVE_TW_SYMBOL.get()
            if not _is_emerging_symbol(tw, symbol):
                return original_phase(now)
            if now.weekday() >= 5:
                return "closed"
            current = now.time()
            if current < time(9, 0):
                return "pre_market"
            if time(9, 0) <= current < time(15, 0):
                return "intraday"
            # Keep a short close-confirm window, mirroring the stable main-board
            # guard but using the official emerging close at 15:00.
            if time(15, 0) <= current <= time(15, 5):
                return "close_confirm"
            return "after_close"

        tw._tw_session_phase = session_phase
        _INSTALLED = True

    @wraps(fetch_tw_price)
    def wrapped(ticker):
        symbol = str(getattr(ticker, "resolved_symbol", "") or "")
        token = _ACTIVE_TW_SYMBOL.set(symbol)
        try:
            return fetch_tw_price(ticker)
        finally:
            _ACTIVE_TW_SYMBOL.reset(token)

    wrapped._v1070_emerging_session = True
    return wrapped
