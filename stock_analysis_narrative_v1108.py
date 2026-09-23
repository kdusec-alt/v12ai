# -*- coding: utf-8 -*-
"""Per-ticker, evidence-led analysis display built from existing V12 outputs.

This is presentation-only: it does not recompute signals, forecast prices, or
change the formal decision.  The company-event column is deliberately strict:
only ticker-specific evidence with a verifiable date inside three recent
weekday trading sessions is presented as a fresh company catalyst.
"""
from __future__ import annotations

from datetime import date, datetime
import hashlib
from pathlib import Path
import re
import threading
from typing import Any, Mapping

try:
    from memory_store import MEMORY_DIR, read_json, write_json
    COMPANY_EVENT_CACHE_PATH = Path(MEMORY_DIR) / "stock_company_events_v1108.json"
except Exception:
    COMPANY_EVENT_CACHE_PATH = Path(".tino_memory/stock_company_events_v1108.json")
    read_json = None
    write_json = None


_PRICE_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_EVENT_CACHE_LOCK = threading.RLock()
_EVENT_CACHE_SCHEMA = "TINO_STOCK_COMPANY_EVENT_CACHE_V1108"


def _map(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _num(value: Any) -> float | None:
    try:
        result = float(str(value).replace(",", "").replace("%", "").strip())
        return result if result == result and abs(result) != float("inf") else None
    except Exception:
        return None


def _price(value: Any) -> str:
    number = _num(value)
    if number is None:
        return "資料待確認"
    if abs(number) >= 1000:
        return f"{number:,.0f}"
    if abs(number) >= 100:
        return f"{number:,.2f}".rstrip("0").rstrip(".")
    return f"{number:.2f}"


def _date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    # News feeds use ISO timestamps, date-only strings, or a date embedded in
    # a localized timestamp.  Do not treat relative labels such as "latest" as
    # proof of freshness.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except Exception:
        match = re.search(r"(?<!\d)(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)", text)
        if not match:
            return None
        try:
            return date(*(int(part) for part in match.groups()))
        except Exception:
            return None


def _weekday_age(then: date, now: date) -> int | None:
    if then > now:
        return None
    # Three recent trading sessions means the event date plus the next two
    # weekday session boundaries.  Exchange holidays cannot be inferred from
    # this display layer, so exact event freshness remains date-grounded.
    return sum(1 for ordinal in range(then.toordinal() + 1, now.toordinal() + 1)
               if date.fromordinal(ordinal).weekday() < 5)


def _company_event(item: Any, symbol: str, name: str) -> bool:
    title = str(getattr(item, "title", "") or "").lower()
    tag = str(getattr(item, "tag", "") or "").lower()
    code = symbol.split(".", 1)[0].lower()
    routed = any(f"{prefix}_company_{code}" in tag for prefix in ("tw", "us"))
    clean_name = re.sub(r"\b(incorporated|inc|corporation|corp|company|co|ltd|limited|holdings|plc|group)\b", " ", name.lower())
    aliases = [x for x in (code, clean_name.strip()) if x and len(x) >= 3]
    named = any((alias in title if re.search(r"[\u4e00-\u9fff]", alias) else
                 bool(re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", title)))
                for alias in aliases)
    company_scoped = "company" in tag or "catalyst" in tag
    return bool(title and company_scoped and (routed or named))


def _fresh_company_events(forecast: Any, reference: date) -> list[dict[str, Any]]:
    ticker = getattr(forecast, "ticker", None)
    symbol = str(getattr(ticker, "resolved_symbol", "") or "")
    name = str(getattr(ticker, "name", "") or "")
    output: dict[str, dict[str, Any]] = {}
    for item in list(getattr(forecast, "news_items", []) or []):
        if not _company_event(item, symbol, name):
            continue
        published = _date(getattr(item, "time", ""))
        age = _weekday_age(published, reference) if published else None
        if age is None or age > 2:
            continue
        row = {
            "ticker": symbol, "name": name,
            "title": str(getattr(item, "title", "") or "").strip(),
            "source": str(getattr(item, "source", "") or "").strip(),
            "time": str(getattr(item, "time", "") or "").strip(),
            "tag": str(getattr(item, "tag", "") or "").strip(),
            "score": _num(getattr(item, "score", 0)) or 0.0,
            "link": str(getattr(item, "link", "") or "").strip(),
        }
        fingerprint = hashlib.sha256(
            f"{symbol}|{published.isoformat()}|{row['title']}".encode("utf-8")
        ).hexdigest()[:24]
        row["fingerprint"] = fingerprint
        output[fingerprint] = row

    # Keep a compact, replace-in-place memory so an analysis can still refer
    # to verified company events when its current news fetch is empty.
    with _EVENT_CACHE_LOCK:
        cached: dict[str, Any] = {}
        if callable(read_json):
            try:
                stored = read_json(COMPANY_EVENT_CACHE_PATH, {})
                if stored.get("schema") == _EVENT_CACHE_SCHEMA:
                    cached = {
                        str(row.get("fingerprint")): dict(row)
                        for row in stored.get("events", [])
                        if isinstance(row, Mapping) and row.get("fingerprint")
                    }
            except Exception:
                cached = {}
        previous = dict(cached)
        cached.update(output)
        live: dict[str, dict[str, Any]] = {}
        for fingerprint, row in cached.items():
            # Preserve other tickers' still-relevant rows only after their own
            # date is checked against the current session.
            event_date = _date(row.get("time"))
            age = _weekday_age(event_date, reference) if event_date else None
            if age is not None and age <= 2:
                live[fingerprint] = row
        if callable(write_json) and live != previous:
            try:
                write_json(COMPANY_EVENT_CACHE_PATH, {
                    "schema": _EVENT_CACHE_SCHEMA,
                    "updated_at": reference.isoformat(),
                    "events": list(live.values())[-300:],
                })
            except Exception:
                # Persistence is helpful continuity; it must never block stock
                # analysis if the host filesystem or optional remote mirror is
                # unavailable.
                pass
        return sorted(
            [row for row in live.values() if str(row.get("ticker") or "").upper() == symbol.upper()],
            key=lambda row: (_date(row.get("time")) or date.min, abs(_num(row.get("score")) or 0)),
            reverse=True,
        )


def _industry(forecast: Any) -> str:
    price = getattr(forecast, "price_frame", None)
    context = _map(getattr(price, "context", {}))
    fundamental = _map(context.get("fundamental"))
    for value in (context.get("industry"), context.get("sector"), context.get("company_industry"),
                  fundamental.get("industry"), fundamental.get("sector")):
        if str(value or "").strip():
            return str(value).strip()
    persona = _map(context.get("persona"))
    if persona.get("label") and "觀察" not in str(persona["label"]):
        return str(persona["label"])
    profile = ""
    if price is not None:
        try:
            from morning_brief_v1107 import _industry as shared_industry
            shared = shared_industry(price)
            if shared and shared != "產業資料未同步":
                return shared
        except Exception:
            pass
    try:
        from quantum_entanglement import sector_profile
        profile = sector_profile(price) if price else ""
    except Exception:
        pass
    labels = {
        "memory": "記憶體／半導體", "semiconductor": "半導體／電子",
        "financial": "金融", "biotech": "生技／醫療",
        "defense": "國防／航太", "airline": "航空", "shipping": "航運",
        "energy": "能源", "broad": "產業分類待確認",
    }
    return labels.get(profile, profile.replace("_", "／") or "產業分類待確認")


def _fair_low(forecast: Any) -> float | None:
    text = str((_map(getattr(forecast, "radar", {}))).get("Fair Value") or "")
    match = re.search(r"(?:下緣情境|保守|下緣)[^\d-]*(-?\d[\d,]*(?:\.\d+)?)", text)
    return _num(match.group(1)) if match else None


def _daily_lower_bound(forecast: Any) -> float | None:
    ticker = getattr(forecast, "ticker", None)
    if str(getattr(ticker, "market", "")).upper() != "TW":
        return None
    price = getattr(forecast, "price_frame", None)
    context = _map(getattr(price, "context", {}))
    rule = _map(context.get("exchange_rule"))
    decision = _map(getattr(forecast, "decision_card", {}))
    rule = (_map(decision.get("_exchange_rule"))
            or _map(_map(decision.get("_price_meta")).get("exchange_rule"))
            or rule)
    lower = _num(rule.get("daily_lower"))
    if lower is not None:
        return lower
    if rule.get("fixed_daily_limit") is False:
        return None
    reference = _num(rule.get("reference_price")) or _num(getattr(price, "previous_close", None))
    limit_pct = _num(rule.get("price_limit_pct")) or _num(getattr(ticker, "price_limit_pct", None))
    if limit_pct is None:
        try:
            from exchange_rule_engine_v1069 import static_price_limit_pct
            limit_pct = static_price_limit_pct(ticker)
        except Exception:
            limit_pct = None
    if reference is None or limit_pct is None or limit_pct <= 0:
        return None
    try:
        from exchange_rule_engine_v1069 import classify_static_rule, tw_daily_price_bounds
        family = str(rule.get("product_family") or classify_static_rule(
            market=ticker.market, exchange=ticker.exchange, asset_type=ticker.asset_type,
            symbol=ticker.resolved_symbol, name=ticker.name,
        ).get("product_family") or "COMMON_STOCK")
        return tw_daily_price_bounds(reference, limit_pct,
                                     family)[0]
    except Exception:
        return reference * (1.0 - limit_pct)


def build_stock_analysis(forecast: Any, decision_brief: Mapping[str, Any],
                         reference_date: date | None = None) -> dict[str, str]:
    """Return compact, ticker-specific row cells from existing model evidence."""
    ticker = getattr(forecast, "ticker", None)
    price = getattr(forecast, "price_frame", None)
    decision = _map(getattr(forecast, "decision_card", {}))
    radar = _map(getattr(forecast, "radar", {}))
    if reference_date is not None:
        reference = reference_date
    else:
        reference = _date(getattr(price, "price_date", ""))
        if reference is None:
            try:
                from zoneinfo import ZoneInfo
                reference = datetime.now(ZoneInfo("Asia/Taipei")).date()
            except Exception:
                reference = date.today()
    last = _num(getattr(price, "last", None)) or _num(decision.get("現價"))
    previous = _num(getattr(price, "previous_close", None))
    fair_low = _fair_low(forecast)
    market = str(getattr(ticker, "market", "")).upper()
    price_mode = str(decision.get("資料標題") or "")
    market_status = str(getattr(price, "market_status", "") or "")
    status_label = "收盤" if price_mode.startswith("收盤") or market_status in {"closed_reference", "after_close"} else "現價"
    price_date = _date(getattr(price, "price_date", ""))
    date_label = f"{price_date.month}/{price_date.day}" if price_date else "日期待確認"
    price_status = f"{date_label}{status_label} {_price(last)}"
    if previous is not None:
        price_status += f"；前收 {_price(previous)}"

    entry = str(decision_brief.get("entry_zone") or "等待模型位階")
    lower_bound = _daily_lower_bound(forecast)
    zone_match = _PRICE_RE.findall(entry)
    if market == "TW" and lower_bound is not None and zone_match:
        vals = [_num(x) for x in zone_match[:2]]
        vals = [x for x in vals if x is not None]
        if vals:
            lo, hi = min(vals), max(vals)
            if hi < lower_bound:
                entry = f"模型區 {_price(lo)}～{_price(hi)} 低於今日跌停 {_price(lower_bound)}；今日不可成交，觀察是否止穩"
            elif lo < lower_bound:
                entry = f"今日可執行 {_price(lower_bound)}～{_price(hi)}（受跌停 {_price(lower_bound)} 限制）；守穩先 1/3，確認後分批"
    staged = str(decision_brief.get("staged_entry") or "依模型區等待價格與量能確認")
    if entry.startswith("模型區 ") or entry.startswith("今日可執行 "):
        staged = entry

    invalid = str(decision_brief.get("invalidation") or "待模型確認")
    risk = str(decision_brief.get("primary_risk") or "價格與量能尚未確認")
    risk_cell = f"跌破 {_price(invalid)}／{risk}" if _num(invalid) is not None else f"{invalid}；{risk}"

    events = _fresh_company_events(forecast, reference)
    if events:
        parts = []
        for item in events[:2]:
            title = " ".join(str(item.get("title") or "").split())
            source = str(item.get("source") or "").strip()
            published = _date(item.get("time"))
            stamp = f"{published.month}/{published.day}" if published else ""
            parts.append("／".join(x for x in (title, source, stamp) if x))
        evidence = "近3交易日公司事件：" + "；".join(parts)
    else:
        inst = str(radar.get("三大法人") or "籌碼資料待確認")
        quantum = str(radar.get("Quantum 貢獻") or "")
        vwap = str(decision.get("VWAP位置") or "VWAP待確認")
        volume = _num(getattr(price, "volume", None))
        avg_volume = None
        recent = list(getattr(price, "recent_volumes", []) or [])
        usable = [_num(x) for x in recent[-5:]]
        usable = [x for x in usable if x is not None and x > 0]
        if usable:
            avg_volume = sum(usable) / len(usable)
        volume_line = f"量能 {volume / avg_volume:.2f}倍近5日均量" if volume and avg_volume else "量能資料待確認"
        linkage = quantum.split("｜", 1)[0] if quantum else "產業聯動資料待確認"
        evidence = ("近3個交易日未偵測到新的公司專屬事件；改以法人、價格/VWAP、量能與產業聯動判斷。 "
                    f"法人：{inst[:100]}；{vwap}；{volume_line}；聯動：{linkage[:100]}")

    return {
        "industry": _industry(forecast),
        "price_status": price_status,
        "model_low": f"模型技術下緣 {_price(fair_low)}" if fair_low is not None else "模型技術下緣待確認",
        "entry": staged,
        "risk": risk_cell,
        "evidence": evidence,
        "confidence": str(decision_brief.get("confidence_label") or "待確認"),
        "market": market,
    }
