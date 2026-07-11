# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


def maybe_run_auto_audit_time_guard(now: Optional[datetime] = None, *, markets: Optional[List[str]] = None, execute: Optional[bool] = None) -> Dict[str, Any]:
    """RC24.1 Stability Hotfix.

    Main Streamlit app must never execute or even prepare heavy Auto Audit work
    during boot/rerun.  This no-op keeps imports compatible while removing the
    crash vector.  Full Auto Audit should be reintroduced later through a
    controlled Admin/worker entry, not the quote page render cycle.
    """
    return {
        "status": "disabled_bootsafe",
        "mode": "no_op",
        "execute": False,
        "reason": "Auto Audit disabled from Streamlit boot/rerun for stability",
        "markets": {},
    }


def execute_due_auto_audit_once(now: Optional[datetime] = None, *, markets: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "status": "disabled_bootsafe",
        "mode": "no_op",
        "execute": False,
        "reason": "Controlled execution temporarily disabled until RC24.1 stability observation passes",
        "markets": {},
    }


def auto_audit_status_rows() -> List[Dict[str, Any]]:
    return [{
        "market": "ALL",
        "trade_date": "",
        "status": "disabled_bootsafe",
        "attempt_at_tw": "",
        "pending_t1": None,
        "pending_today": None,
        "audited_t1": None,
        "audited_today": None,
        "reason": "Auto Audit temporarily disabled from main app to prevent rerun crash",
    }]
