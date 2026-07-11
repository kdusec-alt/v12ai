# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
import html as html_lib
import math
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Dict, List, Tuple


def _safe_float(value, default=None):
    try:
        if value in (None, "", "None", "nan", "--", "-"):
            return default
        x = float(str(value).replace(",", "").replace("%", "").strip())
        if math.isfinite(x):
            return x
    except Exception:
        pass
    return default


def _fmt_pct(value) -> str:
    x = _safe_float(value)
    return "" if x is None else f"{x:+.2f}%"


def _format_billion_value(billion: float | None) -> str:
    b = _safe_float(billion)
    if b is None:
        return ""
    if abs(b) < 0.1:
        wan = b * 10000.0
        wan_txt = f"{wan:,.1f}".rstrip("0").rstrip(".")
        return f"{b:,.4f}億（{wan_txt}萬）"
    if abs(b) < 1:
        return f"{b:,.4f}億"
    return f"{b:,.2f}億"


def _prev_ym(y: int, m: int) -> tuple[int, int]:
    return (y, m - 1) if m > 1 else (y - 1, 12)


def _target_revenue_month(price_date: str) -> tuple[int, int]:
    try:
        ref = date.fromisoformat(str(price_date)[:10]) if re.match(r"\d{4}-\d{2}-\d{2}", str(price_date or "")) else date.today()
    except Exception:
        ref = date.today()
    y, m = ref.year, ref.month - 1
    if m <= 0:
        y -= 1
        m = 12
    return y, m


def _target_revenue_months(price_date: str, lookback: int = 8) -> List[str]:
    y, m = _target_revenue_month(price_date)
    out = []
    for _ in range(max(1, lookback)):
        out.append(f"{y:04d}/{m:02d}")
        y, m = _prev_ym(y, m)
    return out


def _month_from_news_month(mm: int, price_date: str) -> str:
    for ym in _target_revenue_months(price_date, 8):
        if int(ym.split("/")[1]) == int(mm):
            return ym
    ref_y = int(str(price_date)[:4]) if re.match(r"\d{4}", str(price_date or "")) else date.today().year
    return f"{ref_y:04d}/{int(mm):02d}"


def _strip_html_text(value: str) -> str:
    txt = html_lib.unescape(str(value or ""))
    txt = re.sub(r"<[^>]+>", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


def _moneydj_number_to_billion(num, unit: str) -> float | None:
    v = _safe_float(num)
    if v is None:
        return None
    u = str(unit or "")
    if "億" in u:
        return v
    if "萬" in u:
        return v / 10000.0
    if "千" in u:
        return v / 100000.0
    if "元" in u:
        return v / 100000000.0
    return None


def _extract_pct(text: str, pos_words: Tuple[str, ...], neg_words: Tuple[str, ...]) -> str:
    for w in pos_words + neg_words:
        m = re.search(w + r"[^0-9+\-]{0,12}([+\-]?\d+(?:\.\d+)?)\s*%", text)
        if not m:
            continue
        val = _safe_float(m.group(1))
        if val is None:
            continue
        if w in neg_words and val > 0:
            val = -val
        return _fmt_pct(val)
    return ""


def extract_moneydj_revenue_from_text(symbol: str, text: str, price_date: str, link: str = "") -> Dict[str, object] | None:
    code = symbol.split(".")[0]
    clean = _strip_html_text(text)
    if code not in clean and "月營收" not in clean:
        return None
    mm_match = re.search(r"(\d{1,2})\s*月(?:合併)?營收", clean)
    if not mm_match:
        return None
    ym = _month_from_news_month(int(mm_match.group(1)), price_date)
    seg = clean[max(0, mm_match.start() - 16): min(len(clean), mm_match.end() + 90)]
    rev_match = re.search(r"(?:營收|合併營收)[^0-9]{0,18}([0-9,]+(?:\.[0-9]+)?)\s*(億|萬|千)?元", seg)
    if not rev_match:
        rev_match = re.search(r"([0-9,]+(?:\.[0-9]+)?)\s*(億|萬|千)?元", seg)
    if not rev_match:
        return None
    b = _moneydj_number_to_billion(rev_match.group(1), rev_match.group(2) or "元")
    if b is None:
        return None
    mom = _extract_pct(clean, ("月增", "較上月增加", "月成長", "MoM"), ("月減", "較上月減少"))
    yoy = _extract_pct(clean, ("年增", "較去年同期增加", "較去年同期成長", "YoY"), ("年減", "較去年同期減少"))
    accum_b = None
    accum_yoy = ""
    acc = re.search(r"(?:累計|1\s*(?:至|~|-)\s*\d+\s*月|前\s*\d+\s*月)[^0-9]{0,30}([0-9,]+(?:\.[0-9]+)?)\s*(億|萬|千)?元", clean)
    if acc:
        accum_b = _moneydj_number_to_billion(acc.group(1), acc.group(2) or "元")
        tail = clean[acc.start(): min(len(clean), acc.end() + 90)]
        accum_yoy = _extract_pct(tail, ("累計年增", "年增", "成長", "增加"), ("累計年減", "年減", "減少", "衰退"))
    return {
        "accepted": True,
        "source": "MoneyDJRevenueNews",
        "month": ym,
        "revenue": _format_billion_value(b),
        "revenue_billion": b,
        "mom": mom,
        "yoy": yoy,
        "accum_revenue": _format_billion_value(accum_b) if accum_b is not None else "",
        "accum_revenue_billion": accum_b,
        "accum_yoy": accum_yoy,
        "source_url": link,
        "month_anchor": "news_month_phrase",
        "month_anchor_risk": False,
        "model_eligible": False,
    }


def _pick_record_for_months(records: List[Dict[str, object]], months: List[str]) -> Dict[str, object] | None:
    good = [r for r in records if r.get("accepted") and r.get("revenue_billion") is not None]
    for m in months:
        for r in good:
            if str(r.get("month")) == m:
                return r
    return sorted(good, key=lambda r: str(r.get("month", "")))[-1] if good else None


def parse_moneydj_revenue_news(symbol: str, price_date: str, timeout: float = 2.0) -> Dict[str, object]:
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return {"accepted": False, "source": "MoneyDJRevenueNews", "reason": "offline"}
    code = symbol.split(".")[0]
    queries = [f"{code} 月營收 MoneyDJ", f"{code} 營收 site:moneydj.com/kmdj/news"]
    records: List[Dict[str, object]] = []
    try:
        import requests
        for q in queries:
            url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"})
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 TINO-V12"}, timeout=timeout)
            if r.status_code >= 400 or not r.content:
                continue
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:8]:
                txt = f"{item.findtext('title') or ''} {item.findtext('description') or ''}"
                rec = extract_moneydj_revenue_from_text(symbol, txt, price_date, item.findtext("link") or "")
                if rec and rec.get("accepted"):
                    records.append(rec)
    except Exception as exc:
        return {"accepted": False, "source": "MoneyDJRevenueNews", "reason": f"MoneyDJ error:{type(exc).__name__}"}
    rec = _pick_record_for_months(records, _target_revenue_months(price_date, 8))
    return rec or {"accepted": False, "source": "MoneyDJRevenueNews", "reason": "no MoneyDJ revenue hit"}
