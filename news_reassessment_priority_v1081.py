# -*- coding: utf-8 -*-
"""Verified-news priority guard for V1081 event reassessment.

The existing V1078/V1079 classifiers remain the source of event identity,
severity and timestamp eligibility.  This layer only normalises execution
priority: eligible material news may request the established full analysis
pipeline; stale, re-indexed or unverified rows can never trigger it.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping


SCHEMA = "TINO_NEWS_REASSESSMENT_PRIORITY_V1081"


def prioritize_reassessment_plan(plan: Mapping[str, Any] | None) -> Dict[str, Any]:
    row = dict(plan or {})
    severity = max(0, min(4, int(row.get("event_severity") or row.get("severity") or 0)))
    stale = bool(
        row.get("stale_reindexed")
        or row.get("timestamp_status") == "stale_reindexed"
        or row.get("event_timestamp_status") == "stale_reindexed"
    )
    explicit_verified = row.get("event_verified")
    if explicit_verified is None:
        explicit_verified = row.get("model_eligible")
    if explicit_verified is None:
        explicit_verified = row.get("timestamp_model_eligible")
    # assess_event_delta already applies V1079.  When it explicitly requests a
    # reassessment and supplies no legacy verification field, treat that request
    # as the verified result of the upstream guard, not as a raw headline.
    verified = bool(explicit_verified) if explicit_verified is not None else bool(
        row.get("needs_reassessment") and row.get("event_fingerprint")
    )
    requested = bool(row.get("needs_reassessment"))
    eligible = bool(requested and verified and not stale)

    if stale:
        priority = "IGNORE_STALE"
        reason = "舊聞重新收錄，不重算"
    elif requested and not verified:
        priority = "VERIFY_ONLY"
        reason = "事件來源或時間未驗證，不重算"
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
        "event_verified": verified,
        "stale_reindexed": stale,
        "reassessment_priority": priority,
        "priority_reason": reason,
        "needs_reassessment": eligible,
        "immediate_recompute": bool(eligible and severity >= 2),
        "full_pipeline_required": eligible,
        "formal_forecast_mutated_in_place": False,
    })
    return row
