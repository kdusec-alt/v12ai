# -*- coding: utf-8 -*-
"""Cross-asset Session Truth for TINO V1081.

Cross-asset confirmation must match both trading date and session. A live or
pre-market NQ row cannot share one vote with previous-close QQQ/SOX/SMH merely
because the calendar date appears similar.

Taiwan has two separate needs:
- one verified same-session TAIEX/TPEX row is enough to calculate individual
  relative strength;
- cross-asset confirmation still requires at least two coherent rows.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time as dt_time
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
_SESSION_ALIASES = {
    "pre_market": ("pre_market", "premarket", "pre-market", "盤前", "美股盤前"),
    "intraday": ("intraday", "regular", "regular_session", "market_open", "盤中", "即時", "realtime", "live"),
    "after_hours": ("after_hours", "after-hours", "post_market", "postmarket", "盤後", "延長交易"),
    "official_close": (
        "official_close", "previous_close", "prior_close", "closed_reference", "after_close",
        "正式收盤", "前一收盤", "昨收", "前收", "收盤參考",
    ),
    "overnight": ("overnight", "night_session", "夜盤", "電子盤"),
}


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _date_text(value: Any) -> str:
    match = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", _text(value))
    if not match:
        return ""
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _parse_timestamp(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    candidates = [raw]
    match = re.search(
        r"20\d{2}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?(?:\s*[+-]\d{2}:?\d{2}|Z)?",
        raw,
    )
    if match:
        candidates.insert(0, match.group(0).replace("/", "-"))
    for candidate in candidates:
        normalized = candidate.strip().replace("Z", "+00:00")
        normalized = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", normalized)
        try:
            return datetime.fromisoformat(normalized)
        except Exception:
            continue
    return None


def _explicit_session(value: Any) -> str:
    low = _text(value).lower()
    for canonical, aliases in _SESSION_ALIASES.items():
        if any(alias.lower() in low for alias in aliases):
            return canonical
    return ""


def _normalize_target_session(value: str) -> str:
    explicit = _explicit_session(value)
    if explicit:
        return explicit
    low = _text(value).lower()
    if low in {"close_confirm", "closed", "after_close", "closed_reference"}:
        return "official_close"
    return low or "unknown"


def _session(forecast: Any) -> str:
    decision = _mapping(getattr(forecast, "decision_card", {}))
    thesis = _mapping(decision.get("_decision_thesis") or decision.get("_decision_narrative"))
    truth = _mapping(decision.get("_price_truth"))
    truth.update(_mapping(thesis.get("price_truth")))
    direct = _text(truth.get("session") or truth.get("market_status")).lower()
    if direct:
        return _normalize_target_session(direct)
    meta = _mapping(decision.get("_price_meta"))
    direct = _text(meta.get("session") or meta.get("market_status")).lower()
    if direct:
        return _normalize_target_session(direct)
    title = _text(decision.get("資料標題"))
    if title.startswith("盤中"):
        return "intraday"
    if title.startswith("盤前"):
        return "pre_market"
    if title.startswith("盤後"):
        return "after_hours"
    if title.startswith("收盤") or title.startswith("休市"):
        return "official_close"
    return "unknown"


def _market(forecast: Any) -> str:
    return _text(getattr(getattr(forecast, "ticker", None), "market", "")).upper()


def _target_date(forecast: Any, market: str) -> str:
    decision = _mapping(getattr(forecast, "decision_card", {}))
    thesis = _mapping(decision.get("_decision_thesis") or decision.get("_decision_narrative"))
    truth = _mapping(decision.get("_price_truth"))
    truth.update(_mapping(thesis.get("price_truth")))
    meta = _mapping(decision.get("_price_meta"))
    candidates = (
        truth.get("price_date"), truth.get("date"), truth.get("as_of"),
        meta.get("price_date"), meta.get("date"), meta.get("as_of"),
        getattr(forecast, "price_date", ""), decision.get("價格日期"), decision.get("價格時間"),
    )
    for value in candidates:
        parsed = _date_text(value)
        if parsed:
            return parsed
    return datetime.now(_NEW_YORK if market == "US" else _TAIPEI).date().isoformat()


def _session_from_clock(value: Any, market: str) -> str:
    parsed = _parse_timestamp(value)
    if parsed is None or parsed.tzinfo is None:
        return "unknown"
    local = parsed.astimezone(_NEW_YORK if market == "US" else _TAIPEI)
    clock = local.time().replace(tzinfo=None)
    if market == "US":
        if dt_time(4, 0) <= clock < dt_time(9, 30):
            return "pre_market"
        if dt_time(9, 30) <= clock < dt_time(16, 0):
            return "intraday"
        if dt_time(16, 0) <= clock < dt_time(20, 0):
            return "after_hours"
        return "overnight"
    if dt_time(8, 30) <= clock < dt_time(13, 35):
        return "intraday"
    if dt_time(13, 35) <= clock < dt_time(15, 30):
        return "official_close"
    return "overnight"


def _proxy_rows(proxies: Mapping[str, Any], market: str) -> Dict[str, Dict[str, str]]:
    as_of = _mapping(proxies.get("as_of"))
    session_map = _mapping(proxies.get("session") or proxies.get("sessions") or proxies.get("market_status"))
    output: Dict[str, Dict[str, str]] = {}
    for key in _RELEVANT.get(market, ()):
        if proxies.get(key) is None:
            continue
        raw_as_of = as_of.get(key)
        explicit = _explicit_session(session_map.get(key)) or _explicit_session(raw_as_of)
        output[key] = {
            "as_of": _text(raw_as_of),
            "date": _date_text(raw_as_of),
            "session_bucket": explicit or _session_from_clock(raw_as_of, market) or "unknown",
        }
    return output


def _preferred_group(
    groups: Mapping[str, list[str]], *, target_key: str, exact_minimum: int,
) -> tuple[str, list[str]]:
    candidates = [(key, sorted(values)) for key, values in groups.items() if key and values]
    if not candidates:
        return "", []
    exact = sorted(groups.get(target_key) or [])
    if len(exact) >= max(1, int(exact_minimum)):
        return target_key, exact
    candidates.sort(
        key=lambda item: (len(item[1]), 1 if item[0] == target_key else 0, item[0]),
        reverse=True,
    )
    return candidates[0]


def build_cross_asset_session_truth(
    forecast: Any, proxies: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    data = dict(proxies or {})
    market = _market(forecast)
    target_session = _session(forecast)
    target_date = _target_date(forecast, market)
    rows = _proxy_rows(data, market)
    groups: Dict[str, list[str]] = defaultdict(list)
    for key, meta in rows.items():
        groups[f"{meta.get('date') or 'unknown_date'}|{meta.get('session_bucket') or 'unknown'}"].append(key)

    target_key = f"{target_date}|{target_session}"
    # One verified Taiwan cash index is sufficient for relative-strength
    # benchmarking. US/cross-asset confirmation keeps a two-row quorum.
    exact_minimum = 1 if market == "TW" else 2
    vote_key, eligible = _preferred_group(
        groups, target_key=target_key, exact_minimum=exact_minimum,
    )
    vote_date, _, vote_session = vote_key.partition("|")
    eligible_set = set(eligible)
    all_keys = sorted(rows)
    excluded = sorted(key for key in all_keys if key not in eligible_set)

    exact_session = bool(
        vote_key == target_key and target_session not in {"unknown", "overnight"}
    )
    benchmark_same_session = bool(
        exact_session
        and (
            market == "US" and len(eligible) >= 2
            or market == "TW" and any(key in {"taiex", "tpex"} for key in eligible)
        )
    )
    cross_asset_same_session = bool(exact_session and len(eligible) >= 2)
    if market == "TW" and cross_asset_same_session:
        cross_asset_same_session = any(key in {"taiex", "tpex"} for key in eligible)

    if not eligible:
        vote_scope = "insufficient"
    elif cross_asset_same_session:
        vote_scope = "same_session"
    elif benchmark_same_session:
        vote_scope = "same_session_benchmark"
    elif len(eligible) >= 2:
        vote_scope = "coherent_context_session"
    else:
        vote_scope = "insufficient"

    proxy_rows = {
        key: {
            **meta,
            "eligible": key in eligible_set,
            "reason": "coherent_vote_group" if key in eligible_set else "different_session_excluded",
        }
        for key, meta in rows.items()
    }
    return {
        "schema": SCHEMA,
        "market": market,
        "target_session": target_session,
        "target_date": target_date,
        "target_group_key": target_key,
        "vote_group_key": vote_key,
        "vote_group_date": vote_date if vote_date != "unknown_date" else "",
        "vote_group_session": vote_session or "unknown",
        "vote_scope": vote_scope,
        # Compatibility: individual benchmark logic may use this. Cross-asset
        # voting must inspect cross_asset_same_session as well.
        "same_session": benchmark_same_session,
        "benchmark_same_session": benchmark_same_session,
        "cross_asset_same_session": cross_asset_same_session,
        "eligible_keys": eligible,
        "excluded_keys": excluded,
        "proxy_rows": proxy_rows,
        "group_sizes": {key: len(value) for key, value in sorted(groups.items())},
        "verified": bool(benchmark_same_session or len(eligible) >= 2),
        "reason": (
            "同日期同Session跨資產資料可進確認"
            if cross_asset_same_session else
            "同Session台股指數可作個股相對強弱基準；跨資產確認仍不足"
            if benchmark_same_session else
            "跨資產資料僅作一致時段背景，不作個股當前Session確認"
            if len(eligible) >= 2 else
            "可用同日期同Session資料不足"
        ),
        "decision_influence": "session_filter_only",
    }


def attach_session_truth(
    forecast: Any, proxies: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    truth = build_cross_asset_session_truth(forecast, proxies)
    try:
        card = dict(getattr(forecast, "decision_card", {}) or {})
        card["_session_truth_v1081"] = truth
        card["_market_proxy_v1081"] = {
            key: value for key, value in dict(proxies or {}).items()
            if key in set(_RELEVANT.get(_market(forecast), ()))
        }
        forecast.decision_card = card
    except Exception:
        pass
    return truth
