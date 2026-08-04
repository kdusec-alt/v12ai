# -*- coding: utf-8 -*-
"""Explicit Public/Admin memory boundary for V1093+."""
from __future__ import annotations

from typing import Any, Dict


MEMORY_POLICY = {
    "schema": "TINO_MEMORY_POLICY_V1093",
    "public": {
        "read": ["public_prediction_history", "public_audit_summary"],
        "write": [],
        "scope": "queried_ticker_only",
        "ttl_days": 30,
        "private_watchlist_access": False,
        "promotion_gate": "DENY",
        "reject_gate": "ANY_RUNTIME_WRITE",
    },
    "admin": {
        "read": ["prediction_log", "audit_log", "ticker_profiles", "system_status"],
        "write": ["prediction_log", "audit_log", "approved_profile_updates"],
        "scope": "authenticated_admin",
        "ttl_days": 365,
        "private_watchlist_access": True,
        "promotion_gate": "TINO_ADMIN_APPROVE",
        "reject_gate": "UNVERIFIED_OR_FALLBACK_SAMPLE",
    },
}


def memory_permission(role: str, operation: str, resource: str) -> Dict[str, Any]:
    actor = "admin" if str(role or "").lower() == "admin" else "public"
    rule = dict(MEMORY_POLICY[actor])
    allowed = str(resource) in set(rule.get(str(operation).lower(), []))
    return {
        "schema": MEMORY_POLICY["schema"], "role": actor,
        "operation": str(operation).lower(), "resource": str(resource),
        "allowed": allowed, "promotion_gate": rule.get("promotion_gate"),
        "reject_gate": rule.get("reject_gate"),
    }
