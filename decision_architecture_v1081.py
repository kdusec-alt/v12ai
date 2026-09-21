# -*- coding: utf-8 -*-
"""TINO V1081 unified web-decision architecture.

This is one shared execution/narrative arbiter for every TW/US ticker.  It never
changes formal Direction, T0/T1/High/Low, Confidence, Prediction DNA, Auto Audit,
Genome, Research weights or calibration schedules.

Priority order:
1. Price/Session truth and hard vetoes.
2. Individual selling pressure, failed structure, relative weakness and verified
   company/fundamental risk.
3. Limit liquidity and closed-session constraints.
4. Chase protection before right-side confirmation.
5. VWAP position and selling exhaustion / individual confirmation.
6. Event waiting may only downgrade a green state; it can never promote risk.

No ticker code, company name or one-off sector rule is allowed here.
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
import math
import re
from typing import Any, Dict, Iterable, Mapping, Sequence


SCHEMA = "TINO_WEB_DECISION_ARCHITECTURE_V1085"

_STATE_META = {
    # Internal states remain stable for Audit compatibility. Public labels must be
    # executable decisions; V1085 arbitration may further promote/downgrade them.
    "BUY_TODAY_CONFIRM": ("green", "🟢", "買進"),
    "WAIT_VWAP_PULLBACK": ("yellow", "🟡", "續抱／空手不進"),
    "WAIT_VWAP_RECLAIM": ("red", "🔴", "禁止進場"),
    "WAIT_RECLAIM_HOLD": ("yellow", "🟡", "續抱／空手不進"),
    "WAIT_NEXT_SESSION": ("red", "🔴", "禁止進場"),
    "LIMIT_LIQUIDITY_WAIT": ("yellow", "🟡", "續抱／禁止追價"),
    "OVERHEATED_NO_CHASE": ("yellow", "🟠", "減碼／禁止追價"),
    "SELLING_EXPANSION_BLOCK": ("red", "🔴", "賣出"),
    "FAILED_BREAKOUT_EXIT": ("red", "🔴", "賣出"),
    "DATA_WAIT": ("red", "⚪", "禁止進場"),
}

_SELLING_STATES = {"panic_acceleration", "deleveraging", "selling_expansion"}
_EXHAUSTION_STATES = {"selling_exhaustion", "bottom_probe"}
_STRONG_THESIS_STATES = {
    "bad_news_absorbed", "surge_divergence", "limit_breakout",
    "strong_continuation", "countertrend_breakout", "conditional_attack",
    "rebound_confirmed", "trend_repair",
}
_EVENT_WAIT_STATES = {"event_awaiting_market_reaction", "event_reaction_in_progress"}
_HARD_BLOCK_STATES = {
    "forecast_cooldown", "forecast_cooldown_event_pending",
    "earnings_expectation_reset", "good_news_rejected", "company_risk_confirmed",
    "capital_raise_repricing", "industry_narrative_repricing", "macro_beta_selloff",
    "positioning_selloff", "session_repricing", "trend_break",
}
_ACTIVE_CONFIRM_SESSIONS = {"intraday", "close_confirm", "pre_market"}
_CLOSED_SESSIONS = {
    "after_hours", "after_close", "official_close", "closed_reference", "closed", "休市",
}

_PROXY_ALIASES = {
    "TAIEX": ("TAIEX", "加權指數", "台股加權", "上市指數"),
    "TPEX": ("TPEX", "櫃買指數", "上櫃指數", "OTC"),
    "SOX": ("SOX", "費半", "費城半導體"),
    "NQ": ("NQ", "NASDAQ 100", "NASDAQ100", "那斯達克100", "那指期"),
    "QQQ": ("QQQ",),
    "SMH": ("SMH",),
    "SPY": ("SPY", "S&P 500", "S&P500"),
    "TSM_ADR": ("TSM_ADR", "TSM ADR", "台積電ADR"),
}

_FUNDAMENTAL_NEGATIVE = (
    "財報差", "財報不佳", "低於預期", "未達預期", "不如預期", "虧損",
    "獲利衰退", "eps衰退", "eps 下滑", "毛利率下滑", "營益率下滑",
    "財測下修", "展望下修", "獲利預警", "營收預警", "需求放緩",
    "增資", "稀釋", "折價發行", "misses estimates", "below estimates",
    "profit warning", "guidance cut", "lowered guidance", "weak outlook",
    "margin decline", "earnings miss",
)
_FUNDAMENTAL_POSITIVE = (
    "優於預期", "高於預期", "擊敗預期", "獲利成長", "eps成長", "eps 成長",
    "毛利率提升", "財測上修", "展望上修", "上修財測", "上調展望",
    "beats estimates", "above estimates", "guidance raised", "strong outlook",
)
_FUNDAMENTAL_TRUSTED_SOURCE_MARKERS = (
    "正式財報", "公開資訊觀測站", "mops", "twse", "tpex", "櫃買中心",
    "sec filing", "sec.gov", "edgar", "10-q", "10-k", "8-k",
    "investor relations", "company ir", "公司公告", "法說會簡報",
    "finmind", "yahooquotesummary", "yahoo quote summary",
)
_STALE_MARKERS = ("stale_reindexed", "舊聞重新收錄", "old_reindexed")


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "--", "NA", "待同步", "暫停"):
            return None
        number = float(str(value).replace(",", "").replace("%", "").strip())
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _first_number(value: Any) -> float | None:
    match = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", _text(value))
    return _num(match.group(0)) if match else None


def _price(value: Any) -> str:
    number = _num(value)
    if number is None:
        return "--"
    if abs(number) >= 1000:
        return f"{number:,.0f}"
    if abs(number) >= 100:
        return f"{number:,.2f}".rstrip("0").rstrip(".")
    return f"{number:,.2f}"


def _ticker_market(forecast: Any) -> str:
    return _text(getattr(getattr(forecast, "ticker", None), "market", "")).upper()


def _all_evidence_text(forecast: Any) -> str:
    decision = _mapping(getattr(forecast, "decision_card", {}))
    radar = _mapping(getattr(forecast, "radar", {}))
    pieces: list[str] = []
    for value in radar.values():
        if isinstance(value, (str, int, float)):
            pieces.append(_text(value))
    for key, value in decision.items():
        if not str(key).startswith("_") and isinstance(value, (str, int, float)):
            pieces.append(_text(value))
    for item in list(getattr(forecast, "news_items", []) or []):
        if isinstance(item, Mapping):
            pieces.extend((_text(item.get("title")), _text(item.get("tag"))))
        else:
            pieces.extend((_text(getattr(item, "title", "")), _text(getattr(item, "tag", ""))))
    return "｜".join(piece for piece in pieces if piece)


@lru_cache(maxsize=256)
def _proxy_changes_cached(source: str) -> tuple[tuple[str, float], ...]:
    output: Dict[str, float] = {}
    for canonical, aliases in _PROXY_ALIASES.items():
        values: list[float] = []
        for alias in aliases:
            pattern = rf"{re.escape(alias)}\s*[:：]?\s*([+-]?\d+(?:\.\d+)?)\s*%"
            for match in re.finditer(pattern, source, flags=re.I):
                value = _num(match.group(1))
                if value is not None:
                    values.append(value)
        if values:
            output[canonical] = values[-1]
    return tuple(sorted(output.items()))


def _proxy_changes(text: str) -> Dict[str, float]:
    return dict(_proxy_changes_cached(_text(text)))


def _flatten_selected(payloads: Iterable[Any]) -> str:
    pieces: list[str] = []

    def walk(value: Any, depth: int = 0) -> None:
        if depth > 3:
            return
        if isinstance(value, Mapping):
            for key, item in value.items():
                if not str(key).startswith("trace"):
                    pieces.append(_text(key))
                    walk(item, depth + 1)
        elif isinstance(value, (list, tuple, set)):
            for item in list(value)[:24]:
                walk(item, depth + 1)
        elif isinstance(value, (str, int, float, bool)):
            pieces.append(_text(value))

    for payload in payloads:
        walk(payload)
    return "｜".join(piece for piece in pieces if piece)


@lru_cache(maxsize=256)
def _fundamental_text_state_cached(text: str) -> str:
    low = text.lower()
    negative = any(term.lower() in low for term in _FUNDAMENTAL_NEGATIVE)
    positive = any(term.lower() in low for term in _FUNDAMENTAL_POSITIVE)
    if negative and positive:
        return "mixed"
    if negative:
        return "negative"
    if positive:
        return "positive"
    return "unknown"


def _truth_payload(decision: Mapping[str, Any], thesis: Mapping[str, Any]) -> Dict[str, Any]:
    output: Dict[str, Any] = {}
    if isinstance(decision.get("_price_truth"), Mapping):
        output.update(dict(decision["_price_truth"]))
    if isinstance(thesis.get("price_truth"), Mapping):
        output.update(dict(thesis["price_truth"]))
    return output


def _regime_payload(decision: Mapping[str, Any], thesis: Mapping[str, Any]) -> Dict[str, Any]:
    for key in ("_market_regime_v1077", "_market_regime", "_regime_state", "_shadow_market_regime"):
        if isinstance(decision.get(key), Mapping):
            return dict(decision[key])
    return dict(thesis.get("market_regime")) if isinstance(thesis.get("market_regime"), Mapping) else {}


def _session(decision: Mapping[str, Any], truth: Mapping[str, Any]) -> str:
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
    if title.startswith("休市"):
        return "closed_reference"
    return ""


def _operative_values(decision: Mapping[str, Any], truth: Mapping[str, Any], session: str) -> tuple[float, float]:
    truth_last = _num(truth.get("current_price") or truth.get("last"))
    truth_return = _num(truth.get("current_return_pct") or truth.get("return_pct"))
    decision_last = _num(decision.get("現價"))
    decision_return = _num(decision.get("漲跌幅"))
    live = bool(truth.get("live_session_quote"))
    active = session in {"pre_market", "intraday", "after_hours", "close_confirm"}
    last = truth_last if active and live and truth_last is not None else decision_last
    day_pct = truth_return if active and live and truth_return is not None else decision_return
    return float(last if last is not None else truth_last or 0.0), float(
        day_pct if day_pct is not None else truth_return or 0.0
    )


def _vwap_position(last: float, vwap: float | None) -> str:
    if vwap is None or vwap <= 0 or last <= 0:
        return "unknown"
    tolerance = max(abs(vwap) * 0.0008, 0.01)
    if last > vwap + tolerance:
        return "above"
    if last < vwap - tolerance:
        return "below"
    return "at"


def _price_shape(*, last: float, open_price: float | None, high: float | None, low: float | None) -> Dict[str, Any]:
    open_to_last = (last / open_price - 1.0) * 100.0 if open_price and open_price > 0 and last > 0 else None
    fade_from_high = (last / high - 1.0) * 100.0 if high and high > 0 and last > 0 else None
    range_position = (
        max(0.0, min(1.0, (last - low) / (high - low)))
        if high and low and high > low and last > 0 else None
    )
    return {
        "open_to_last_pct": round(open_to_last, 4) if open_to_last is not None else None,
        "fade_from_high_pct": round(fade_from_high, 4) if fade_from_high is not None else None,
        "range_position": round(range_position, 4) if range_position is not None else None,
        "near_low": bool(range_position is not None and range_position <= 0.22),
        "near_high": bool(range_position is not None and range_position >= 0.78),
        "opening_selloff": bool(open_to_last is not None and open_to_last <= -2.0),
        "opening_recovery": bool(open_to_last is not None and open_to_last >= 2.0),
    }


def _parse_datetime(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    for candidate in (raw, raw[:25], raw[:19], raw[:16]):
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            # A naive event timestamp has no provable timezone.  Do not guess
            # UTC/Taipei/New York and accidentally restart or skip the window.
            return parsed if parsed.tzinfo is not None else None
        except Exception:
            continue
    return None


def _event_context(thesis: Mapping[str, Any], decision: Mapping[str, Any], evidence: str) -> Dict[str, Any]:
    event = _mapping(thesis.get("event") or thesis.get("dominant_event") or decision.get("_event_context"))
    state = _text(thesis.get("state"))
    severity = int(_num(event.get("severity") or thesis.get("event_severity") or 0) or 0)
    elapsed = _num(
        event.get("reaction_elapsed_minutes")
        or thesis.get("reaction_elapsed_minutes")
        or decision.get("_event_reaction_elapsed_minutes")
    )
    if elapsed is None:
        start = _parse_datetime(
            event.get("first_tradable_at")
            or event.get("reaction_started_at")
            or thesis.get("first_tradable_at")
            or thesis.get("event_published_at")
            or event.get("published_at")
        )
        if start is not None:
            elapsed = max(0.0, (datetime.now(start.tzinfo) - start).total_seconds() / 60.0)
    low = evidence.lower()
    stale = bool(
        event.get("stale_reindexed")
        or thesis.get("stale_reindexed")
        or any(marker in low for marker in _STALE_MARKERS)
    )
    verification_present = False
    verified = False
    for candidate in (
        event.get("verified"), thesis.get("event_verified"), thesis.get("source_verified"),
        thesis.get("event_model_eligible"), event.get("model_eligible"),
    ):
        if candidate is not None:
            verification_present = True
            verified = bool(candidate)
            break
    if stale:
        verified = False
    window_open = bool(event.get("reaction_window_open") or thesis.get("reaction_window_open"))
    event_wait_state = state in _EVENT_WAIT_STATES
    wait_active = bool(
        event_wait_state and verification_present and verified and not stale
        and ((elapsed is not None and elapsed < 30.0) or window_open)
    )
    if stale:
        priority = "IGNORE_STALE"
    elif event_wait_state and not verified:
        priority = "VERIFY_ONLY"
    elif verified and severity >= 3:
        priority = "P1_IMMEDIATE_RECALC"
    elif verified and severity >= 2:
        priority = "P2_RECALC"
    elif verified and severity >= 1:
        priority = "P3_OBSERVE"
    else:
        priority = "NONE"
    return {
        "state": state,
        "severity": severity,
        "verified": verified,
        "verification_present": verification_present,
        "stale": stale,
        "elapsed_minutes": round(float(elapsed), 2) if elapsed is not None else None,
        "wait_active": wait_active,
        "clock_missing": bool(event_wait_state and elapsed is None and not window_open),
        "priority": priority,
    }


def _fundamental_context(decision: Mapping[str, Any], thesis: Mapping[str, Any], radar: Mapping[str, Any]) -> Dict[str, Any]:
    payloads = [
        decision.get("_earnings_intelligence"), decision.get("_fundamental_intelligence"),
        decision.get("_fundamental"), thesis.get("fundamental"), thesis.get("earnings"),
        radar.get("基本面"), radar.get("Company News"),
    ]
    text = _flatten_selected(payloads)
    state = _fundamental_text_state_cached(text.lower())
    explicit = _text(
        thesis.get("fundamental_state") or thesis.get("earnings_state")
        or _mapping(decision.get("_fundamental_intelligence")).get("state")
    ).lower()
    if explicit in {"negative", "weak", "miss", "below_expectation", "risk"}:
        state = "negative"
    elif explicit in {"positive", "strong", "beat", "above_expectation"}:
        state = "positive"
    structured = [payload for payload in payloads if isinstance(payload, Mapping)]
    structured_verified = any(
        payload.get("verified") is True
        or payload.get("accepted") is True
        or payload.get("model_eligible") is True
        or _text(payload.get("status")).lower() in {"verified", "accepted", "official"}
        for payload in structured
    )
    trusted_text = any(marker.lower() in text.lower() for marker in _FUNDAMENTAL_TRUSTED_SOURCE_MARKERS)
    explicit_verified = bool(
        thesis.get("fundamental_verified")
        or thesis.get("earnings_verified")
        or _mapping(decision.get("_earnings_intelligence")).get("verified")
    )
    verified = bool(structured_verified or trusted_text or explicit_verified)
    return {
        "state": state,
        "verified": verified,
        "verification_basis": (
            "structured" if structured_verified else "trusted_source_marker" if trusted_text
            else "explicit" if explicit_verified else "unverified"
        ),
        "text": text[:500],
    }


def _structured_same_session(decision: Mapping[str, Any], truth: Mapping[str, Any]) -> bool:
    for payload in (
        decision.get("_session_truth_v1081"), decision.get("_market_session_truth"),
        truth.get("cross_asset_session_truth"),
    ):
        if isinstance(payload, Mapping):
            if payload.get("same_session") is True:
                return True
            if payload.get("same_session") is False:
                return False
    return False


def _market_context(*, market: str, symbol: str, day_pct: float, evidence: str,
                    decision: Mapping[str, Any], truth: Mapping[str, Any]) -> Dict[str, Any]:
    changes = _proxy_changes(evidence)
    structured = _mapping(decision.get("_market_proxy_v1081"))
    for canonical in _PROXY_ALIASES:
        value = _num(structured.get(canonical.lower()))
        if value is None:
            value = _num(structured.get(canonical))
        if value is not None:
            changes[canonical] = value
    session_payload = _mapping(decision.get("_session_truth_v1081"))
    eligible = {str(key).upper() for key in (session_payload.get("eligible_keys") or [])}
    if eligible:
        changes = {key: value for key, value in changes.items() if key.upper() in eligible}
    session_verified = _structured_same_session(decision, truth)
    if market == "TW":
        candidates = (
            [changes.get("TPEX"), changes.get("TAIEX")]
            if str(symbol or "").upper().endswith(".TWO")
            else [changes.get("TAIEX"), changes.get("TPEX")]
        )
        support_keys = ("TAIEX", "TPEX", "SOX", "NQ", "TSM_ADR")
    else:
        candidates = [changes.get("QQQ"), changes.get("NQ"), changes.get("SPY"), changes.get("SOX")]
        support_keys = ("QQQ", "NQ", "SPY", "SOX", "SMH")
    benchmark = next((value for value in candidates if value is not None), None)
    gap = day_pct - benchmark if benchmark is not None and session_verified else None
    severe_weak = bool(gap is not None and (gap <= -4.0 or (benchmark >= 3.0 and day_pct < 0)))
    positive = {key: changes[key] for key in support_keys if changes.get(key) is not None and changes[key] >= 0.5}
    negative = {key: changes[key] for key in support_keys if changes.get(key) is not None and changes[key] <= -0.5}
    supportive = len(positive) >= 2
    return {
        "proxy_changes": changes,
        "positive_proxies": positive,
        "negative_proxies": negative,
        "supportive": supportive,
        "confirmed": bool(supportive and session_verified and day_pct >= 0 and not severe_weak),
        "session_verified": session_verified,
        "benchmark_return_pct": benchmark,
        "relative_gap_pct": round(gap, 4) if gap is not None else None,
        "severe_relative_weakness": severe_weak,
        "relative_strength": bool(gap is not None and gap >= 1.5),
    }


def _limit_context(*, market: str, day_pct: float, thesis_state: str,
                   decision: Mapping[str, Any], last: float,
                   high: float | None, low: float | None) -> Dict[str, Any]:
    rule = _mapping(decision.get("_exchange_rule") or _mapping(decision.get("_price_meta")).get("exchange_rule"))
    limit_like = bool(market == "TW" and day_pct >= 9.5) or thesis_state == "limit_breakout"
    explicit_lock = rule.get("is_limit_up_locked")
    if explicit_lock is None:
        explicit_lock = rule.get("limit_locked")
    if explicit_lock is None:
        explicit_lock = decision.get("_limit_up_locked")
    frozen = bool(
        limit_like and high is not None and low is not None and last > 0
        and abs(high - last) <= max(last * 0.0005, 0.01)
        and abs(low - last) <= max(last * 0.0005, 0.01)
    )
    return {
        "limit_like": limit_like,
        "explicit_locked": explicit_lock is True,
        "explicit_opened": explicit_lock is False,
        "frozen_print": frozen,
        "liquidity_verified": explicit_lock is not None,
    }


def _failed_breakout(*, last: float, stop: float | None, vwap_position: str,
                     thesis_state: str, regime_state: str) -> bool:
    if stop is not None and stop > 0 and last > 0 and last < stop:
        return True
    if thesis_state in {"good_news_rejected", "trend_break"} and vwap_position != "above":
        return True
    return regime_state in _SELLING_STATES and vwap_position == "below"


def _price_tiles(*, state: str, last: float, vwap: float | None, low: float | None,
                 confirmation: float | None, stop: float | None,
                 no_chase: float | None, first: float | None) -> list[Dict[str, str]]:
    vp, lp, cp, sp, np = (_price(vwap), _price(low), _price(confirmation), _price(stop), _price(no_chase))
    if state == "BUY_TODAY_CONFIRM":
        return [
            {"label": "先鋒", "value": f"現價 {_price(last)} 小量確認"},
            {"label": "回測", "value": f"VWAP {vp} 不破" if vwap else "首次量縮回測"},
            {"label": "加碼", "value": f"站穩 {cp}" if confirmation else "突破平台確認"},
            {"label": "短停", "value": f"跌破VWAP {vp} 未收回" if vwap else "突破平台失守"},
            {"label": "不追", "value": np if no_chase else "急拉過熱不追"},
        ]
    if state == "WAIT_VWAP_PULLBACK":
        return [
            {"label": "回測", "value": f"VWAP {vp}" if vwap else "首次量縮回測"},
            {"label": "確認", "value": "回測不破且賣壓量縮"},
            {"label": "加碼", "value": f"站穩 {cp}" if confirmation else "重新放量轉強"},
            {"label": "失效", "value": f"VWAP {vp} 跌破未收回" if vwap else sp},
            {"label": "不追", "value": np if no_chase else "偏離VWAP過大"},
        ]
    if state == "WAIT_VWAP_RECLAIM":
        return [
            {"label": "狀態", "value": f"先收復 VWAP {vp}" if vwap else "等待結構收復"},
            {"label": "支撐", "value": lp if low else (_price(first) if first else "低點不再下移")},
            {"label": "確認", "value": f"站回 {vp} 後維持／回踩不破" if vwap else "收復後維持5–15分鐘"},
            {"label": "失效", "value": f"跌破 {lp}" if low else (sp if stop else "再創盤中新低")},
            {"label": "不追", "value": "急拉碰VWAP不直接追"},
        ]
    if state == "WAIT_RECLAIM_HOLD":
        return [
            {"label": "位置", "value": f"VWAP {vp} 附近" if vwap else "多空交界"},
            {"label": "確認", "value": "維持5–15分鐘或回踩不破"},
            {"label": "轉強", "value": f"站穩 {cp}" if confirmation else "高點抬升"},
            {"label": "失效", "value": f"跌回VWAP {vp} 下方" if vwap else sp},
            {"label": "不追", "value": np if no_chase else "量價未確認不追"},
        ]
    if state == "LIMIT_LIQUIDITY_WAIT":
        return [
            {"label": "成交", "value": "漲停排隊／成交不確定"},
            {"label": "限價", "value": f"僅掛 {_price(last)} 小量"},
            {"label": "開板", "value": "先看賣壓與VWAP承接"},
            {"label": "失效", "value": f"開板跌破 {_price(vwap or low)}" if (vwap or low) else "開板後賣壓擴張"},
            {"label": "不追", "value": "不得加價追單"},
        ]
    if state == "OVERHEATED_NO_CHASE":
        return [
            {"label": "現況", "value": f"不追 {_price(last)}"},
            {"label": "等待", "value": f"VWAP {vp}／突破平台" if vwap else "量縮回測"},
            {"label": "確認", "value": "回測後重新放量"},
            {"label": "失效", "value": f"跌破 {lp}" if low else sp},
            {"label": "不追", "value": np if no_chase else "第三段加速"},
        ]
    if state == "SELLING_EXPANSION_BLOCK":
        return [
            {"label": "進場", "value": "禁止接刀"},
            {"label": "止跌", "value": f"不再跌破 {lp}" if low else "低點停止下移"},
            {"label": "收復", "value": f"VWAP {vp}" if vwap else "重新站回關鍵均價"},
            {"label": "轉強", "value": f"站穩 {cp}" if confirmation else "量價結構修復"},
            {"label": "風險", "value": sp if stop else (lp if low else "再創新低")},
        ]
    if state == "FAILED_BREAKOUT_EXIT":
        return [
            {"label": "進場", "value": "取消原計畫"},
            {"label": "修復", "value": f"收復VWAP {vp}" if vwap else "站回突破平台"},
            {"label": "確認", "value": f"站穩 {cp}" if confirmation else "重新放量"},
            {"label": "停手", "value": sp if stop else "原結構失守"},
            {"label": "不追", "value": "未修復前不新增"},
        ]
    if state == "WAIT_NEXT_SESSION":
        return [
            {"label": "時段", "value": "下一正式交易時段重算"},
            {"label": "開盤", "value": "先看缺口是否守住"},
            {"label": "承接", "value": "賣壓量縮與VWAP"},
            {"label": "失效", "value": f"跌破 {lp}" if low else "缺口完全回補"},
            {"label": "舊錨", "value": f"{_price(first)} 僅供Audit" if first else "不沿用舊買點"},
        ]
    return [
        {"label": "資料", "value": "等待同源價格"},
        {"label": "Session", "value": "等待時段確認"},
        {"label": "VWAP", "value": "尚未驗證"},
        {"label": "風險", "value": "不建立買點"},
        {"label": "Audit", "value": "原預測保留"},
    ]


def _build_message(state: str, reasons: Sequence[str], tiles: Sequence[Mapping[str, str]]) -> str:
    reason = reasons[0] if reasons else "等待更多證據"
    values = [str(tile.get("value") or "") for tile in tiles]
    if state == "BUY_TODAY_CONFIRM":
        return f"今日可小量參與：{reason}。先鋒只做確認單；{values[1]}，第二筆再加碼；{values[3]}。"
    if state == "WAIT_VWAP_PULLBACK":
        return f"今日等回測：{reason}。{values[0]}；{values[1]}才參與，{values[3]}。"
    if state == "WAIT_VWAP_RECLAIM":
        return f"等待收復確認：{reason}。{values[0]}不是直接買價；{values[2]}才可小量，{values[3]}。"
    if state == "WAIT_RECLAIM_HOLD":
        return f"收復後等承接：{reason}。{values[1]}；未完成前不把碰觸VWAP視為買點。"
    if state == "LIMIT_LIQUIDITY_WAIT":
        return f"漲停成交條件待確認：{reason}。可用限價小量排隊，但成交不確定；開板後重新檢查賣壓、VWAP與回封品質。"
    if state == "WAIT_NEXT_SESSION":
        return f"等下一交易時段：{reason}。目前不新增追價；下一正式時段先驗證缺口、量能與VWAP。"
    if state == "OVERHEATED_NO_CHASE":
        return f"過熱不追：{reason}。{values[0]}；{values[1]}後再重新評估。"
    if state == "SELLING_EXPANSION_BLOCK":
        return f"賣壓未止，禁止接刀：{reason}。至少等待低點停止下移、賣壓量縮並收復VWAP。"
    if state == "FAILED_BREAKOUT_EXIT":
        return f"突破失敗／取消：{reason}。未完成結構修復前不新增部位。"
    return "價格、Session或驗證資料尚未完成，不建立進場價格。"


def assess_entry_opportunity(forecast: Any) -> Dict[str, Any]:
    decision = _mapping(getattr(forecast, "decision_card", {}))
    radar = _mapping(getattr(forecast, "radar", {}))
    thesis = _mapping(decision.get("_decision_thesis") or decision.get("_decision_narrative"))
    truth = _truth_payload(decision, thesis)
    regime = _regime_payload(decision, thesis)

    market = _ticker_market(forecast)
    session = _session(decision, truth)
    last, day_pct = _operative_values(decision, truth, session)
    open_price = _num(decision.get("開盤") or decision.get("今日開盤") or truth.get("open"))
    high = _num(decision.get("最高") or decision.get("今日高") or truth.get("high"))
    low = _num(decision.get("最低") or decision.get("今日低") or truth.get("low"))
    vwap = _num(truth.get("vwap"))
    if vwap is None:
        vwap = _first_number(decision.get("VWAP位置"))
    first = _num(decision.get("低接第一批"))
    second = _num(decision.get("低接第二批"))
    stop = _num(decision.get("防守"))
    no_chase = _num(decision.get("不追"))
    confirmation = _first_number(decision.get("轉強")) or _first_number(decision.get("攻擊"))

    vwap_position = _vwap_position(last, vwap)
    shape = _price_shape(last=last, open_price=open_price, high=high, low=low)
    thesis_state = _text(thesis.get("state"))
    entry_permission = _text(thesis.get("entry_permission"))
    action_mode = _text(thesis.get("action_mode"))
    regime_state = _text(regime.get("state") or regime.get("current_state"))
    evidence = _all_evidence_text(forecast)
    event = _event_context(thesis, decision, evidence)
    fundamental = _fundamental_context(decision, thesis, radar)
    market_ctx = _market_context(
        market=market,
        symbol=_text(getattr(getattr(forecast, "ticker", None), "resolved_symbol", "")),
        day_pct=day_pct,
        evidence=evidence,
        decision=decision,
        truth=truth,
    )
    limit_ctx = _limit_context(
        market=market, day_pct=day_pct, thesis_state=thesis_state,
        decision=decision, last=last, high=high, low=low,
    )

    hard_truth_block = bool(
        _mapping(decision.get("_price_meta")).get("decision_blocked")
        or truth.get("decision_blocked") or thesis_state == "price_truth_blocked"
    )
    selling_expansion = bool(
        regime_state in _SELLING_STATES
        or (day_pct <= -4.0 and vwap_position == "below")
        or (
            len(market_ctx["negative_proxies"]) >= 2 and day_pct < 0
            and vwap_position == "below" and market_ctx["session_verified"]
        )
    )
    failed = _failed_breakout(
        last=last, stop=stop, vwap_position=vwap_position,
        thesis_state=thesis_state, regime_state=regime_state,
    )
    fundamental_veto = bool(
        fundamental["state"] == "negative" and fundamental["verified"]
        and (
            vwap_position == "below" or shape["near_low"]
            or shape["opening_selloff"] or market_ctx["severe_relative_weakness"]
        )
    )
    negative_absorption_watch = bool(
        fundamental["state"] == "negative" and fundamental["verified"]
        and not fundamental_veto and vwap_position in {"above", "at"} and day_pct > 0
    )
    severe_individual_weakness = bool(
        market_ctx["severe_relative_weakness"] and vwap_position == "below"
        and (shape["near_low"] or day_pct < 0)
    )
    strong_threshold = 3.0 if market == "US" else 4.0
    strong_tape = bool(
        day_pct >= strong_threshold or thesis_state in _STRONG_THESIS_STATES
        or "A突破" in _text(_mapping(decision.get("_direction_engine")).get("gate_state"))
    )
    old_anchor_far = bool(first and first > 0 and last > 0 and last / first - 1.0 >= 0.07)
    no_chase_crossed = bool(no_chase and last >= no_chase)
    event_repricing = bool(
        event["verified"] and event["priority"] in {"P1_IMMEDIATE_RECALC", "P2_RECALC"}
        and strong_tape
    )
    anchor_invalidated = bool(
        (event_repricing or market_ctx["confirmed"] or thesis_state in _STRONG_THESIS_STATES)
        and (old_anchor_far or no_chase_crossed)
    )
    benchmark = market_ctx["benchmark_return_pct"]
    excess_vs_benchmark = day_pct - benchmark if benchmark is not None else None
    # A right-side confirmation is not automatically a good entry.  Once the
    # session has already repriced sharply, the system must wait for a pullback
    # instead of rewarding the same green candle again through VWAP, state and
    # price-strength signals.  Verified event repricing still keeps the bullish
    # direction, but it does not waive entry-price discipline.
    chase_guard = bool(
        market == "TW"
        and vwap_position == "above"
        and day_pct >= 3.0
        and not limit_ctx["limit_like"]
        and not negative_absorption_watch
        and not event["wait_active"]
    )
    overextended = bool(
        day_pct >= (20.0 if market == "US" else 9.8)
        or (excess_vs_benchmark is not None and excess_vs_benchmark >= 11.0)
        or (no_chase_crossed and not anchor_invalidated)
        or chase_guard
    )
    tradable_session = session in _ACTIVE_CONFIRM_SESSIONS

    reasons: list[str]
    if hard_truth_block or last <= 0:
        state, reasons = "DATA_WAIT", ["即時價格或參考基準尚未通過同源驗證"]
    elif failed and not selling_expansion:
        state, reasons = "FAILED_BREAKOUT_EXIT", ["價格已跌破防守或原突破結構"]
    elif selling_expansion:
        state = "SELLING_EXPANSION_BLOCK"
        reasons = ["賣壓仍在擴張，且基本面存在反證" if fundamental_veto else "賣壓仍在擴張，跌深本身不是買點"]
    elif fundamental_veto:
        state, reasons = "SELLING_EXPANSION_BLOCK", ["基本面反證與弱勢價格同時成立"]
    elif severe_individual_weakness:
        state, reasons = "SELLING_EXPANSION_BLOCK", ["大盤／產業轉強，但個股仍明顯逆勢走弱"]
    elif thesis_state in _HARD_BLOCK_STATES or (entry_permission == "blocked" and day_pct <= 0):
        state, reasons = "SELLING_EXPANSION_BLOCK", ["上層風險重定價或進場閘門仍未解除"]
    elif limit_ctx["limit_like"] and not limit_ctx["explicit_opened"]:
        state, reasons = "LIMIT_LIQUIDITY_WAIT", ["價格已到漲停附近，但封單與開板承接尚未完整驗證"]
    elif session in _CLOSED_SESSIONS:
        state, reasons = "WAIT_NEXT_SESSION", ["目前已非可驗證的正式盤中新進場時段"]
    elif chase_guard:
        state, reasons = "OVERHEATED_NO_CHASE", [
            "方向可能正確，但當日漲幅已進入追價風險區，改等量縮回測"
        ]
    elif vwap_position == "below":
        state = "WAIT_VWAP_RECLAIM"
        reasons = [
            "開高後賣壓明顯，現價仍在VWAP下方" if shape["opening_selloff"]
            else "現價位於VWAP下方且接近盤中低點" if shape["near_low"]
            else "價格尚未收復時段VWAP，確認門檻不是直接買價"
        ]
    elif vwap_position == "at":
        state, reasons = "WAIT_RECLAIM_HOLD", ["價格位於VWAP多空交界，需要維持或回踩確認"]
    elif overextended:
        state, reasons = "OVERHEATED_NO_CHASE", ["方向可能正確，但漲幅或價格偏離已過度延伸"]
    elif negative_absorption_watch:
        state, reasons = "WAIT_VWAP_PULLBACK", ["基本面偏弱但價格暫時吸收，仍需回測確認而非直接追價"]
    elif regime_state in _EXHAUSTION_STATES and vwap_position == "above" and tradable_session:
        state, reasons = "BUY_TODAY_CONFIRM", ["賣壓已由擴張轉為衰竭，且價格收復VWAP"]
    elif (
        vwap_position == "above" and tradable_session and strong_tape
        and not shape["opening_selloff"] and not shape["near_low"]
        and fundamental["state"] != "negative"
        and (
            thesis_state in _STRONG_THESIS_STATES or market_ctx["relative_strength"]
            or event_repricing or market_ctx["confirmed"]
        )
    ):
        state, reasons = "BUY_TODAY_CONFIRM", ["個股價格站穩VWAP且自身結構完成轉強"]
    elif vwap_position == "above" and tradable_session:
        state, reasons = "WAIT_VWAP_PULLBACK", ["價格位於VWAP上方，但仍需首次量縮回測確認承接"]
    elif action_mode in {"pullback", "pullback_or_confirmation", "confirmation_only"}:
        state = "WAIT_VWAP_PULLBACK" if vwap_position == "above" else "WAIT_RECLAIM_HOLD"
        reasons = ["等待同一交易時段的價格確認，不再預設回到舊低接價"]
    else:
        state, reasons = "WAIT_RECLAIM_HOLD", ["目前優勢不足以直接追價，等待可驗證的價格結構"]

    # One-way event cap: only green may be downgraded inside a verified window.
    if event["wait_active"] and state == "BUY_TODAY_CONFIRM":
        state = (
            "WAIT_VWAP_PULLBACK" if vwap_position == "above"
            else "WAIT_VWAP_RECLAIM" if vwap_position == "below"
            else "WAIT_RECLAIM_HOLD"
        )
        reasons = [f"事件首輪反應仍在有效觀察窗（已經過 {event['elapsed_minutes']:.0f} 分鐘）"]

    color, icon, label = _STATE_META[state]
    tiles = _price_tiles(
        state=state, last=last, vwap=vwap, low=low, confirmation=confirmation,
        stop=stop, no_chase=no_chase, first=first,
    )

    conditions: list[Dict[str, Any]] = []
    if vwap_position == "above":
        conditions.append({"ok": True, "text": "價格位於時段VWAP上方"})
    elif vwap_position == "below":
        conditions.append({"ok": False, "text": "價格仍在時段VWAP下方"})
    elif vwap_position == "at":
        conditions.append({"ok": False, "text": "價格位於VWAP多空交界"})
    else:
        conditions.append({"ok": False, "text": "VWAP資料尚未完成驗證"})

    gap = market_ctx["relative_gap_pct"]
    if gap is not None:
        conditions.append({
            "ok": gap >= 0,
            "text": f"個股相對市場 {gap:+.2f}%" if gap >= 0 else f"個股落後市場 {abs(gap):.2f}%",
        })
    elif market_ctx["supportive"]:
        conditions.append({
            "ok": market_ctx["confirmed"],
            "text": "跨市場環境同向，但Session未驗證，不代替個股確認"
            if not market_ctx["session_verified"] else "跨市場環境與個股同向",
        })
    else:
        conditions.append({"ok": False, "text": "相對市場／產業基準不足"})

    if fundamental["state"] == "negative" and fundamental["verified"]:
        conditions.append({"ok": False, "text": "基本面存在已驗證反證"})
    elif fundamental["state"] == "negative":
        conditions.append({"ok": False, "text": "基本面負面訊號未驗證，不作正式否決"})
    elif fundamental["state"] == "positive":
        conditions.append({"ok": True, "text": "基本面方向提供支持"})
    else:
        conditions.append({"ok": False, "text": "基本面不加分也不自行推論"})

    if event["clock_missing"]:
        conditions.append({"ok": False, "text": "事件反應時間未驗證，不套固定15–30分鐘模板"})
    elif event["wait_active"]:
        conditions.append({"ok": False, "text": f"事件首輪反應 {event['elapsed_minutes']:.0f}/30 分鐘"})
    elif event["stale"]:
        conditions.append({"ok": False, "text": "舊聞重新收錄已排除"})
    elif event["verified"] and event["priority"] != "NONE":
        conditions.append({"ok": True, "text": "重大事件已驗證並完成重新仲裁"})

    if anchor_invalidated:
        conditions.insert(0, {"ok": True, "text": "舊低接錨點已降級為Audit參考"})
    if selling_expansion:
        conditions.insert(0, {"ok": False, "text": "賣壓仍在擴張"})
    if shape["opening_selloff"]:
        conditions.insert(0, {"ok": False, "text": f"開盤後回落 {abs(shape['open_to_last_pct'] or 0):.2f}%"})

    message = _build_message(state, reasons, tiles)
    summary_parts = list(reasons)
    if anchor_invalidated:
        summary_parts.append("原低接價保留於Audit，不再作為目前主要買點")
    elif event["clock_missing"]:
        summary_parts.append("事件時鐘缺證，不使用永久等待模板")

    return {
        "schema": SCHEMA,
        "state": state,
        "label": label,
        "color": color,
        "icon": icon,
        "summary": "；".join(summary_parts[:2]),
        "conditions": conditions,
        "canonical_main_message": message,
        "price_strategy_text": "｜".join(f"{row['label']} {row['value']}" for row in tiles[:2]),
        "price_tiles": tiles,
        "anchor_invalidated": anchor_invalidated,
        "original_low_entry_audit": {"first": first, "second": second, "stop": stop, "no_chase": no_chase},
        "market": market,
        "session": session,
        "operative_price": round(last, 6),
        "operative_return_pct": round(day_pct, 4),
        "vwap": vwap,
        "vwap_position": vwap_position,
        "price_shape": shape,
        "fundamental": fundamental,
        "market_context": market_ctx,
        "event_context": event,
        "limit_context": limit_ctx,
        "reassessment_priority": event["priority"],
        "thesis_state": thesis_state,
        "regime_state": regime_state,
        "show_score": False,
        "narrative_only": True,
        "formal_forecast_unchanged": True,
    }


assess_entry_timing = assess_entry_opportunity
