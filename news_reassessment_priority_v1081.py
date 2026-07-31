# -*- coding: utf-8 -*-
"""Verified-news priority guard for V1081 event reassessment.

V1078/V1079 remain the source of event identity, severity and timestamp
eligibility.  This layer normalises execution priority only.  A fingerprint is
a deduplication identity, never proof that the source, timestamp or content was
verified.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping


SCHEMA = "TINO_NEWS_REASSESSMENT_PRIORITY_V1081"


def _explicit_verification(row: Mapping[str, Any]) -> tuple[bool, bool]:
    """Return (field_present, verified) from accepted upstream truth fields."""
    for key in (
        "event_verified",
        "source_verified",
        "content_verified",
        "timestamp_verified",
        "model_eligible",
        "timestamp_model_eligible",
        "event_model_eligible",
    ):
        if key in row and row.get(key) is not None:
            return True, bool(row.get(key))
    return False, False


def prioritize_reassessment_plan(plan: Mapping[str, Any] | None) -> Dict[str, Any]:
    row = dict(plan or {})
    severity = max(0, min(4, int(row.get("event_severity") or row.get("severity") or 0)))
    stale = bool(
        row.get("stale_reindexed")
        or row.get("timestamp_status") == "stale_reindexed"
        or row.get("event_timestamp_status") == "stale_reindexed"
    )
    verification_present, verified = _explicit_verification(row)
    requested = bool(row.get("needs_reassessment"))
    eligible = bool(requested and verification_present and verified and not stale)

    if stale:
        priority = "IGNORE_STALE"
        reason = "舊聞重新收錄，不重算"
    elif requested and not verification_present:
        priority = "VERIFY_ONLY"
        reason = "事件缺少明確驗證結果，指紋僅供去重，不重算"
    elif requested and not verified:
        priority = "VERIFY_ONLY"
        reason = "事件來源、內容或時間未通過驗證，不重算"
    elif eligible and severity >= 3:
        priority = "P1_IMMEDIATE"
        reason = "已驗證重大事件，優先完整重算"
    elif eligible and severity >= 2:
        priority = "P2_RECALCULATE"
        reason = "已驗證重要事件，完整重算"
    elif eligible:
        priority = "P3_RECHECK"
        reason = "已驗證事件，重新檢查價格反應"
    else:
        priority = "NONE"
        reason = "無新重大事件"

    row.update({
        "schema_v1081": SCHEMA,
        "event_verified": bool(verified),
        "verification_present": verification_present,
        "stale_reindexed": stale,
        "reassessment_priority": priority,
        "priority_reason": reason,
        "needs_reassessment": eligible,
        "immediate_recompute": bool(eligible and severity >= 2),
        "full_pipeline_required": eligible,
        "formal_forecast_mutated_in_place": False,
    })
    return row
