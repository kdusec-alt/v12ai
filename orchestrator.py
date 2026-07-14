# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, List, Optional
from dataclasses import replace
import os
import re
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from arbitration import cap_total_adjustment, clamp, is_price_neutral_module, signal_price_adjustment
from config import HIGH_MAGNET_BUFFER, MAX_CONFIDENCE, MIN_CONFIDENCE, REQUIRED_RADAR_ROWS
from debug_trace import ensure_required_trace_rows, trace_step_from_signal
from direction_engine import DirectionResult, build_direction_forecast
from direction_orchestration import (
    direction_ensemble as _direction_ensemble,
    direction_label_zh as _direction_label_zh,
    quantum_tactical_overlay as _quantum_tactical_overlay,
    tactical_confidence as _tactical_confidence,
)
from features_common import common_signals
from features_etf import etf_signals
from features_tw import tw_signals
from features_us import us_signals
from forecast_engine import build_raw_forecast
from trend_engine import build_trend_snapshot, trend_radar_line, trend_tag
from models import FinalForecast, NewsItem, PredictionTrace, PriceFrame, RawForecast, SignalPacket, TraceStep
from price_guard import apply_market_bounds, validate_price_frame
from truth_guard import truth_to_main_label
from data_sources_market_heat import fetch_tw_market_heat, market_heat_radar_line
from bubble_radar import assess_bubble_risk, bubble_radar_line
try:
    from macro_event_calendar import macro_calendar_guard_text
except Exception:
    def macro_calendar_guard_text(*args, **kwargs):
        return "宏觀事件待同步"
try:
    from v13_research.macro_event_engine import compact_macro_event_line
except Exception:
    def compact_macro_event_line(*args, **kwargs):
        return ""

try:
    from event_intelligence import assess_policy_geo
except Exception:
    def assess_policy_geo(*args, **kwargs):
        return {
            "line": "Policy/Geo｜觀察｜事件資料待同步",
            "score": 0.0,
            "risk": 0.0,
            "bias": 0.0,
            "confidence": 0.0,
            "uncertainty": 0.0,
            "reason": "event_intelligence_unavailable",
            "level": "觀察",
            "labels": [],
            "channels": [],
            "sectors": [],
            "top_title": "",
            "matched_count": 0,
        }
def collect_signals(price: PriceFrame, manual_macro: str = "neutral") -> List[SignalPacket]:
    signals = common_signals(price, manual_macro)
    if price.ticker.asset_type == "etf":
        signals.extend(etf_signals(price))
    elif price.ticker.market == "TW":
        signals.extend(tw_signals(price))
    else:
        signals.extend(us_signals(price))
    return signals
def _stop_forecast(price: PriceFrame, reason: str) -> FinalForecast:
    trace = PredictionTrace(price.ticker.resolved_symbol, None, [], None)
    return FinalForecast(price.ticker, True, reason, None, None, None, None, None, 0.0, None, None, {}, ["STOP"], "價格無效，停止產生預測。", "資料不可用", {}, trace, [price.truth], "", [], [])
def _money(v) -> str:
    try:
        return f"{float(v):+,.0f}"
    except Exception:
        return "待同步"
def _fmt(v, digits: int = 2) -> str:
    try:
        return f"{float(v):.{digits}f}"
    except Exception:
        return "待同步"
def _lots(v) -> str:
    return f"{_money(v)}張" if _money(v) != "待同步" else "待同步"
def _pct(v, digits: int = 2) -> str:
    try:
        return f"{float(v):+.{digits}f}%"
    except Exception:
        return "待同步"
def _price_snapshot(price: PriceFrame | None) -> dict:
    if price is None:
        return {}
    snap = ((price.context or {}).get("price_snapshot") or {})
    if not isinstance(snap, dict):
        snap = {}
    return snap
def _ssot_vwap_state(price: PriceFrame | None) -> str:
    """VWAP must be calculated from SSOT numeric fields, never parsed from text."""
    if price is None:
        return "VWAP觀察"
    snap = _price_snapshot(price)
    try:
        last = float(snap.get("last", price.last))
        vwap = float(snap.get("vwap", price.vwap))
        return "VWAP 上方" if last >= vwap else "VWAP 下方"
    except Exception:
        return "VWAP 上方" if price.last >= price.vwap else "VWAP 下方"


def _first_friday_local(year: int, month: int) -> date:
    d = date(year, month, 1)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def _macro_calendar_guard_text() -> str:
    """One frontend-safe official calendar shared by TW and US routes."""
    return macro_calendar_guard_text()

def _futures_delta_text(net, delta) -> str:
    try:
        d = int(float(str(delta).replace(',', '')))
    except Exception:
        return "淨部位待前日比對"
    if d == 0:
        return "淨部位持平"
    if float(net) < 0:
        return f"淨空增加 {abs(d):,}口" if d < 0 else f"淨空減少 {abs(d):,}口"
    if float(net) > 0:
        return f"淨多增加 {abs(d):,}口" if d > 0 else f"淨多減少 {abs(d):,}口"
    return f"淨部位變化 {d:+,}口"


def _futures_brief(futures: dict) -> str:
    if not isinstance(futures, dict) or not futures.get("accepted") or futures.get("net_oi") in (None, ""):
        return "期貨｜待同步｜未納入V2"
    try:
        net = int(float(str(futures.get("net_oi")).replace(',', '')))
    except Exception:
        return "期貨｜待同步｜未納入V2"
    if abs(net) <= 1000:
        return "期貨｜待同步｜未納入V2"
    date_txt = str(futures.get("date") or "日期待同步")
    side = "淨空" if net < 0 else "淨多" if net > 0 else "中性"
    delta_txt = str(futures.get("delta_label") or _futures_delta_text(net, futures.get("delta")))
    risk = str(futures.get("risk_level") or "觀察")
    return f"期貨｜{date_txt}｜臺股期貨｜外資{side} {net:+,}口｜{delta_txt}｜結算壓力 {risk}"


def _foreign_flow_line(price: PriceFrame | None) -> str:
    if price is None:
        return "外資V2｜查詢中｜期貨待同步"
    ctx = price.context or {}
    amount_line = _foreign_amount_line(ctx) or "外資V2｜觀察｜估買賣壓待同步"
    futures = ctx.get('futures', {}) if isinstance(ctx.get('futures', {}), dict) else {}
    return f"{str(amount_line).strip('｜')}｜{_futures_brief(futures)}"


def _news_text(item: NewsItem) -> str:
    return f"{str(getattr(item, 'title', '') or '')} {str(getattr(item, 'tag', '') or '')}".lower()


def _has_any(text: str, keys: list[str]) -> bool:
    return any(str(k).lower() in text for k in keys)


def _short_title(title: str, limit: int = 24) -> str:
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    return title[:limit] + "…" if len(title) > limit else title


def _quantum_macro_policy_assessment(
    news_items: List[NewsItem] | None = None,
    macro: dict | None = None,
    market: str = "",
) -> dict:
    """Shared policy/geopolitical assessment.

    RC4.2 delegates literal detection and economic-transmission mapping to the
    lightweight Event Intelligence layer.  Macro calendar events remain in the
    separate Event/Macro route and are not double-counted here.
    """
    result = assess_policy_geo(
        news_items or [],
        market=str(market or ""),
        profile="general",
    )
    # Preserve the legacy dictionary contract consumed by SignalPacket/radar.
    return {
        "line": result.get("line") or "Policy/Geo｜觀察｜近端政策/地緣事件未形成方向",
        "score": float(result.get("score") or 0.0),
        "risk": float(result.get("risk") or 0.0),
        "bias": float(result.get("bias") or 0.0),
        "confidence": float(result.get("confidence") or 0.0),
        "uncertainty": float(result.get("uncertainty") or 0.0),
        "reason": str(result.get("reason") or "policy_geo_observation"),
        "level": str(result.get("level") or "觀察"),
        "labels": list(result.get("labels") or []),
        "channels": list(result.get("channels") or []),
        "sectors": list(result.get("sectors") or []),
        "matched_count": int(result.get("matched_count") or 0),
    }

def _geo_risk_from_news(news_items: List[NewsItem] | None = None) -> str:
    # Backward-compatible name: main UI row now uses the broader Policy/Geo engine.
    return _quantum_macro_policy_assessment(news_items).get("line", "Policy/Geo｜觀察")


def _macro_line(macro: dict, price: PriceFrame | None = None, raw: RawForecast | None = None, news_items: List[NewsItem] | None = None) -> str:
    # Macro row must only contain macro-calendar information.  EPS / revenue /
    # stock-level event metadata belongs to 基本面, not here.
    geo = _quantum_macro_policy_assessment(news_items, macro, getattr(getattr(price, "ticker", None), "market", "")).get("line", "Policy/Geo｜觀察")
    if isinstance(macro, dict) and bool(macro.get('accepted')):
        cal = str(macro.get('calendar') or _macro_calendar_guard_text())
        strength = str(macro.get('strength') or '中')
        cross = []
        for label, key in (("台指夜盤", "tx_night"), ("費半", "sox"), ("那指", "nq"), ("MU", "mu")):
            value = macro.get(key)
            if value is None and key == "nq":
                value = macro.get("qqq")
            try:
                cross.append(f"{label} {float(value):+.2f}%")
            except Exception:
                continue
        cross_text = "｜跨市場 " + " / ".join(cross[:4]) if cross else ""
        result_line = compact_macro_event_line(macro, news_items)
        if result_line:
            return f"{result_line}｜下一事件 {cal}｜強度 {strength}{cross_text}｜{geo}"
        return f"Macro｜{cal}｜強度 {strength}{cross_text}｜{geo}"
    result_line = compact_macro_event_line(macro if isinstance(macro, dict) else {}, news_items)
    if result_line:
        return f"{result_line}｜{geo}"
    return f"Macro｜{_macro_calendar_guard_text()}｜強度 中｜{geo}"


def _foreign_amount_line(ctx: dict) -> str:
    if not isinstance(ctx, dict):
        return ""
    flow_v2 = ctx.get('foreign_flow_v2', {}) if isinstance(ctx.get('foreign_flow_v2', {}), dict) else {}
    if flow_v2 and flow_v2.get('accepted') and flow_v2.get('amount_billion') not in (None, '', '待估'):
        display = str(flow_v2.get('display') or '').strip('｜')
        if display:
            return display.replace('今日預估大盤', '').replace('今日預估', '')
        direction = str(flow_v2.get('direction_label') or '觀察')
        amount = flow_v2.get('amount_range') or flow_v2.get('amount_billion')
        amount_txt = str(amount) if str(amount).endswith('億內') or '～' in str(amount) else f"{amount}億"
        tone = '賣壓' if str(flow_v2.get('direction')) == 'sell' else '買盤' if str(flow_v2.get('direction')) == 'buy' else '中性'
        return f"外資V2｜{direction}｜估{tone} {amount_txt}｜{flow_v2.get('alert','壓力觀察')}"
    tv = ctx.get('tv_pressure', {}) if isinstance(ctx.get('tv_pressure', {}), dict) else {}
    if tv and tv.get('accepted') and tv.get('amount_billion') not in (None, '', '待估'):
        direction_raw = str(tv.get('direction', '預估大盤外資買賣壓'))
        if '賣壓' in direction_raw:
            tone = '賣壓'
        elif '買盤' in direction_raw or '買超' in direction_raw:
            tone = '買盤'
        else:
            tone = '中性'
        amount = tv.get('amount_billion')
        amount_txt = str(amount) if str(amount).endswith('億內') else f"{amount}億"
        return f"外資V2｜觀察｜估{tone} {amount_txt}｜{tv.get('alert','壓力觀察')}"
    return "外資V2｜待同步"


def _futures_line(futures: dict, price: PriceFrame | None = None) -> str:
    return _foreign_flow_line(price)
def _fundamental_line(fundamental: dict, etf_note: str = "", price: PriceFrame | None = None, news_items: List[NewsItem] | None = None) -> str:
    if etf_note:
        return etf_note
    name = price.ticker.name if price else "個股"
    f = fundamental or {}
    if f.get('accepted'):
        parts = []
        month = str(f.get('month') or '').strip()
        target = str(f.get('target_month') or '').strip()
        quality = str(f.get('revenue_quality') or '').strip()
        revenue = f.get('revenue')
        if month:
            if target and month != target:
                parts.append(f"最新可用 {month}")
            else:
                parts.append(month)
        if revenue:
            if quality == 'reference' or not f.get('revenue_model_usable', f.get('cross_checked', True)):
                parts.append(f"月營收參考 {revenue}")
            else:
                parts.append(f"當月 {revenue}")
        elif f.get('revenue_status'):
            parts.append(str(f.get('revenue_status')))
        if f.get('mom'):
            parts.append(f"MoM {f.get('mom')}")
        if f.get('yoy'):
            parts.append(f"YoY {f.get('yoy')}")
        if f.get('accum_revenue'):
            parts.append(f"累計 {f.get('accum_revenue')}")
        if f.get('accum_yoy'):
            parts.append(f"累計YoY {f.get('accum_yoy')}")
        announcement_date = str(f.get('announcement_date') or '').strip()
        if announcement_date:
            parts.append(f"公告 {announcement_date}")
            parts.append("短線催化只計公告日與次交易日")

        eps = f.get('eps')
        eps_date = f.get('eps_date')
        if eps:
            q = f.get('eps_quarter') or '最近季'
            if f.get('eps_stale'):
                parts.append(f"EPS參考 {q} {eps}")
                if eps_date:
                    parts.append(f"EPS日期 {eps_date}")
                parts.append('EPS待更新')
            else:
                parts.append(f"EPS {q} {eps}")
                if eps_date:
                    parts.append(f"EPS日期 {eps_date}")
        else:
            parts.append('EPS待同步')

        def _pretty_fund_source(src: object) -> str:
            txt = str(src or '').strip()
            mapping = {
                'FinMind_MonthRevenue': 'FinMind月營收',
                'FinMind_FinancialStatements': 'FinMind財報',
                'MOPS_MonthRevenue': 'MOPS官方月營收',
                'GoodinfoRevenue': 'Goodinfo營收',
                'YahooRevenue': 'Yahoo營收',
                'AnueRevenue': '鉅亨營收',
                'MoneyDJRevenueNews': 'MoneyDJ營收新聞',
                'TW_FUNDAMENTAL_MULTI_SOURCE_PENDING': '多來源待同步',
                'TW_FUNDAMENTAL_NEEDS_CROSSCHECK': '月營收待官方交叉',
                'V9_VERIFIED_MOPS_EPS_MEMORY': 'MOPS/EPS已驗證',
            }
            return mapping.get(txt, txt.replace('V9_VERIFIED_MOPS_EPS_MEMORY','MOPS/EPS已驗證'))

        status = str(f.get('revenue_status') or '').strip()
        if status and revenue and quality not in {'official', 'cross_checked'}:
            parts.append(status)
        if f.get('revenue_month_anchor_risk'):
            parts.append('月份錨點待確認')
        rev_src = _pretty_fund_source(f.get('revenue_source') or f.get('source',''))
        cross_sources = str(f.get('cross_sources') or '').strip()
        if cross_sources:
            pretty = '+'.join(_pretty_fund_source(x.strip()) for x in cross_sources.split(',') if x.strip())
            if pretty:
                parts.append(f"月營收交叉 {pretty}")
        elif rev_src:
            parts.append(f"月營收來源 {rev_src}")
        if revenue and not f.get('revenue_model_usable', f.get('cross_checked', True)):
            parts.append('月營收未入模型')
        eps_src = _pretty_fund_source(f.get('eps_source') or '')
        if eps_src and eps:
            parts.append(f"EPS來源 {eps_src}")
        clean = []
        seen = set()
        for x in parts:
            sx = str(x).strip()
            if not sx or sx.endswith('None') or sx in seen:
                continue
            seen.add(sx)
            clean.append(sx)
        return f"{name}｜" + "｜".join(clean)
    return f"{name}｜月營收/EPS 查詢中｜先看價格/VWAP/法人資券"
def _session_words(price: PriceFrame) -> Dict[str, str]:
    status = getattr(price, "market_status", "closed_reference")
    if status == "intraday":
        return {"info": "盤中資料", "main": "盤中操盤", "mode": "台股盤中版", "semantic": "盤中即時路徑參考，正式 T1 指向下一交易日收盤。", "anchor": "台股盤中 Reality Anchor"}
    if status == "after_close":
        return {"info": "收盤資料", "main": "明日操盤", "mode": "台股收盤正式版", "semantic": "收盤後使用今日收盤資料，正式 T1 指向下一交易日收盤。", "anchor": "台股收盤 Reality Anchor"}
    if status == "pre_market":
        return {"info": "盤前參考資料", "main": "盤前操盤", "mode": "台股盤前參考版", "semantic": "盤前使用最近交易日資料，僅作今日路徑參考。", "anchor": "台股盤前 Reality Anchor"}
    return {"info": "休市資料", "main": "休市參考", "mode": "台股休市參考版", "semantic": "休市期間使用最近交易日資料，僅作參考。", "anchor": "台股休市 Reality Anchor"}
def _us_session_words(price: PriceFrame) -> Dict[str, str]:
    table={"pre_market":("盤前參考資料","開盤前操盤","美股盤前雷達","盤前使用最近正式收盤與盤前/期貨/宏觀校準；正式 T1 指向今晚收盤。","美股盤前 Reality Anchor"),"intraday":("盤中資料","盤中操盤","美股盤中雷達","盤中以現價/VWAP/量價同步校準；正式 T1 指向今晚收盤。","美股盤中 Reality Anchor"),"after_hours":("盤後資料","盤後觀察","美股盤後雷達","盤後只校準風險與隔日盤前，不硬改正式收盤價。","美股盤後 Reality Anchor"),"closed_reference":("休市資料","休市參考","美股休市雷達","休市期間使用最近正式收盤與宏觀/財報事件作為參考。","美股休市 Reality Anchor")}
    a,b,c,d,e=table.get(getattr(price,"market_status","closed_reference"),table["closed_reference"])
    return {"info":a,"main":b,"mode":c,"semantic":d,"anchor":e}

def _signal_map(signals: List[SignalPacket]) -> Dict[str, SignalPacket]:
    return {s.module: s for s in signals}
def _formal_ok(block: dict) -> bool:
    source = str(block.get("source", ""))
    return bool(block.get("accepted", False)) and "V12_DERIVED_PROXY" not in source and "PROXY" not in source.upper()
def _high_magnet_guard(final_t1: float, high: float, low: float) -> float:
    span = max(high - low, 0.01)
    return min(final_t1, high - span * HIGH_MAGNET_BUFFER)
def _streak_label(price: PriceFrame) -> str:
    # RC2.3 Final: use TrendEngine close-to-close calculation.
    # Do not add daily percentages; cumulative return is close_today / close_N_days_ago - 1.
    return trend_tag(price)

def _news_summary(news_items: List[NewsItem]) -> Dict[str, object]:
    news_items = news_items or []
    accepted = [n for n in news_items if abs(float(n.score)) >= 0.06]
    ignored = max(0, len(news_items) - len(accepted))
    score = sum(float(n.score) for n in accepted)
    tags = "、".join(sorted({n.tag for n in accepted})[:3]) if accepted else "headline_neutral"
    top = accepted[0].title if accepted else (news_items[0].title if news_items else "新聞待同步")
    bias = max(min(score * 0.08, 0.04), -0.04)
    return {"count": len(news_items), "accepted": len(accepted), "ignored": ignored, "score": score, "tags": tags, "top": top, "bias": bias}
def _parse_streak_days(text: object) -> tuple[str, int]:
    s = str(text or "")
    m = re.search(r"連([買賣])(\d+)天", s)
    if not m:
        return "", 0
    return m.group(1), int(m.group(2))


def _to_int(v, default: int = 0) -> int:
    try:
        if v in (None, "", "--", "待同步"):
            return default
        return int(float(str(v).replace(",", "")))
    except Exception:
        return default


def _inst_actor_momentum(label: str, today, ten_day, streak_text: object) -> str:
    """法人連續買賣熱度。

    修正重點：
    - 「連買/連賣 N 天」只代表短線連續方向，不可把 10 日累計放在括號裡冒充連續天數數量。
    - 顯示分成：短線連續、今日張數、10日累計、語意結論。
    - 星等與顏色用短線方向 + 10日中期累計共同判斷。
    """
    side, days = _parse_streak_days(streak_text)
    today_i = _to_int(today)
    cum10 = _to_int(ten_day)
    if not side:
        side = "買" if today_i > 0 else "賣" if today_i < 0 else ""
        days = 1 if side else 0
    streak = str(streak_text or (f"連{side}1天" if side else "方向觀察"))

    same_dir = (side == "買" and cum10 > 0) or (side == "賣" and cum10 < 0)
    conflict = (side == "買" and cum10 < 0) or (side == "賣" and cum10 > 0)
    abs_cum = abs(cum10)
    abs_today = abs(today_i)

    # 星等：短線連續 + 今日力道 + 10日累計；短線與10日矛盾時封頂，避免假強/假弱。
    strength = 1
    if days >= 2:
        strength += 1
    if days >= 5:
        strength += 1
    if abs_today >= 1000:
        strength += 1
    if abs_cum >= 5000:
        strength += 1
    if abs_cum >= 20000:
        strength += 1
    if conflict:
        strength = min(strength, 3)
    if abs_today < 300 and abs_cum < 1000 and days <= 1:
        strength = 1
    strength = min(5, max(1, strength))
    stars = "★" * strength + "☆" * (5 - strength)

    if side == "賣" and same_dir:
        icon = "🔴" if abs_cum >= 10000 or days >= 3 else "🟡"
        tone = "中期籌碼壓力重" if abs_cum >= 50000 else ("持續調節" if days >= 3 else "短線轉弱，中期偏空")
    elif side == "買" and same_dir:
        icon = "🟢" if abs_cum >= 5000 or days >= 3 else "🟡"
        tone = "持續布局" if days >= 3 or abs_cum >= 10000 else "短線買盤觀察，中期偏多"
    elif side == "賣" and conflict:
        icon = "🟡"
        tone = "短線轉弱觀察，中期仍偏多"
    elif side == "買" and conflict:
        icon = "🟡"
        tone = "短線回補觀察，中期仍偏空"
    elif side == "買":
        icon, tone = "🟡", "短線買盤觀察"
    elif side == "賣":
        icon, tone = "🟡", "短線賣壓觀察"
    else:
        icon, tone = "🟡", "法人方向觀察"

    return (
        f"{icon} {label}　{stars}\n"
        f"短線{streak}｜今日 {_lots(today_i)}｜10日累計 {_lots(cum10)}\n"
        f"→ {tone}"
    )


def _institution_flow_momentum(inst: dict) -> str:
    if not _formal_ok(inst):
        return ""
    rows = [
        _inst_actor_momentum("外資", inst.get("foreign"), inst.get("foreign_10"), inst.get("foreign_streak")),
        _inst_actor_momentum("投信", inst.get("trust"), inst.get("trust_10"), inst.get("trust_streak")),
        _inst_actor_momentum("自營", inst.get("dealer"), inst.get("dealer_10"), inst.get("dealer_streak")),
    ]
    return "Institution Flow Momentum（法人連續買賣熱度）\n" + "\n\n".join(rows)


def _inst_line(inst: dict, multiline: bool = True) -> str:
    if not _formal_ok(inst):
        return ""
    sep = "\n" if multiline else "｜"
    return sep.join([
        f"外資 今日 {_lots(inst.get('foreign'))}｜3日 {_lots(inst.get('foreign_3'))}｜5日 {_lots(inst.get('foreign_5'))}｜10日 {_lots(inst.get('foreign_10'))}｜{inst.get('foreign_streak','')}",
        f"投信 今日 {_lots(inst.get('trust'))}｜3日 {_lots(inst.get('trust_3'))}｜5日 {_lots(inst.get('trust_5'))}｜10日 {_lots(inst.get('trust_10'))}｜{inst.get('trust_streak','')}",
        f"自營 今日 {_lots(inst.get('dealer'))}｜3日 {_lots(inst.get('dealer_3'))}｜5日 {_lots(inst.get('dealer_5'))}｜10日 {_lots(inst.get('dealer_10'))}｜{inst.get('dealer_streak','')}",
        f"法人日期：{inst.get('date')}｜來源：{inst.get('source')}｜{inst.get('reason','')}",
    ])
def _inst_radar_line(inst: dict, proxy: dict | None = None) -> str:
    if _formal_ok(inst):
        mom = _institution_flow_momentum(inst)
        return _inst_line(inst, True) + ("\n\n" + mom if mom else "")
    return "三大法人｜官方資料查詢中｜先看外資買賣壓V2、VWAP與量價；法人列不使用推估資料冒充"
def _margin_line(margin: dict, multiline: bool = True) -> str:
    if not _formal_ok(margin):
        return ""
    sep = "\n" if multiline else "｜"
    return sep.join([
        f"融資 今日 {_lots(margin.get('margin'))}｜3日 {_lots(margin.get('margin_3'))}｜5日 {_lots(margin.get('margin_5'))}｜10日 {_lots(margin.get('margin_10'))}｜{margin.get('margin_streak','')}",
        f"融券 今日 {_lots(margin.get('short'))}｜3日 {_lots(margin.get('short_3'))}｜5日 {_lots(margin.get('short_5'))}｜10日 {_lots(margin.get('short_10'))}｜{margin.get('short_streak','')}",
        f"券資比 {_fmt(margin.get('ratio'), 2)}%｜資券日期：{margin.get('date')}｜來源：{margin.get('source')}｜{margin.get('reason','')}",
    ])
def _margin_radar_line(margin: dict, proxy: dict | None = None) -> str:
    if _formal_ok(margin):
        return _margin_line(margin, True)
    return "資券｜官方資料查詢中｜先看VWAP、價格階梯與空方回補條件；資券列不使用推估資料冒充"
def _institution_alignment(inst: dict) -> str:
    vals = [_to_int(inst.get("foreign")), _to_int(inst.get("trust")), _to_int(inst.get("dealer"))]
    pos = sum(1 for v in vals if v > 0)
    neg = sum(1 for v in vals if v < 0)
    if pos >= 3:
        return "法人同步偏多"
    if neg >= 3:
        return "法人同步偏空"
    if pos >= 2 and neg == 0:
        return "法人偏多"
    if neg >= 2 and pos == 0:
        return "法人偏空"
    return "法人分歧"


def _chip_summary(inst: dict, margin: dict, bsi: dict) -> str:
    parts = []
    if _formal_ok(inst):
        align = _institution_alignment(inst)
        parts.append(f"{align}｜外資{inst.get('foreign_streak','觀察')}｜投信{inst.get('trust_streak','觀察')}")
    if _formal_ok(margin):
        parts.append(f"資券同步｜融資{margin.get('margin_streak','觀察')}｜融券{margin.get('short_streak','觀察')}｜券資比{_fmt(margin.get('ratio'),2)}%")
    parts.append("借券同步" if _formal_ok(bsi) else "借券看資券/VWAP與價格階梯")
    return "｜".join([x for x in parts if x])
def _main_clean(text: str) -> str:
    out = str(text or "")
    replacements = {
        "Dashboard Truth Guard": "", "Truth Guard": "", "WAIT_OFFICIAL": "", "RuntimeError": "",
        "Fallback": "", "fallback": "", "僅方向參考": "戰術參考", "不納入正式分數": "",
        "待同步｜": "", "待同步": "", "資料回補中": "", "待接": "", "不硬改價": "", "由 Orchestrator 採納": "",
    }
    for k, v in replacements.items():
        out = out.replace(k, v)
    while "｜｜" in out:
        out = out.replace("｜｜", "｜")
    return out.strip("｜ ")
def _tv_pressure_line(tv: dict) -> str:
    if not tv or not bool(tv.get("accepted", False)) or tv.get("amount_billion") in (None, "", "待估"):
        return ""
    direction = tv.get('direction', '預估大盤外資買賣壓')
    if direction in {'預估外資賣壓', '預估外資買盤', '預估外資中性'}:
        direction = direction.replace('預估外資', '預估大盤外資')
    if direction == '預估大盤外資買超':
        direction = '預估大盤外資買盤'
    amount = tv.get('amount_billion')
    amount_txt = str(amount) if str(amount).endswith('億內') else f"{amount}億"
    return f"{direction}：{amount_txt}｜{tv.get('alert','警戒觀察')}｜個股：{tv.get('stock_fire','主力觀察')}"
def _bsi_line(bsi: dict, proxy: dict, formal: bool = True) -> str:
    if not isinstance(bsi, dict) or not bsi:
        return "借券/SBL觀察｜先看資券、VWAP與價格階梯｜空方壓力以回補條件判讀"
    has_data = any(k in bsi for k in ("borrow_sell_3", "balance_delta_3", "cover_rate"))
    if not has_data:
        return "借券/SBL觀察｜先看資券、VWAP與價格階梯｜空方壓力以回補條件判讀"
    b3,b5,b10 = bsi.get('borrow_sell_3',0), bsi.get('borrow_sell_5',0), bsi.get('borrow_sell_10',0)
    d3,d5,d10 = bsi.get('balance_delta_3',0), bsi.get('balance_delta_5',0), bsi.get('balance_delta_10',0)
    cover, risk = bsi.get('cover_rate',0), bsi.get('risk','NA')
    head = "空方回補啟動｜反彈條件改善" if float(cover or 0) >= 60 or float(d3 or 0) < 0 else "借券賣壓觀察｜等待回補確認"
    return f"{head}｜風險 {risk}\n借賣3/5/10日：{b3:,.0f} / {b5:,.0f} / {b10:,.0f} 張\n餘額3/5/10日：{d3:+,.0f} / {d5:+,.0f} / {d10:+,.0f} 張｜回補率 {cover:.0f}%"
def _sig_line(sm: Dict[str, SignalPacket], name: str, fallback: str) -> str:
    s = sm.get(name)
    if not s:
        return fallback
    return _main_clean(f"{s.signal}｜Risk {s.risk:.0f}｜{s.reason}")
def _price_regime_line(price: PriceFrame, raw: RawForecast) -> str:
    vtxt = _ssot_vwap_state(price)
    snap = build_trend_snapshot(price)
    try:
        ret20 = float(snap.ret_20d) if snap.ret_20d is not None else 0.0
        ma20_gap = float(snap.ma20_gap_pct) if snap.ma20_gap_pct is not None else 0.0
    except Exception:
        ret20, ma20_gap = 0.0, 0.0
    overheat = ret20 >= 35.0 or ma20_gap >= 18.0
    if vtxt == "VWAP 下方":
        bias = "偏弱"
        action = "防守優先｜等回測確認"
    elif overheat:
        bias = "偏強"
        action = "強勢延伸｜站穩續抱｜急拉不追"
    else:
        bias = "偏強"
        action = "站穩續觀察"
    return f"{bias}｜{vtxt}｜{action}"
def _decision_card(price: PriceFrame, raw: RawForecast, score: float, final_t1: float, final_low: float, direction: DirectionResult | None = None, bubble: Dict[str, object] | None = None) -> Dict[str, object]:
    last, vwap, atr = float(price.last), float(price.vwap or price.last), max(float(price.atr14), 0.01)
    low1 = min(raw.raw_low_entry, final_t1 - atr * 0.08)
    low2 = min(final_low, low1 - atr * 0.28)
    attack = max(vwap, final_t1 + atr * 0.18) if last < vwap else max(last, vwap) + atr * 0.18
    turn = max(vwap, raw.raw_t1_high - atr * 0.08)
    stop = min(final_low, low2 - atr * 0.18)
    no_chase = max(raw.raw_no_chase, attack + atr * 0.25)
    fallback_bullish = last >= vwap and raw.raw_abc.get("A", 0) >= raw.raw_abc.get("C", 0)
    bullish = direction.label == "UP" if direction is not None else fallback_bullish
    bearish = direction.label == "DOWN" if direction is not None else False
    neutral_or_conflict = bool(direction is not None and (direction.label == "NEUTRAL" or direction.conflict >= 0.45))
    overlay = _quantum_tactical_overlay(direction)
    hard_defense = bool(overlay.get("hard_defense"))
    pause_second = bool(overlay.get("pause_second"))
    event_caution = bool(overlay.get("event_caution"))
    event_name = str(overlay.get("event_name") or "一級宏觀事件")
    overlay_note = str(overlay.get("note") or "")
    words = _us_session_words(price) if price.ticker.market == "US" else _session_words(price)
    snap = build_trend_snapshot(price)
    try:
        ret20 = float(snap.ret_20d) if snap.ret_20d is not None else 0.0
        ma20_gap = float(snap.ma20_gap_pct) if snap.ma20_gap_pct is not None else 0.0
    except Exception:
        ret20, ma20_gap = 0.0, 0.0
    overheat = ret20 >= 35.0 or ma20_gap >= 18.0
    below_ma20 = snap.ma20_gap_pct is not None and float(snap.ma20_gap_pct) < -1.5
    prefix = words["main"]
    if hard_defense:
        head = "AI進場決策卡｜風險共振｜先防守｜等待海外止穩"
        one = f"{prefix}：地緣/海外盤勢對產業形成負向共振；未站回 {attack:.2f} 前不搶反彈，破 {stop:.2f} 停。"
        axis = "跨市場風險共振｜防守優先"
    elif event_caution:
        head = "AI進場決策卡｜事件卡｜縮小試單｜公布後確認"
        one = f"{prefix}：{event_name}公布前不預設方向；站穩 {attack:.2f} 才試小單，回測 {low1:.2f} 止穩再分批，破 {stop:.2f} 停。"
        axis = "一級事件前｜縮小部位｜等待確認"
    elif bullish and overheat:
        head = "AI進場決策卡｜攻擊卡｜強勢延伸｜站穩才加碼"
        one = f"{prefix}：強勢但乖離偏大，站穩 {attack:.2f} 才可攻；回測 {low1:.2f} 不破再分批，{no_chase:.2f} 上方急拉不追。"
        axis = "強勢延伸｜只追確認"
    elif bullish:
        head = "AI進場決策卡｜攻擊卡｜順勢突破｜站穩加碼"
        one = f"{prefix}：站穩 {attack:.2f} 可攻，回測 {low1:.2f} 不破再分批，{no_chase:.2f} 上方急拉不追。"
        axis = "順勢突破｜站穩加碼"
    elif bearish or below_ma20:
        head = "AI進場決策卡｜防守卡｜等回測確認｜破防守停"
        one = f"{prefix}：方向偏空先防守；殺到 {low1:.2f} 附近只試小單，{low2:.2f} 才第二批，破 {stop:.2f} 收不回停。"
        axis = "防守低接｜等止穩"
    elif neutral_or_conflict:
        head = "AI進場決策卡｜觀望卡｜多空拉鋸｜等確認"
        one = f"{prefix}：多空訊號拉鋸，站穩 {attack:.2f} 才轉強；回測 {low1:.2f} 止穩才試單，破 {stop:.2f} 停。"
        axis = "多空拉鋸｜等待確認"
    else:
        head = "AI進場決策卡｜攻擊卡｜極限低接｜只做試單｜破防守停"
        one = f"{prefix}：不是不能買，是不能亂買；殺到 {low1:.2f} 附近只試小單，{low2:.2f} 才第二批，破 {stop:.2f} 收不回停。"
        axis = "保守低接｜破防守停"
    if pause_second:
        one = one.replace(f"{low2:.2f} 才第二批", "融資降溫前暫停第二批")
        axis = axis + "｜第二批暫停"
    if overlay_note:
        one = one.rstrip("。") + "。" + overlay_note + "。"

    # V13 research isolation contract:
    # Bubble remains available as an Admin/research payload and radar line, but
    # it must not change the formal AI Decision card, score, tactical wording,
    # entry levels, direction, confidence, or forecast values.
    bubble = bubble if isinstance(bubble, dict) else {}
    displayed_score = clamp(float(score), -100.0, 100.0)
    chg = last - float(price.previous_close or last)
    chgp = chg / float(price.previous_close or last) * 100 if float(price.previous_close or last) else 0.0
    price_meta = ((price.context or {}).get("price_meta") or {})
    card = {
        "標題": head, "主訊息": one, "低接第一批": round(low1, 2), "低接第二批": "暫停" if pause_second else round(low2, 2),
        "攻擊": ("事件前縮小試單" if event_caution else (f"站穩 {attack:.2f} 可攻" if bullish and not hard_defense else ("等待海外/融資止穩" if hard_defense or pause_second else f"{low1:.2f} 試單｜{low2:.2f} 再接"))),
        "轉強": f"突破 {turn:.2f} 加碼", "防守": round(stop, 2), "不追": round(no_chase, 2),
        "一句話": one.split("：", 1)[-1], "操作主軸": axis, "決策分": round(displayed_score, 2),
        "模型原因": overlay_note,
        "資料標題": words["info"], "開盤": round(float(price.open), 2), "現價": round(last, 2),
        "最高": round(float(price.high), 2), "最低": round(float(price.low), 2),
        "漲跌": round(chg, 2), "漲跌幅": round(chgp, 2), "VWAP位置": _ssot_vwap_state(price),
        "價格時間": price_meta.get("label", ""),
        # Admin-only payload. UI panels ignore underscore keys; do not render this in V9 front stage.
        "_price_meta": price_meta,
        "_market_microstructure": (price.context or {}).get("market_microstructure", {}),
        "_trend_snapshot": build_trend_snapshot(price).to_dict(),
        "_foreign_flow_v2": (price.context or {}).get("foreign_flow_v2", {}),
        "_tv_pressure": (price.context or {}).get("tv_pressure", {}),
        "_direction_engine": direction.to_dict() if direction is not None else {},
        "_quantum_overlay": overlay,
        "_bubble_radar": bubble,
    }
    if bool(price_meta.get("decision_blocked")):
        card["標題"] = "AI進場決策卡｜價格待確認｜不採用延遲價"
        card["主訊息"] = "價格資料延遲或未通過新鮮度驗證；只看風險區間，不用延遲價做正式進場。"
        card["攻擊"] = "等待即時價確認"
        card["轉強"] = "即時價恢復後再判斷"
        card["一句話"] = "價格待確認，不採用延遲價。"
        card["操作主軸"] = "價格待確認"
    return card
def _deep_report(price: PriceFrame, raw: RawForecast, final: Dict[str, float], decision: Dict[str, object], radar: Dict[str, str], signals: List[SignalPacket], confidence: float, news_items: List[NewsItem]) -> str:
    inst, margin, bsi = price.context.get("inst", {}), price.context.get("margin", {}), price.context.get("bsi", {})
    macro, tv = price.context.get("macro", {}), price.context.get("tv_pressure", {})
    news = _news_summary(news_items)
    if price.ticker.market == "US":
        short = radar.get("空方成本 / 回補", "")
        inst_text, margin_text = _us_inst_dashboard(price), _us_margin_dashboard(price)
        market_note = "美股模板｜不套台股三大法人 / 資券 / BSI｜使用 Short Float、財報、SOX/NQ、VWAP"
    else:
        short = radar.get("空方成本 / 回補", "")
        inst_text, margin_text = _inst_line(inst), _margin_line(margin)
        market_note = "台股模板｜使用三大法人、資券、借券、外資期貨、MOPS/FinMind、VWAP"
    return f"""
【1｜正式預測】
最近收盤模型參考：{final['t0']:.2f}｜T0參考｜事件波動盤｜{_ssot_vwap_state(price)}
下一交易日收盤預估：{final['t1']:.2f}
下一交易日路徑上緣：{final['high']:.2f}
下一交易日風險低點：{final['low']:.2f}
預測語意：{_session_words(price)['semantic'] if price.ticker.market=='TW' else _us_session_words(price)['semantic']}
市場模式：{_session_words(price)['mode'] if price.ticker.market=='TW' else _us_session_words(price)['mode']}｜信心 {confidence:.0f}%
市場分流：{market_note}
【2｜戰術雷達來源】
Fair Value：{radar.get('Fair Value')}
ABC：{radar.get('ABC 多空情境')}
BSI / Short：{radar.get('BSI 借券空方')}
FQC：{radar.get('FQC')}
市場風控：{radar.get('市場風控')}
事件/Macro：{radar.get('事件/Macro')}
外資期貨：{radar.get('外資期貨')}
基本面：{radar.get('基本面')}
泡沫雷達：{bubble_radar_line((price.context or {}).get('bubble_radar'))}
空方成本 / 回補：{short}
【3｜法人資券 / Short Pressure】
{inst_text}
{margin_text}
【4｜事件 / 新聞】
新聞採納：{news['accepted']}/{news['count']}｜情緒 {news['score']:+.2f}｜主事件：{news['top']}
宏觀：{_macro_line(macro, price, raw, news_items)}
TV外資壓力公式：深度/Trace保留，不進主雷達｜{_tv_pressure_line(tv) if price.ticker.market=='TW' else '美股不套用'}
【5｜ABC 情境】
A：突破 {raw.raw_no_chase:.2f} → 觀察軋空/回補觸發，分批利
B：回測 {final['low']:.2f} 不破 → 觀察承接
C：跌破 {final['low'] - price.atr14:.2f} → 防守出場
【6｜T+1 機率分布】A {raw.raw_abc['A']:.1f}%｜B {raw.raw_abc['B']:.1f}%｜C {raw.raw_abc['C']:.1f}%
【7｜T1/T2 物理路徑】T1 {final['t1']:.2f}｜High {final['high']:.2f}｜Low {final['low']:.2f}
【8｜事件/產業同步】市場 {price.ticker.market}｜VWAP {price.vwap:.2f}
【9｜新聞來源】{price.ticker.resolved_symbol}｜{news['count']}則
""".strip()
def _v12_core(price: PriceFrame, signals: List[SignalPacket], trace: PredictionTrace, news_items: List[NewsItem], confidence: float, direction: DirectionResult | None = None) -> Dict[str, object]:
    accepted = [s for s in signals if s.accepted]
    rejected = [s for s in signals if not s.accepted]
    news = _news_summary(news_items)
    inst_ok = _formal_ok(price.context.get("inst", {}))
    margin_ok = _formal_ok(price.context.get("margin", {}))
    if price.ticker.market == "US":
        macro_ok = bool((price.context.get("macro") or {}).get("accepted"))
        short_ok = (price.context.get("short") or {}).get("short_float") is not None
        health = max(20, min(95, confidence * 0.78 + (8 if macro_ok else -3) + (6 if short_ok else -2) + (4 if news["accepted"] else 0)))
        truth_summary = f"價格 {price.truth.source}｜SOX/NQ {'OK' if macro_ok else '觀察'}｜Short Float {'OK' if short_ok else '觀察'}｜新聞採納 {news['accepted']}/{news['count']}"
    else:
        health = max(20, min(95, confidence * 0.74 + (8 if inst_ok else -4) + (8 if margin_ok else -4) + (4 if news["accepted"] else 0)))
        truth_summary = f"價格 {price.truth.source}｜法人 {'OK' if inst_ok else '未顯示'}｜資券 {'OK' if margin_ok else '未顯示'}｜新聞採納 {news['accepted']}/{news['count']}"
    direction_text = "方向資料不足"
    if direction is not None:
        direction_text = (
            f"{_direction_label_zh(direction)}｜A {direction.p_up*100:.0f}% / "
            f"B {direction.p_neutral*100:.0f}% / C {direction.p_down*100:.0f}%"
            f"｜衝突 {direction.conflict*100:.0f}%"
        )
    return {
        "trace_summary": f"Raw T1 {trace.raw_t1:.2f} → Final {trace.final_t1:.2f}｜採納Signal {len(accepted)}｜拒絕/降權 {len(rejected)}",
        "truth_summary": truth_summary,
        "learning_summary": "方向命中與價格誤差分開 Audit｜累積樣本後才建議調權｜需 Tino Approve",
        "model_health": f"Model Health {health:.0f}%｜Confidence {confidence:.0f}%｜{direction_text}",
        "accepted": len(accepted), "rejected": len(rejected), "news": news,
        "direction": direction.to_dict() if direction is not None else {},
    }
def _is_us(price: PriceFrame) -> bool:
    return str(price.ticker.market).upper() == 'US'
def _us_money(v) -> str:
    try:
        x=float(v)
        if abs(x)>=1_000_000_000: return f"{x/1_000_000_000:.1f}B"
        if abs(x)>=1_000_000: return f"{x/1_000_000:.1f}M"
        if abs(x)>=1_000: return f"{x/1_000:.1f}K"
        return f"{x:.0f}"
    except Exception:
        return 'NA'
def _us_persona_line(price: PriceFrame) -> str:
    return str((price.context.get('persona') or {}).get('badge') or '美股產業定位觀察｜盤中用 VWAP 驗證')
def _us_bsi_line(price: PriceFrame) -> str:
    return 'BSI：美股無台股借券'
def _us_short_line(price: PriceFrame, raw: RawForecast) -> str:
    sh=price.context.get('short',{}) or {}
    sf=sh.get('short_float')
    sf_txt=f"Short Float：{float(sf):.2f}%" if sf is not None else 'Short Float：公開來源未同步'
    lo=sh.get('cost_low', price.low); hi=sh.get('cost_high', price.high+price.atr14); trig=sh.get('trigger', raw.raw_no_chase)
    return f"{float(lo):.2f}～{float(hi):.2f}｜回補 {float(trig):.2f}｜{sf_txt}"
def _us_inst_dashboard(price: PriceFrame) -> str:
    return "外資　NA\n投信　NA\n自營　NA\n來源：US"
def _us_margin_dashboard(price: PriceFrame) -> str:
    sh=price.context.get('short',{}) or {}
    shares=sh.get('shares_short')
    days=sh.get('short_ratio')
    if shares:
        return f"空單：{_us_money(shares)}股｜補空天數：{days if days is not None else 'NA'}天"
    return "空單：公開來源未同步｜補空天數：NA"


def _news_tag(item: NewsItem) -> str:
    return str(getattr(item, 'tag', '') or '')


def _news_title(item: NewsItem) -> str:
    return str(getattr(item, 'title', '') or '').strip()


def _us_news_filter(news_items: List[NewsItem] | None, kinds: tuple[str, ...]) -> List[NewsItem]:
    out: List[NewsItem] = []
    for n in (news_items or []):
        tag = _news_tag(n)
        title = _news_title(n)
        if not title:
            continue
        if any(tag.startswith(k) or (k in tag) for k in kinds):
            out.append(n)
    return out


def _us_news_top_text(items: List[NewsItem], limit: int = 2) -> str:
    if not items:
        return ''
    titles = []
    ordered = sorted(items, key=lambda n: abs(float(getattr(n, 'score', 0) or 0)), reverse=True)
    for n in ordered[:limit]:
        t = _short_title(_news_title(n), 30)
        if t:
            titles.append(t)
    return '；'.join(titles)


def _us_news_strength(items: List[NewsItem]) -> tuple[str, float, int, int]:
    if not items:
        return '低', 0.0, 0, 0
    score = sum(float(getattr(n, 'score', 0) or 0) for n in items)
    pos = sum(1 for n in items if float(getattr(n, 'score', 0) or 0) >= 0.06)
    neg = sum(1 for n in items if float(getattr(n, 'score', 0) or 0) <= -0.06)
    abs_score = abs(score)
    if abs_score >= 0.24 or max(pos, neg) >= 3:
        level = '高'
    elif abs_score >= 0.10 or max(pos, neg) >= 1:
        level = '中'
    else:
        level = '低'
    return level, score, pos, neg


def _us_news_themes(items: List[NewsItem]) -> str:
    text = ' '.join((_news_title(n) + ' ' + _news_tag(n)).lower() for n in (items or []))
    themes = []
    if any(k in text for k in ['earnings', 'revenue', 'guidance', 'q1', 'q2', 'q3', 'q4']):
        themes.append('財報/guidance')
    if any(k in text for k in ['hbm', 'dram', 'nand', 'memory']):
        themes.append('HBM/記憶體')
    if any(k in text for k in ['ai', 'blackwell', 'rubin', 'gpu', 'data center', 'custom silicon', 'asic']):
        themes.append('AI/資料中心')
    if any(k in text for k in ['tariff', 'export control', 'china', 'trump', 'sanction', 'taiwan strait']):
        themes.append('政策/地緣')
    if any(k in text for k in ['upgrade', 'downgrade', 'price target', 'rating']):
        themes.append('評級/目標價')
    return '、'.join(themes[:3]) if themes else '事件觀察'


def _us_macro_core_line(price: PriceFrame, news_items: List[NewsItem] | None = None) -> str:
    """US Macro Core: official calendar plus post-release result/reaction verdict."""
    m = price.context.get('macro', {}) or {}
    sox = m.get('sox')
    nq = m.get('nq') if m.get('nq') is not None else m.get('qqq')
    vix = m.get('vix')
    parts = [
        str(m.get('calendar') or _macro_calendar_guard_text()),
        'CPI/PPI/PCE/ISM：依官方日曆校準',
        f"SOX {sox if sox is not None else 'NA'}%",
        f"NQ/QQQ {nq if nq is not None else 'NA'}%",
    ]
    if vix is not None:
        parts.append(f"VIX {vix}")
    result_line = compact_macro_event_line(m, news_items)
    if result_line:
        return result_line.replace('Macro Event｜', 'Macro Core｜', 1) + '｜' + '｜'.join(parts)
    return 'Macro Core｜' + '｜'.join(parts)


def _us_daily_headline_line(price: PriceFrame, news_items: List[NewsItem] | None = None) -> str:
    items = _us_news_filter(news_items, ('daily_headline',))
    level, score, pos, neg = _us_news_strength(items)
    themes = _us_news_themes(items)
    top = _us_news_top_text(items, 2)
    if top:
        direction = '風險偏高' if neg > pos else ('風險支撐' if pos > neg else '市場天氣觀察')
        return f"Daily Headline｜{level}｜2026近端頭條 {len(items)}則｜{themes}｜{direction}｜{top}"
    return f"Daily Headline｜低｜近端未命中重大外部頭條｜全市場風險觀察"


def _us_policy_geo_line(price: PriceFrame, news_items: List[NewsItem] | None = None) -> str:
    assess = _quantum_macro_policy_assessment(news_items, price.context.get('macro', {}) if price else {}, getattr(getattr(price, 'ticker', None), 'market', ''))
    line = str(assess.get('line') or 'Policy/Geo｜觀察')
    line = line.replace('事件觀察，不硬改價', '事件觀察，等待確認')
    persona = _us_persona_line(price) if price else ''
    # Sector mapping: only describe impact, do not hard-change price here.
    if any(k in persona for k in ['半導體', '記憶體', 'AI供應鏈']):
        suffix = '｜半導體/AI供應鏈影響權重高'
    elif any(k in persona for k in ['國防', '無人機']):
        suffix = '｜國防/無人機政策敏感度高'
    else:
        suffix = '｜產業影響權重觀察'
    return line + suffix


def _us_company_news_line(price: PriceFrame, news_items: List[NewsItem] | None = None) -> str:
    company = _us_news_filter(news_items, ('us_company', 'bullish_us_company', 'bearish_us_company'))
    industry = _us_news_filter(news_items, ('us_industry', 'bullish_us_industry', 'bearish_us_industry'))
    use = company + [x for x in industry if x not in company]
    top = _us_news_top_text(use, 2)
    if top:
        level, score, pos, neg = _us_news_strength(use)
        themes = _us_news_themes(use)
        tone = '偏多事件' if pos > neg else ('偏空/風險事件' if neg > pos else '事件觀察')
        return f"Company News｜{price.ticker.resolved_symbol}｜{level}｜英文新聞 {len(use)}則｜{themes}｜{tone}｜{top}"
    return f"Company News｜{price.ticker.resolved_symbol}｜英文新聞查詢中｜先看 Macro Core / VWAP / 財報"
def _us_fundamental_line(price: PriceFrame, news_items: List[NewsItem] | None = None) -> str:
    if str(getattr(price.ticker, 'asset_type', 'stock') or 'stock').lower() == 'etf':
        return "ETF Mode｜不套單一公司 EPS / PE｜泡沫雷達改看價格熱度、事件預期與市場風險"
    f=price.context.get('fundamental',{}) or {}
    if f.get('accepted'):
        eps=f.get('eps'); rev=f.get('revenue'); qoq=f.get('qoq'); yoy=f.get('revenue_yoy', f.get('yoy')); pe=f.get('pe')
        eps_yoy=f.get('eps_yoy')
        q=f.get('quarter') or '最新財報'
        if isinstance(q, (int, float)) or str(q).isdigit():
            q = '最新財報'
        nxt=f.get('next_earnings') or ''
        if isinstance(nxt, (int, float)) or str(nxt).isdigit():
            try:
                from datetime import datetime as _dt
                nxt = _dt.fromtimestamp(int(nxt)).date().isoformat()
            except Exception:
                nxt = ''
        days=f.get('earnings_days')
        parts=["月營收：美股不適用", "財報/營收", str(q)]
        if rev is not None:
            rev_label = "營收(季)" if f.get('revenue_kind') == 'quarterly' else "營收(TTM)"
            parts.append(f"{rev_label} {_us_money(rev)}")
        if qoq is not None and f.get('qoq_verified'):
            parts.append(f"營收QoQ {float(qoq):+.2f}%")
        if yoy is not None and f.get('yoy_verified', True):
            parts.append(f"營收YoY {float(yoy):+.2f}%")
        if eps is not None:
            basis = str(f.get('eps_basis') or '')
            if basis == 'normalized_diluted':
                eps_label = "可比EPS(季)"
            elif f.get('eps_kind') == 'quarterly':
                eps_label = "GAAP EPS(季)"
            else:
                eps_label = "GAAP EPS(TTM)"
            parts.append(f"{eps_label} {float(eps):.2f}")
            gaap_eps = f.get('gaap_eps')
            if basis == 'normalized_diluted' and gaap_eps is not None and abs(float(gaap_eps) - float(eps)) > 0.005:
                parts.append(f"GAAP EPS(季) {float(gaap_eps):.2f}")
        if eps_yoy is not None and f.get('eps_yoy_verified', True):
            eps_yoy_label = str(f.get('eps_yoy_label') or 'GAAP EPS YoY')
            suffix = "（僅揭露/不計泡沫Decision）" if not f.get('eps_yoy_decision_eligible', False) else ""
            parts.append(f"{eps_yoy_label} {float(eps_yoy):+.2f}%{suffix}")
        if pe is not None: parts.append(f"PE {float(pe):.2f}")
        if f.get('forward_pe') is not None: parts.append(f"Forward PE {float(f.get('forward_pe')):.2f}")
        if f.get('ps') is not None: parts.append(f"PS {float(f.get('ps')):.2f}")
        if nxt: parts.append(f"下次財報 {nxt}")
        if days is not None: parts.append(f"財報倒數 {days}天")
        if not f.get('qoq_verified'):
            parts.append("QoQ未取得正式季度序列，不計分")
        source = str(f.get('source') or 'YahooFinance')
        parts.append(f"財報語意｜AI / 記憶體 / 供應鏈敘事｜來源 {source}")
        return "｜".join([x for x in parts if x not in ('', None)])
    news=_news_summary(news_items or [])
    return f"月營收：美股不適用｜財報/營收｜Yahoo/Finviz 財報欄位回補中｜新聞事件 {news.get('count',0)}則"

def _us_macro_line(price: PriceFrame, news_items: List[NewsItem] | None = None) -> str:
    # Macro Core remains visible even if company-news fetch is empty.
    return _us_macro_core_line(price, news_items)

def _us_extended_session_line(price: PriceFrame) -> str:
    uss = (price.context or {}).get('us_session', {}) if isinstance((price.context or {}), dict) else {}
    meta = (price.context or {}).get('price_meta', {}) if isinstance((price.context or {}), dict) else {}
    status = str(getattr(price, 'market_status', '') or meta.get('session') or '')
    label = str(uss.get('label') or meta.get('session_label') or {'pre_market':'盤前','intraday':'盤中','after_hours':'盤後','closed_reference':'休市'}.get(status, '休市'))
    if status not in {'pre_market','after_hours','intraday'}:
        return f"{label}｜最近正式收盤參考"
    try:
        chg = float(uss.get('change')) if uss.get('change') is not None else float(price.last) - float(price.previous_close)
        chgp = float(uss.get('change_pct')) if uss.get('change_pct') is not None else chg / float(price.previous_close or price.last) * 100
        vol = uss.get('volume')
        vol_txt = f"｜量 {int(vol):,}" if vol not in (None, '', 'NA') else ''
        src = str(uss.get('source') or meta.get('source') or 'YahooFinance')
        src_txt = '即時快照' if 'PrePost' in src or '1m' in src else '參考快照'
        return f"{label}｜{src_txt}｜Gap {chg:+.2f}/{chgp:+.2f}%{vol_txt}"
    except Exception:
        return f"{label}｜盤前/盤後快照待同步"

def _us_market_line(price: PriceFrame, raw: RawForecast, sm: Dict[str, SignalPacket]) -> str:
    pos=(price.last-price.low)/max(price.high-price.low,0.01)*100
    vtxt=_ssot_vwap_state(price)
    sox=(price.context.get('macro') or {}).get('sox')
    nq=(price.context.get('macro') or {}).get('nq') or (price.context.get('macro') or {}).get('qqq')
    r='估值重定價' if price.last<price.vwap else '風險可控'
    mode=_us_session_words(price)['mode']
    session_line=_us_extended_session_line(price)
    return f"{mode}｜{session_line}｜{r}｜Risk {25 if price.last>=price.vwap else 34}｜SOX {sox if sox is not None else 'NA'}%｜NQ/QQQ {nq if nq is not None else 'NA'}%｜日內位置 {pos:.0f}%｜{vtxt}｜{trend_radar_line(price)}"

def _market_heat_line_for_price(price: PriceFrame) -> str:
    try:
        ctx = price.context or {}
        heat = ctx.get("market_heat") if isinstance(ctx.get("market_heat"), dict) else None
        if not heat:
            heat = fetch_tw_market_heat(str(getattr(price, "price_date", "") or ""))
        return market_heat_radar_line(heat)
    except Exception:
        return "市場熱度｜融資餘額待同步｜先看法人/VWAP/資券"

def _quantum_contribution_line(direction: DirectionResult | None) -> str:
    if direction is None:
        return "方向因子碰撞觀察｜等待方向閘門"

    factors = getattr(direction, "factor_contributions", {}) or {}
    ranked = sorted(
        ((str(name), float(value)) for name, value in factors.items() if abs(float(value)) >= 0.05),
        key=lambda item: abs(item[1]),
        reverse=True,
    )

    # Keep the row compact while preserving the exact arithmetic contract.
    shown = ranked[:6]
    shown_names = {name for name, _ in shown}
    hidden_sum = sum(value for name, value in ranked if name not in shown_names)
    parts = [f"{name} {value:+.1f}" for name, value in shown]
    if abs(hidden_sum) >= 0.05:
        parts.append(f"其他 {hidden_sum:+.1f}")
    if not parts:
        parts.append("有效方向因子互相抵銷")

    direction_total = sum(float(value) for value in factors.values())
    # The engine enforces this invariant; use the factor total for display so
    # no hidden score can appear between the components and the final gate.
    score = float(getattr(direction, "score", direction_total) or 0.0)
    if abs(direction_total - score) > 0.08:
        # Safe visible correction for legacy objects restored from session state.
        parts.append(f"其他 {score - direction_total:+.1f}")

    risk_map = getattr(direction, "risk_contributions", {}) or {}
    confidence_map = getattr(direction, "confidence_adjustments", {}) or {}
    risk_rows = sorted(
        ((str(name), float(value)) for name, value in risk_map.items() if float(value) > 0.05),
        key=lambda item: item[1],
        reverse=True,
    )
    for name, value in risk_rows[:2]:
        parts.append(f"風險 {name} +{value:.1f}R")
    confidence_cut = sum(float(value) for value in confidence_map.values())
    if confidence_cut < -0.05:
        parts.append(f"信心 {confidence_cut:.1f}")

    gate = str(getattr(direction, "gate_state", "") or "B回測")
    return "｜".join(parts) + f"｜方向總分 {score:+.1f} → {gate}"


def _tw_radar(price: PriceFrame, raw: RawForecast, signals: List[SignalPacket], confidence: float, news_items: List[NewsItem], direction: DirectionResult | None = None) -> Dict[str, str]:
    sm=_signal_map(signals); inst=price.context.get('inst',{}); margin=price.context.get('margin',{}); bsi=price.context.get('bsi',{}); macro=price.context.get('macro',{}); futures=price.context.get('futures',{}); fundamental=price.context.get('fundamental',{}); proxy={}
    etf_note = 'ETF Mode｜不套 EPS / 個股財報 / 個股 BSI；只看 price、VWAP、volume、NAV/溢折價、成分股、市場風險' if price.ticker.asset_type == 'etf' else ''
    fqc=sm.get('FQC')
    abc=f"A突破 {raw.raw_abc['A']:.0f}%｜上緣 {raw.raw_t1_high:.2f}　B回測 {raw.raw_abc['B']:.0f}%｜風險低點 {raw.raw_t1_low:.2f}　C防守 {raw.raw_abc['C']:.0f}%"
    market_line=f"市場｜{_price_regime_line(price,raw)}｜{trend_radar_line(price)}"
    rows={
      'Fair Value':f"保守 {price.last-price.atr14:.2f}｜中性 {price.last:.2f}｜樂觀 {price.last+price.atr14:.2f}",
      'ABC 多空情境':abc,
      'BSI 借券空方': etf_note or _bsi_line(bsi, proxy),
      'FQC':f"技術面｜{'尚未止穩' if price.last < price.vwap else '買盤延續'}｜{_ssot_vwap_state(price)}",
      '市場風控':market_line,
      '事件/Macro':_macro_line(macro, price, raw, news_items),
      'Quantum 貢獻':_quantum_contribution_line(direction),
      '外資期貨':_futures_line(futures, price),
      '市場熱度':_market_heat_line_for_price(price),
      '基本面': (etf_note or _fundamental_line(fundamental, '', price, news_items)) + "\n" + bubble_radar_line((price.context or {}).get('bubble_radar')),
      '空方成本 / 回補':f"{price.low:.2f}～{price.high+price.atr14:.2f}｜回補 {raw.raw_no_chase:.2f}｜{_bsi_line(bsi, proxy)}",
      '三大法人':_inst_radar_line(inst, None),
      '資券 / 融資融券': etf_note or _margin_radar_line(margin, None),
      '左側籌碼摘要':_chip_summary(inst, margin, bsi),
      '資料源':truth_to_main_label(price.truth).replace('fallback','price memory').replace('Fallback','price memory'), 'Confidence':f"{confidence:.0f}%"
    }
    return {k:_main_clean(v) for k,v in rows.items()}
def _us_radar(price: PriceFrame, raw: RawForecast, signals: List[SignalPacket], confidence: float, news_items: List[NewsItem], direction: DirectionResult | None = None) -> Dict[str, str]:
    sm=_signal_map(signals); fqc=sm.get('FQC')
    abc=f"A突破 {raw.raw_abc['A']:.0f}%｜上緣 {raw.raw_t1_high:.2f}　B回測 {raw.raw_abc['B']:.0f}%｜風險低點 {raw.raw_t1_low:.2f}　C防守 {raw.raw_abc['C']:.0f}%"
    rows={
      'Fair Value':f"保守 {price.last-price.atr14:.2f}｜中性 {price.last:.2f}｜樂觀 {price.last+price.atr14:.2f}",
      'ABC 多空情境':abc,
      'BSI 借券空方':_us_bsi_line(price),
      'FQC':f"{fqc.signal if fqc else 'FQC觀察'}｜強度 {abs(price.last-price.vwap)/max(price.atr14,0.01)*10:.1f}%｜上緣 {raw.raw_t1_high:.2f}｜下緣 {raw.raw_t1_low:.2f}｜{_ssot_vwap_state(price)}",
      '市場風控':_us_market_line(price, raw, sm),
      '事件/Macro':_us_macro_line(price, news_items),
      'Quantum 貢獻':_quantum_contribution_line(direction),
      'Daily Headline':_us_daily_headline_line(price, news_items),
      'Policy/Geo':_us_policy_geo_line(price, news_items),
      'Company News':_us_company_news_line(price, news_items),
      '外資期貨':'外資V2｜台股專用｜美股不套用',
      '基本面':_us_fundamental_line(price, news_items) + "\n" + bubble_radar_line((price.context or {}).get('bubble_radar')),
      '空方成本 / 回補':_us_short_line(price, raw),
      '三大法人':_us_inst_dashboard(price),
      '資券 / 融資融券':_us_margin_dashboard(price),
      '左側籌碼摘要':f"Short/FQC：{_us_persona_line(price)}｜{_us_short_line(price, raw).split('｜')[-1]}｜美股不套用台股 BSI，以 VWAP / FQC / 量價確認。",
      'US Persona':_us_persona_line(price), '資料源':'資料源：已驗證', 'Confidence':f"{confidence:.0f}%"
    }
    return {k:_main_clean(v) for k,v in rows.items()}
def _radar(price: PriceFrame, raw: RawForecast, signals: List[SignalPacket], confidence: float, news_items: List[NewsItem], direction: DirectionResult | None = None) -> Dict[str, str]:
    if _is_us(price):
        return _us_radar(price, raw, signals, confidence, news_items, direction)
    return _tw_radar(price, raw, signals, confidence, news_items, direction)
def _apply_v9_path_guard(price: PriceFrame, raw: RawForecast, final_t1: float, final_high: float | None = None, final_low: float | None = None):
    if price.ticker.market != 'TW':
        return final_t1, final_high, final_low
    last=float(price.last); atr=max(float(price.atr14), last*0.012, 0.01)
    close_band=min(max(atr*1.25, last*0.018), last*0.045)
    guarded=max(last-close_band, min(last+close_band, float(final_t1)))
    high=final_high if final_high is not None else raw.raw_t1_high
    low=final_low if final_low is not None else raw.raw_t1_low
    high=max(guarded, min(float(high), last + min(max(atr*2.0, last*0.028), last*0.08)))
    low=min(guarded, max(float(low), last - min(max(atr*2.0, last*0.028), last*0.08)))
    return round(guarded,2), round(high,2), round(low,2)
def orchestrate(price: PriceFrame, manual_macro: str = "neutral", news_items: Optional[List[NewsItem]] = None, extra_signals: Optional[List[SignalPacket]] = None) -> FinalForecast:
    ok, reason = validate_price_frame(price)
    if not ok:
        return _stop_forecast(price, reason)
    news_items = news_items or []
    raw = build_raw_forecast(price)
    signals = collect_signals(price, manual_macro)
    ns = _news_summary(news_items)
    if news_items:
        signals.append(SignalPacket("News", f"新聞採納 {ns['accepted']}/{ns['count']}｜情緒 {ns['score']:+.2f}", ns["score"] * 10, 0.0, 2.0, ns["bias"], f"採納 {ns['accepted']}｜忽略 {ns['ignored']}｜{ns['top']}", "GoogleNewsTW", price.price_date, True))
    qmacro = _quantum_macro_policy_assessment(news_items, (price.context or {}).get("macro", {}) if isinstance((price.context or {}), dict) else {}, price.ticker.market)
    if qmacro.get("level") not in ("待同步", "觀察") or qmacro.get("score") or qmacro.get("risk"):
        signals.append(SignalPacket(
            "Quantum Macro",
            str(qmacro.get("line") or "Policy/Geo觀察"),
            float(qmacro.get("score") or 0.0),
            float(qmacro.get("confidence") or 0.0),
            float(qmacro.get("risk") or 0.0),
            float(qmacro.get("bias") or 0.0),
            str(qmacro.get("reason") or "macro policy geo assessment"),
            "GoogleNewsTW+MacroCalendar",
            price.price_date,
            True,
        ))
    if extra_signals:
        signals.extend(extra_signals)

    # V12.2 SSOT: the same market-heat snapshot shown on the right radar must
    # also be available to the direction engine.  Fetch once per request and
    # keep failures non-blocking.
    if price.ticker.market == "TW" and isinstance(price.context, dict):
        if not isinstance(price.context.get("market_heat"), dict):
            if os.environ.get("TINO_OFFLINE_TEST") == "1":
                price.context["market_heat"] = {"accepted": False, "source": "OFFLINE_TEST"}
            else:
                try:
                    price.context["market_heat"] = fetch_tw_market_heat(str(price.price_date or ""))
                except Exception:
                    price.context["market_heat"] = {"accepted": False, "source": "MARKET_HEAT_FETCH_FAILED"}

    # Cross-market bubble radar is a bounded position-risk overlay.  It reads
    # existing facts only and never changes Direction/Quantum/Forecast inputs.
    try:
        bubble = assess_bubble_risk(price, news_items)
    except Exception as exc:
        bubble = {
            "accepted": False, "score": 0.0, "temperature": 0,
            "level": "資料不足", "decision_adjustment": 0.0,
            "line": "AI泡沫雷達｜資料不足，不做泡沫結論",
            "reason": f"{type(exc).__name__}",
        }
    if isinstance(price.context, dict):
        price.context["bubble_radar"] = bubble

    # V12.2 adaptive dual engine: direction is estimated independently from
    # price and receives timestamped event/news evidence for decay control.
    direction = build_direction_forecast(price, signals, news_items)
    raw = replace(raw, raw_abc=direction.abc())

    raw_adjustments = [signal_price_adjustment(signal, price) for signal in signals]
    total_adjustment, _ = cap_total_adjustment(raw_adjustments, price)
    base_t1 = apply_market_bounds(raw.raw_t1 + total_adjustment, price.last, price.ticker.market, price.ticker.price_limit_pct)
    direction_t1, _, ensemble_weight = _direction_ensemble(price, base_t1, direction)
    direction_t1 = apply_market_bounds(direction_t1, price.last, price.ticker.market, price.ticker.price_limit_pct)
    direction_delta = round(direction_t1 - base_t1, 4)
    final_t1 = _high_magnet_guard(direction_t1, raw.raw_t1_high, raw.raw_t1_low)
    final_t1 = apply_market_bounds(final_t1, price.last, price.ticker.market, price.ticker.price_limit_pct)

    trace_signals = list(signals)
    trace_adjustments = list(raw_adjustments)
    trace_signals.append(SignalPacket(
        "Direction Ensemble",
        f"{_direction_label_zh(direction)}｜A {direction.p_up*100:.1f}% / B {direction.p_neutral*100:.1f}% / C {direction.p_down*100:.1f}%",
        direction.score,
        0.0,
        0.0,
        0.0,
        f"regime={direction.regime}｜conflict={direction.conflict:.3f}｜quality={direction.quality:.3f}｜blend={ensemble_weight:.3f}｜families={direction.family_scores}",
        "direction_engine",
        price.truth.date,
        True,
    ))
    trace_adjustments.append(direction_delta)

    # Reconcile market bounds/high-magnet rounding into one explicit guard step.
    reconstructed = raw.raw_t1 + sum(trace_adjustments)
    if abs(final_t1 - reconstructed) > 0.0001:
        trace_signals.append(SignalPacket("High Magnet Guard", "T1 High Magnet Guard", 0, 0, 0, 0, "T1 不可永遠貼近 High｜含市場價格邊界", "orchestrator", price.truth.date, True))
        trace_adjustments.append(round(final_t1 - reconstructed, 4))

    steps: List[TraceStep] = []
    for signal, adjustment in zip(trace_signals, trace_adjustments):
        steps.append(trace_step_from_signal(signal.module, signal, 0.0 if is_price_neutral_module(signal.module) else adjustment))
    steps = ensure_required_trace_rows(steps, price.truth.date)

    score = float(direction.score)
    tactical_confidence = _tactical_confidence(signals)
    confidence = clamp(direction.confidence * 0.78 + tactical_confidence * 0.22, MIN_CONFIDENCE, MAX_CONFIDENCE)
    price_meta = (price.context or {}).get("price_meta", {}) if isinstance((price.context or {}).get("price_meta", {}), dict) else {}
    if bool(price_meta.get("limited_price_mode")):
        confidence = clamp(confidence - 8.0, MIN_CONFIDENCE, MAX_CONFIDENCE)

    final_high = apply_market_bounds(max(final_t1, raw.raw_t1_high), price.last, price.ticker.market, price.ticker.price_limit_pct)
    final_low = apply_market_bounds(min(final_t1, raw.raw_t1_low), price.last, price.ticker.market, price.ticker.price_limit_pct)
    final_t1, final_high, final_low = _apply_v9_path_guard(price, raw, final_t1, final_high, final_low)
    recon = raw.raw_t1 + sum(step.adjustment for step in steps)
    if abs(recon - final_t1) > 0.0001:
        steps.append(TraceStep('V9 Path Guard', 'TW/US market route price guard', round(final_t1 - recon, 4), 0.0, True, 'V9 前台路徑守門；避免台股權值被高Beta/美股風險打成假崩跌', 'orchestrator', price.truth.date))

    final_t0 = apply_market_bounds(raw.raw_t0 + (final_t1 - raw.raw_t1) * 0.20, price.previous_close, price.ticker.market, price.ticker.price_limit_pct)
    decision = _decision_card(price, raw, score, final_t1, final_low, direction, bubble)
    decision["_direction_ensemble_weight"] = ensemble_weight
    radar = _radar(price, raw, signals, confidence, news_items, direction)
    trace = PredictionTrace(price.ticker.resolved_symbol, raw.raw_t1, steps, final_t1)
    decision["v12_core"] = _v12_core(price, signals, trace, news_items, confidence, direction)
    final_values = {"t0": final_t0, "t1": final_t1, "high": final_high, "low": final_low}
    deep = _deep_report(price, raw, final_values, decision, radar, signals, confidence, news_items)
    tags = [_streak_label(price), _ssot_vwap_state(price), "高檔別追" if price.last >= raw.raw_no_chase else "低接優先"]
    return FinalForecast(price.ticker, False, "", raw, final_t0, final_t1, final_high, final_low, confidence, raw.raw_no_chase, raw.raw_low_entry, decision, tags, str(decision["一句話"]), _session_words(price)["anchor"] if price.ticker.market == "TW" else _us_session_words(price)["anchor"], radar, trace, [price.truth], deep, news_items, signals)
