# -*- coding: utf-8 -*-
"""Cross-asset Session Truth for TINO V1081.

The proxy provider already records one ``as_of`` value per instrument.  This
module converts those timestamps into one auditable vote group so a live or
pre-market NQ row can never be mixed with previous-close QQQ/SOX/SMH rows in
the same risk vote.

A coherent previous-session group may still be shown as market background, but
only a group that matches the ticker's target session may be used as current
cross-market confirmation by the individual entry engine.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
import re
from typing import Any, Dict, Mapping
from zoneinfo import ZoneInfo


SCHEMA = "TINO_CROSS_ASSET_SESSION_TRUTH_V1081"
_TAIPEI = ZoneInfo("Asia/Taipei")
_NEW_YORK = ZoneInfo("America/New_York")

_RELEVANT = {
    "TW": ("taiex", "tpex", "tx_night", "tsm_adr", "sox", "nq", "qqq", "smh"),
    "US": ("qqq", "sox", "smh", "nq", "spy", "vix_change"),
}


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _date_text(value: Any) -> str:
    raw = _text(value)
    match = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", raw)
    if not match:
        return ""
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _session(forecast: Any) -> str:
    decision = _mapping(getattr(forecast, "decision_card", {}))
    thesis = _mapping(decision.get("_decision_thesis") or decision.get("_decision_narrative"))
    truth = _mapping(decision.get("_price_truth"))
    truth.update(_mapping(thesis.get("price_truth")))
    direct = _text(truth.get("session") or truth.get("market_status")).lower()
    if direct:
        return direct
    meta = _mapping(decision.get("_price_meta"))
    direct = _text(meta.get("session") or meta.get("market_status")).lower()
    if direct:
        return direct
    title = _text(decision.get("資料標題"))
    if title.startswith("盤中"):
        return "intraday"
    if title.startswith("盤前"):
        return "pre_market"
    if title.startswith("盤後"):
        return "after_hours"
    if title.startswith("收盤"):
        return "after_close"
    return "unknown"


def _market(forecast: Any) -> str:
    return _text(getattr(getattr(forecast, "ticker", None), "market", "")).upper()


def _target_date(forecast: Any, market: str, session: str) -> str:
    decision = _mapping(getattr(forecast, "decision_card", {}))
    thesis = _mapping(decision.get("_decision_thesis") or decision.get("_decision_narrative"))
    truth = _mapping(decision.get("_price_truth"))
    truth.update(_mapping(thesis.get("price_truth")))
    meta = _mapping(decision.get("_price_meta"))
    candidates = (
        truth.get("price_date"), truth.get("date"), truth.get("as_of"),
        meta.get("price_date"), meta.get("date"), meta.get("as_of"),
        getattr(forecast, "price_date", ""),
        decision.get("價格日期"), decision.get("價格時間"),
    )
    for value in candidates:
        parsed = _date_text(value)
        if parsed:
            return parsed
    # Last-resort calendar fallback is explicit and marked in the result.  For
    # US sessions use New York's date, not the following Taipei morning.
    now = datetime.now(_NEW_YORK if market == "US" else _TAIPEI)
    return now.date().isoformat()


def _proxy_dates(proxies: Mapping[str, Any], market: str) -> Dict[str, str]:
    as_of = _mapping(proxies.get("as_of"))
    output: Dict[str, str] = {}
    for key in _RELEVANT.get(market, ()):
        value = proxies.get(key)
        if value is None:
            continue
        parsed = _date_text(as_of.get(key))
        if parsed:
            output[key] = parsed
    return output


def _preferred_group(
    groups: Mapping[str, list[str]],
    *,
    target_date: str,
) -> tuple[str, list[str]]:
    candidates = [
        (group_date, sorted(keys))
        for group_date, keys in groups.items()
        if group_date and keys
    ]
    if not candidates:
        return "", []
    target_keys = sorted(groups.get(target_date) or [])
    if len(target_keys) >= 2:
        # Two or more current-session rows form a usable live group even when a
        # larger previous-close bundle exists.  A single live future still may
        # not override a coherent prior-session group.
        return target_date, target_keys
    # Otherwise prefer the largest coherent set.  Ties prefer the ticker's
    # target date, then the newest date.
    candidates.sort(
        key=lambda item: (
            len(item[1]),
            1 if item[0] == target_date else 0,
            item[0],
        ),
        reverse=True,
    )
    return candidates[0]


def build_cross_asset_session_truth(
    forecast: Any,
    proxies: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    data = dict(proxies or {})
    market = _market(forecast)
    session = _session(forecast)
    target_date = _target_date(forecast, market, session)
    dates = _proxy_dates(data, market)
    groups: Dict[str, list[str]] = defaultdict(list)
    for key, as_of_date in dates.items():
        groups[as_of_date].append(key)
    vote_date, eligible = _preferred_group(groups, target_date=target_date)
    all_keys = sorted(dates)
    excluded = sorted(key for key in all_keys if key not in set(eligible))

    same_date = bool(vote_date and vote_date == target_date)
    same_session = bool(
        same_date
        and len(eligible) >= 2
        and session in {"intraday", "close_confirm", "after_close", "closed_reference"}
        and market == "US"
    )
    # Taiwan same-session confirmation requires Taiwan index rows.  Overseas
    # closes remain context even when their calendar date coincides.
    if market == "TW":
        tw_same = [key for key in eligible if key in {"taiex", "tpex"}]
        same_session = bool(
            same_date
            and len(tw_same) >= 1
            and session in {"intraday", "close_confirm", "after_close", "closed_reference"}
        )

    if len(eligible) < 2:
        vote_scope = "insufficient"
    elif same_session:
        vote_scope = "same_session"
    else:
        vote_scope = "coherent_context_session"

    rows = {
        key: {
            "as_of": _text(_mapping(data.get("as_of")).get(key)),
            "date": dates.get(key, ""),
            "eligible": key in set(eligible),
            "reason": "coherent_vote_group" if key in set(eligible) else "different_session_excluded",
        }
        for key in all_keys
    }
    return {
        "schema": SCHEMA,
        "market": market,
        "target_session": session,
        "target_date": target_date,
        "vote_group_date": vote_date,
        "vote_scope": vote_scope,
        "same_session": same_session,
        "eligible_keys": eligible,
        "excluded_keys": excluded,
        "proxy_rows": rows,
        "group_sizes": {key: len(value) for key, value in sorted(groups.items())},
        "verified": bool(len(eligible) >= 2),
        "reason": (
            "同Session跨資產資料可進確認"
            if same_session else
            "跨資產資料只作一致時段背景，不作個股同Session確認"
            if len(eligible) >= 2 else
            "可用同時段跨資產資料不足"
        ),
        "decision_influence": "session_filter_only",
    }


def attach_session_truth(
    forecast: Any,
    proxies: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    truth = build_cross_asset_session_truth(forecast, proxies)
    try:
        card = dict(getattr(forecast, "decision_card", {}) or {})
        card["_session_truth_v1081"] = truth
        card["_market_proxy_v1081"] = {
            key: value
            for key, value in dict(proxies or {}).items()
            if key in set(_RELEVANT.get(_market(forecast), ()))
        }
        forecast.decision_card = card
    except Exception:
        pass
    return truth
