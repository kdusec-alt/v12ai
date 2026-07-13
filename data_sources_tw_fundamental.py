# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, timedelta, datetime
import math
import os
import re
import time
from statistics import median
from typing import Dict, List, Tuple

import pandas as pd
from truth_guard import today_taipei_date
from data_sources_tw_moneydj import parse_moneydj_revenue_news


_FUND_CACHE: Dict[tuple, tuple[float, Dict[str, object]]] = {}
_FUND_CACHE_TTL_SEC = int(os.environ.get("TINO_FUND_CACHE_TTL_SEC", "21600"))  # 6 hours
_FUND_FAST_BUDGET_SEC = float(os.environ.get("TINO_FUND_FAST_BUDGET_SEC", "7.5"))
_FUND_HTTP_TIMEOUT_SEC = float(os.environ.get("TINO_FUND_HTTP_TIMEOUT_SEC", "3.0"))


def _cache_get(key: tuple) -> Dict[str, object] | None:
    hit = _FUND_CACHE.get(key)
    if not hit:
        return None
    ts, value = hit
    if time.time() - ts <= _FUND_CACHE_TTL_SEC:
        out = dict(value)
        out["cache_hit"] = True
        return out
    _FUND_CACHE.pop(key, None)
    return None


def _cache_put(key: tuple, value: Dict[str, object]) -> Dict[str, object]:
    _FUND_CACHE[key] = (time.time(), dict(value))
    return value


def _finmind_query(dataset: str, stock_id: str, start: str, end: str | None = None) -> List[Dict[str, object]]:
    import requests
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {"dataset": dataset, "data_id": stock_id, "start_date": start}
    if end:
        params["end_date"] = end
    token = os.environ.get("FINMIND_TOKEN") or os.environ.get("FINMIND_API_TOKEN")
    if token:
        params["token"] = token
    r = requests.get(url, params=params, timeout=_FUND_HTTP_TIMEOUT_SEC, headers={"User-Agent": "TINO-V12/1.1"})
    r.raise_for_status()
    js = r.json()
    data = js.get("data") if isinstance(js, dict) else None
    return data if isinstance(data, list) else []


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


def _format_billion_and_wan_from_thousand(value) -> tuple[str, float | None]:
    x = _safe_float(value)
    if x is None:
        return "", None
    billion = x / 100_000.0  # 千元 -> 億元
    return _format_billion_value(billion), billion


def _format_billion_and_wan_from_twd(value) -> tuple[str, float | None]:
    x = _safe_float(value)
    if x is None:
        return "", None
    billion = x / 100_000_000.0  # 元 -> 億元
    return _format_billion_value(billion), billion


def _record_from_twd(source: str, month: str, revenue, mom=None, yoy=None, accum=None, accum_yoy=None) -> Dict[str, object]:
    txt, b = _format_billion_and_wan_from_twd(revenue)
    acc_txt, acc_b = _format_billion_and_wan_from_twd(accum) if accum not in (None, "") else ("", None)
    return {
        "accepted": b is not None,
        "source": source,
        "month": month,
        "revenue_raw_twd": _safe_float(revenue),
        "revenue": txt,
        "revenue_billion": b,
        "mom": _fmt_pct(mom),
        "yoy": _fmt_pct(yoy),
        "accum_revenue": acc_txt,
        "accum_revenue_billion": acc_b,
        "accum_yoy": _fmt_pct(accum_yoy),
        "unit_basis": "TWD",
    }


def _record_from_thousand(source: str, month: str, revenue, mom=None, yoy=None, accum=None, accum_yoy=None) -> Dict[str, object]:
    txt, b = _format_billion_and_wan_from_thousand(revenue)
    acc_txt, acc_b = _format_billion_and_wan_from_thousand(accum) if accum not in (None, "") else ("", None)
    return {
        "accepted": b is not None,
        "source": source,
        "month": month,
        "revenue_raw_thousand": _safe_float(revenue),
        "revenue": txt,
        "revenue_billion": b,
        "mom": _fmt_pct(mom),
        "yoy": _fmt_pct(yoy),
        "accum_revenue": acc_txt,
        "accum_revenue_billion": acc_b,
        "accum_yoy": _fmt_pct(accum_yoy),
    }


def _record_from_billion(source: str, month: str, revenue_billion, mom=None, yoy=None, accum_billion=None, accum_yoy=None) -> Dict[str, object]:
    b = _safe_float(revenue_billion)
    acc_b = _safe_float(accum_billion)
    return {
        "accepted": b is not None,
        "source": source,
        "month": month,
        "revenue": _format_billion_value(b),
        "revenue_billion": b,
        "mom": _fmt_pct(mom),
        "yoy": _fmt_pct(yoy),
        "accum_revenue": _format_billion_value(acc_b) if acc_b is not None else "",
        "accum_revenue_billion": acc_b,
        "accum_yoy": _fmt_pct(accum_yoy),
    }


def _fmt_twd_billion(value) -> str:
    txt, _ = _format_billion_and_wan_from_thousand(value)
    return txt


def _fmt_pct(value) -> str:
    x = _safe_float(value)
    if x is None:
        return ""
    return f"{x:+.2f}%"


def _parse_iso_date(value: object) -> date | None:
    try:
        txt = str(value or "")[:10]
        if re.match(r"\d{4}-\d{2}-\d{2}", txt):
            return date.fromisoformat(txt)
    except Exception:
        pass
    return None


def _quarter_label_from_date(value: object) -> str:
    d = _parse_iso_date(value)
    if not d:
        return "最近季"
    q = ((d.month - 1) // 3) + 1
    return f"{d.year}Q{q}"


def _eps_freshness(eps_date: object, price_date: str) -> Dict[str, object]:
    d = _parse_iso_date(eps_date)
    try:
        ref = date.fromisoformat(price_date) if re.match(r"\d{4}-\d{2}-\d{2}", str(price_date or "")) else today_taipei_date()
    except Exception:
        ref = today_taipei_date()
    if not d:
        return {"eps_quarter": "最近季", "eps_stale": True, "eps_usable": False, "eps_age_days": None}
    age = (ref - d).days
    stale = age > 120
    return {"eps_quarter": _quarter_label_from_date(d.isoformat()), "eps_stale": stale, "eps_usable": not stale, "eps_age_days": age}


def _target_revenue_month(price_date: str) -> tuple[int, int]:
    try:
        ref = date.fromisoformat(str(price_date)[:10]) if re.match(r"\d{4}-\d{2}-\d{2}", str(price_date or "")) else today_taipei_date()
    except Exception:
        ref = today_taipei_date()
    y, m = ref.year, ref.month - 1
    if m <= 0:
        y -= 1
        m = 12
    return y, m


def _prev_ym(y: int, m: int) -> tuple[int, int]:
    return (y, m - 1) if m > 1 else (y - 1, 12)


def _target_revenue_months(price_date: str, lookback: int = 4) -> List[str]:
    y, m = _target_revenue_month(price_date)
    out = []
    for _ in range(max(1, lookback)):
        out.append(f"{y:04d}/{m:02d}")
        y, m = _prev_ym(y, m)
    return out


def _normalize_month_text(value: object) -> str:
    sv = str(value or "").strip()
    if not sv:
        return ""
    if re.fullmatch(r"\d{6}", sv):
        return f"{sv[:4]}/{sv[4:]}"
    if re.fullmatch(r"\d{4}[-/]\d{1,2}", sv):
        y, m = re.split(r"[-/]", sv)
        return f"{int(y):04d}/{int(m):02d}"
    if re.fullmatch(r"\d{3,4}/\d{1,2}", sv):
        y, m = sv.split("/")
        y = int(y) + 1911 if len(y) == 3 else int(y)
        return f"{y:04d}/{int(m):02d}"
    return ""


def _month_from_value(row: Dict[str, object]) -> str:
    # Strict Revenue Month Anchor Guard: generic `date` is disclosure/update date
    # in many sources, so it cannot be treated as revenue month directly.
    for key in ("revenue_month", "month", "revenueMonth", "year_month", "YearMonth", "revenue_year_month"):
        m = _normalize_month_text(row.get(key))
        if m:
            return m
    return ""


def _month_from_finmind_revenue_row(row: Dict[str, object]) -> tuple[str, str, bool]:
    """Resolve the *revenue period* for FinMind monthly revenue rows.

    Important: in FinMind TaiwanStockMonthRevenue, fields such as
    `revenue_month` / `revenue_year` are growth rates, not the revenue
    year-month.  Do not treat them as month anchors.

    For safety, a generic `date` is treated as an announcement/sync month and
    mapped to the previous revenue month.  This prevents May revenue announced
    in June from being displayed as June revenue.  Because this is inferred, it
    is marked anchor_risk=True and remains reference-only unless official data
    or a safe cross-source confirms it.
    """
    for key in ("year_month", "YearMonth", "revenue_year_month", "revenue_period", "period"):
        m = _normalize_month_text(row.get(key))
        if m:
            return m, "explicit_revenue_period", False
    d = _parse_iso_date(row.get("date"))
    if not d:
        return "", "missing_month_anchor", True
    y, m = _prev_ym(d.year, d.month)
    return f"{y:04d}/{m:02d}", "finmind_date_inferred_prev_month", True


def _mops_candidate_urls(year: int, month: int, symbol: str) -> list[str]:
    roc_year = year - 1911
    sym = str(symbol or "").upper()
    # Speed guard: avoid probing all markets for every ticker.
    # .TW normally lives under listed market (sii). .TWO can be OTC or emerging (rotc).
    if sym.endswith(".TW"):
        buckets = ["sii"]
    elif sym.endswith(".TWO"):
        buckets = ["otc", "rotc"]
    else:
        buckets = ["sii", "otc", "rotc"]
    urls = []
    for bucket in buckets:
        # MOPS static files are usually non-padded month; keep zero-padded as fallback.
        for mm in (str(month), f"{month:02d}"):
            urls.append(f"https://mops.twse.com.tw/nas/t21/{bucket}/t21sc03_{roc_year}_{mm}_0.html")
    out = []
    for u in urls:
        if u not in out:
            out.append(u)
    return out


def _cell_float(v):
    return _safe_float(str(v).replace(",", ""), None)


def _extract_numeric_sequence_after_code(vals: List[str], stock_id: str) -> List[float]:
    idx = -1
    for i, v in enumerate(vals[:5]):
        vv = str(v).strip()
        if vv == stock_id or vv.startswith(stock_id + " ") or vv.startswith(stock_id + "\u3000"):
            idx = i
            break
    if idx < 0:
        return []
    nums = []
    for v in vals[idx + 1:]:
        fv = _cell_float(v)
        if fv is not None:
            nums.append(fv)
    return nums


def _extract_mops_row_from_df(df: pd.DataFrame, stock_id: str) -> Dict[str, object] | None:
    if df is None or df.empty:
        return None
    tmp = df.copy()
    tmp.columns = [" ".join([str(x) for x in c if str(x) != "nan"]).strip() if isinstance(c, tuple) else str(c).strip() for c in tmp.columns]
    for _, row in tmp.iterrows():
        vals = [str(x).strip() for x in row.tolist()]
        if not any(v == stock_id or v.startswith(stock_id) for v in vals[:5]):
            continue
        d = row.to_dict()
        def by_col(*needles, exclude=()):
            for col, val in d.items():
                c = str(col)
                if all(n in c for n in needles) and not any(e in c for e in exclude):
                    fv = _cell_float(val)
                    if fv is not None:
                        return fv
            return None
        revenue = by_col("當月", "營收", exclude=("累計", "去年", "上月"))
        mom = by_col("上月", "增減")
        yoy = by_col("去年", "增減")
        accum = by_col("累計", "營收", exclude=("去年", "增減"))
        accum_yoy = by_col("前期", "增減") or by_col("累計", "增減")
        nums = _extract_numeric_sequence_after_code(vals, stock_id)
        if revenue is None and len(nums) >= 1: revenue = nums[0]
        if mom is None and len(nums) >= 4: mom = nums[3]
        if yoy is None and len(nums) >= 5: yoy = nums[4]
        if accum is None and len(nums) >= 6: accum = nums[5]
        if accum_yoy is None and len(nums) >= 8: accum_yoy = nums[7]
        if revenue is None:
            return None
        return _record_from_thousand("MOPS_MonthRevenue", "", revenue, mom, yoy, accum, accum_yoy)
    return None


def _fetch_mops_month_revenue(symbol: str, price_date: str) -> Dict[str, object]:
    stock_id = symbol.split(".")[0]
    import requests
    last_error = ""
    targets = _target_revenue_months(price_date, lookback=3)
    for ym in targets:
        yy, mm = [int(x) for x in ym.split("/")]
        for url in _mops_candidate_urls(yy, mm, symbol):
            try:
                r = requests.get(url, timeout=_FUND_HTTP_TIMEOUT_SEC, headers={"User-Agent": "Mozilla/5.0 TINO-V12"})
                if r.status_code >= 400 or not r.content:
                    last_error = f"HTTP{r.status_code}"
                    continue
                r.encoding = "big5"
                html = r.text
                # Fast negative: parsing MOPS tables is expensive; skip pages not containing the ticker.
                if stock_id not in html:
                    continue
                tables = pd.read_html(html)
                for df in tables:
                    row = _extract_mops_row_from_df(df, stock_id)
                    if row:
                        row["month"] = ym
                        row["source_url"] = url
                        row["official"] = True
                        row["accepted"] = True
                        return row
            except Exception as exc:
                last_error = type(exc).__name__
    return {"accepted": False, "source": "MOPS_MonthRevenue", "reason": f"MOPS month revenue not found:{last_error}"}


def _pick_record_for_months(records: List[Dict[str, object]], months: List[str]) -> Dict[str, object] | None:
    good = [r for r in records if r.get("accepted") and r.get("revenue_billion") is not None]
    for m in months:
        for r in good:
            if str(r.get("month")) == m:
                return r
    if good:
        good = sorted(good, key=lambda r: str(r.get("month", "")))
        return good[-1]
    return None


def _fetch_finmind_month_revenue(symbol: str, price_date: str) -> Dict[str, object]:
    stock_id = symbol.split(".")[0]
    try:
        end_dt = date.fromisoformat(price_date) if re.match(r"\d{4}-\d{2}-\d{2}", price_date) else today_taipei_date()
    except Exception:
        end_dt = today_taipei_date()
    rows = _finmind_query("TaiwanStockMonthRevenue", stock_id, (end_dt - timedelta(days=420)).isoformat(), end_dt.isoformat())
    recs = []
    for row in rows:
        revenue = None
        for k in ("revenue", "Revenue", "month_revenue", "monthly_revenue"):
            if row.get(k) not in (None, ""):
                revenue = row.get(k); break
        if revenue is None:
            continue
        mom = row.get("revenue_month_growth") or row.get("mom") or row.get("MoM") or row.get("month_growth")
        yoy = row.get("revenue_year_growth") or row.get("yoy") or row.get("YoY") or row.get("year_growth")
        ym, anchor, anchor_risk = _month_from_finmind_revenue_row(row)
        if not ym:
            continue
        rec = _record_from_twd("FinMind_MonthRevenue", ym, revenue, mom, yoy, row.get("cumulative_revenue") or row.get("accumulated_revenue") or row.get("accum_revenue"), row.get("cumulative_revenue_year_growth") or row.get("accum_yoy"))
        rec["announcement_date"] = str(row.get("date", ""))[:10]
        rec["month_anchor"] = anchor
        rec["month_anchor_risk"] = bool(anchor_risk)
        if anchor != "explicit_revenue_period":
            rec["model_eligible"] = False
        if rec.get("accepted"):
            recs.append(rec)
    if not recs:
        return {"accepted": False, "source": "FinMind_MonthRevenue", "reason": "FinMind month revenue empty"}
    target = _pick_record_for_months(recs, _target_revenue_months(price_date, 6))
    if not target:
        return {"accepted": False, "source": "FinMind_MonthRevenue", "reason": "FinMind no target month"}
    history = [float(r["revenue_billion"]) for r in recs if _safe_float(r.get("revenue_billion")) not in (None, 0)]
    target["history_billion"] = history[-8:]
    target["accepted"] = True
    return target


def _normalize_external_revenue_unit(value: float, unit_hint: str) -> tuple[str, float | None]:
    hint = unit_hint.lower()
    v = _safe_float(value)
    if v is None:
        return "", None
    if "千" in unit_hint or "thousand" in hint:
        return _format_billion_and_wan_from_thousand(v)
    if "百萬" in unit_hint or "million" in hint:
        return _format_billion_value(v / 100.0), v / 100.0
    if "萬" in unit_hint:
        return _format_billion_value(v / 10000.0), v / 10000.0
    # Goodinfo / Yahoo TW revenue pages normally display 億元 after table normalization.
    return _format_billion_value(v), v


def _parse_public_revenue_table(source: str, url: str, symbol: str, price_date: str, default_unit: str = "億元") -> Dict[str, object]:
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return {"accepted": False, "source": source, "reason": "offline"}
    try:
        import requests
        html = requests.get(url, headers={"User-Agent": "Mozilla/5.0 TINO-V12"}, timeout=_FUND_HTTP_TIMEOUT_SEC).text
        if not html or len(html) < 200:
            return {"accepted": False, "source": source, "reason": "empty html"}
        targets = _target_revenue_months(price_date, 6)
        unit_hint = default_unit
        if "千元" in html: unit_hint = "千元"
        elif "百萬元" in html: unit_hint = "百萬元"
        elif "萬元" in html: unit_hint = "萬元"
        recs = []
        try:
            tables = pd.read_html(html)
        except Exception:
            tables = []
        for df in tables:
            if df is None or df.empty:
                continue
            for _, r in df.iterrows():
                vals = [str(x).strip() for x in r.tolist()]
                joined = "｜".join(vals)
                m = re.search(r"(\d{3,4})[/-](\d{1,2})", joined)
                if not m:
                    continue
                y = int(m.group(1)) + 1911 if len(m.group(1)) == 3 else int(m.group(1))
                mo = int(m.group(2))
                nums = [_safe_float(v) for v in vals]
                nums = [x for x in nums if x is not None]
                if not nums:
                    continue
                # Pick first positive data number that is not the year/month token.
                candidates = [x for x in nums if abs(x) > 0.0001 and int(abs(x)) not in {y, mo, y - 1911}]
                if not candidates:
                    continue
                revenue = candidates[0]
                txt, b = _normalize_external_revenue_unit(revenue, unit_hint)
                if b is None:
                    continue
                pct = [x for x in candidates[1:] if -500 < x < 500]
                recs.append({"accepted": True, "source": source, "month": f"{y:04d}/{mo:02d}", "revenue": txt, "revenue_billion": b, "mom": _fmt_pct(pct[0]) if len(pct) >= 1 else "", "yoy": _fmt_pct(pct[1]) if len(pct) >= 2 else ""})
        rec = _pick_record_for_months(recs, targets)
        if rec:
            return rec
        return {"accepted": False, "source": source, "reason": "no parse target"}
    except Exception as exc:
        return {"accepted": False, "source": source, "reason": f"{source} error:{type(exc).__name__}"}


def _parse_yahoo_tw_revenue(symbol: str, price_date: str) -> Dict[str, object]:
    return _parse_public_revenue_table("YahooRevenue", f"https://tw.stock.yahoo.com/quote/{symbol}/revenue", symbol, price_date, "億元")


def _parse_goodinfo_revenue(symbol: str, price_date: str) -> Dict[str, object]:
    code = symbol.split(".")[0]
    return _parse_public_revenue_table("GoodinfoRevenue", f"https://goodinfo.tw/tw/StockMonthRevenue.asp?STOCK_ID={code}", symbol, price_date, "億元")


def _parse_anue_revenue(symbol: str, price_date: str) -> Dict[str, object]:
    code = symbol.split(".")[0]
    return _parse_public_revenue_table("AnueRevenue", f"https://invest.cnyes.com/twstock/TWS/{code}/revenue", symbol, price_date, "億元")


def _fetch_finmind_eps(symbol: str, price_date: str) -> Dict[str, object]:
    stock_id = symbol.split(".")[0]
    try:
        end_dt = date.fromisoformat(price_date) if re.match(r"\d{4}-\d{2}-\d{2}", price_date) else today_taipei_date()
    except Exception:
        end_dt = today_taipei_date()
    start_dt = end_dt - timedelta(days=520)
    for dataset in ("TaiwanStockFinancialStatements", "TaiwanStockFinancialStatement"):
        try:
            rows = _finmind_query(dataset, stock_id, start_dt.isoformat(), end_dt.isoformat())
        except Exception:
            rows = []
        eps_rows = []
        for r in rows:
            blob = " ".join(str(r.get(k, "")) for k in ("type", "name", "origin_name", "account", "indicator"))
            if any(k in blob.upper() for k in ("EPS", "EARNINGS PER SHARE")) or "每股" in blob:
                val = None
                for key in ("value", "Value", "eps", "EPS"):
                    val = _safe_float(r.get(key), None)
                    if val is not None:
                        break
                if val is not None:
                    eps_rows.append((str(r.get("date", ""))[:10], val))
        if eps_rows:
            d, eps = sorted(eps_rows, key=lambda x: x[0])[-1]
            return {"accepted": True, "source": "FinMind_FinancialStatements", "eps": f"{eps:.2f}", "eps_date": d, "eps_source": "FinMind_FinancialStatements", "eps_quarter": _quarter_label_from_date(d)}
    return {"accepted": False, "source": "FinMind_FinancialStatements", "reason": "EPS field empty"}


def _revenue_close(a: Dict[str, object], b: Dict[str, object], tolerance: float = 0.08) -> bool:
    av = _safe_float(a.get("revenue_billion")); bv = _safe_float(b.get("revenue_billion"))
    if av is None or bv is None or av <= 0 or bv <= 0:
        return False
    return abs(av - bv) / max(abs(av), abs(bv), 0.01) <= tolerance


def _source_rank(src: str) -> int:
    order = {"MOPS_MonthRevenue": 0, "FinMind_MonthRevenue": 1, "GoodinfoRevenue": 2, "MoneyDJRevenueNews": 3, "YahooRevenue": 4, "AnueRevenue": 5}
    return order.get(src, 99)


def _single_record_sane(rec: Dict[str, object]) -> bool:
    b = _safe_float(rec.get("revenue_billion"))
    if b is None or b < 0:
        return False
    hist = [_safe_float(x) for x in rec.get("history_billion", [])]
    hist = [x for x in hist if x is not None and x > 0]
    if len(hist) >= 4 and b > 0:
        med = median(hist)
        if med > 0 and (b > med * 10 or b < med / 10):
            return False
    return True


def _choose_revenue_record(records: List[Dict[str, object]], price_date: str) -> tuple[Dict[str, object] | None, str, bool, str]:
    months = _target_revenue_months(price_date, 6)
    good = [r for r in records if r.get("accepted") and _safe_float(r.get("revenue_billion")) is not None]
    for m in months:
        same = [r for r in good if str(r.get("month")) == m]
        if not same:
            continue
        mops = [r for r in same if r.get("source") == "MOPS_MonthRevenue"]
        if mops:
            return mops[0], "月營收官方驗證", True, "official"
        for i, a in enumerate(same):
            for b in same[i + 1:]:
                if _revenue_close(a, b):
                    pair = sorted([a, b], key=lambda x: _source_rank(str(x.get("source"))))
                    primary = dict(pair[0])
                    primary["cross_sources"] = f"{a.get('source')},{b.get('source')}"
                    anchor_safe = all(not x.get("month_anchor_risk") for x in pair)
                    model_pair = all(x.get("model_eligible", True) is not False for x in pair)
                    if anchor_safe and model_pair:
                        return primary, "月營收交叉驗證", True, "cross_checked"
                    return primary, "月營收交叉參考｜月份/來源待官方確認，不入模型", False, "reference"
        sane = sorted([r for r in same if _single_record_sane(r)], key=lambda x: _source_rank(str(x.get("source"))))
        if sane:
            picked = sane[0]
            if picked.get("month_anchor_risk") or picked.get("model_eligible", True) is False:
                return picked, "月營收參考｜月份/來源待官方確認，不入模型", False, "reference"
            return picked, "月營收參考｜待官方交叉，不入模型", False, "reference"
    return None, "月營收待同步｜保留上月/官方資料查詢中", False, "pending"


def _parse_moneydj_revenue_news(symbol: str, price_date: str) -> Dict[str, object]:
    return parse_moneydj_revenue_news(symbol, price_date, timeout=_FUND_HTTP_TIMEOUT_SEC)


def _source_worker(fn, symbol: str, price_date: str) -> Dict[str, object]:
    try:
        return fn(symbol, price_date)
    except Exception as exc:
        return {"accepted": False, "source": getattr(fn, "__name__", "source"), "reason": type(exc).__name__}


def _run_sources_fast(symbol: str, price_date: str) -> List[Dict[str, object]]:
    """Run fundamental sources in the foreground with no orphan worker threads.

    RC24.1 used ThreadPoolExecutor + shutdown(wait=False).  Futures that had
    already started could not be cancelled, so public-site downloads continued
    after Streamlit finished rendering.  On a constrained cloud container this
    creates the exact pattern: result appears, then the backend is killed.

    RC24.2 keeps only official MOPS/FinMind in the normal render path.  Optional
    MoneyDJ/Goodinfo/Yahoo/Anue cross-check is explicit opt-in and still runs
    synchronously, so no work survives past the function return.
    """
    core_fns = [_fetch_mops_month_revenue, _fetch_finmind_month_revenue]
    deep_fns = [_parse_moneydj_revenue_news, _parse_goodinfo_revenue, _parse_yahoo_tw_revenue, _parse_anue_revenue]
    fns = list(core_fns)
    if os.environ.get("TINO_FUND_DEEP_CROSSCHECK", "0") == "1":
        fns.extend(deep_fns)

    results: List[Dict[str, object]] = []
    started = time.monotonic()
    for fn in fns:
        # Do not start another source after the foreground budget is consumed.
        # A source already running is allowed to finish under its own HTTP timeout;
        # critically, it never remains alive after this function returns.
        if results and (time.monotonic() - started) >= _FUND_FAST_BUDGET_SEC:
            results.append({
                "accepted": False,
                "source": "TW_FUNDAMENTAL_BUDGET_GUARD",
                "reason": f"foreground_budget>{_FUND_FAST_BUDGET_SEC}s",
            })
            break
        results.append(_source_worker(fn, symbol, price_date))

    if not results:
        results.append({"accepted": False, "source": "TW_FUNDAMENTAL_TIMEOUT", "reason": "no_source_result"})
    return results



def _enrich_same_month_growth(primary: Dict[str, object], records: List[Dict[str, object]]) -> Dict[str, object]:
    """Fill missing TW monthly growth fields from the same revenue month only.

    Revenue amount and growth percentages sometimes arrive from different public
    endpoints.  Mixing months would create a false acceleration signal, so this
    helper only coalesces accepted rows with an identical normalized month and
    records every contributing source.
    """
    out = dict(primary or {})
    month = _normalize_month_text(out.get("month"))
    if not month:
        return out
    same = [
        row for row in records
        if isinstance(row, dict)
        and row.get("accepted")
        and _normalize_month_text(row.get("month")) == month
    ]
    same = sorted(same, key=lambda row: _source_rank(str(row.get("source"))))
    used = []
    for key in ("mom", "yoy", "accum_revenue", "accum_revenue_billion", "accum_yoy"):
        if out.get(key) not in (None, "", "NA", "--"):
            continue
        for row in same:
            value = row.get(key)
            if value not in (None, "", "NA", "--"):
                out[key] = value
                used.append(str(row.get("source") or ""))
                break
    if used:
        existing = [x for x in str(out.get("growth_sources") or "").split(",") if x]
        out["growth_sources"] = ",".join(dict.fromkeys(existing + used))
    return out

def fetch_tw_fundamental_crosscheck(symbol: str, price_date: str) -> Dict[str, object]:
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return {"accepted": False, "source": "TW_FUNDAMENTAL_OFFLINE", "reason": "offline"}
    cache_key = ("tw_fundamental_v25_strict_anchor", str(symbol).upper(), str(price_date)[:10])
    cached = _cache_get(cache_key)
    if cached:
        return cached
    t0 = time.time()
    source_results = _run_sources_fast(symbol, price_date)
    eps = _fetch_finmind_eps(symbol, price_date)
    primary, status, model_ok, quality = _choose_revenue_record(source_results, price_date)
    raw_sources = [str(x.get("source")) for x in source_results if x.get("accepted")]
    eps_meta = _eps_freshness(eps.get("eps_date", ""), price_date) if eps.get("accepted") else {"eps_quarter": "最近季", "eps_stale": True, "eps_usable": False, "eps_age_days": None}
    primary = _enrich_same_month_growth(primary or {}, source_results)
    announcement_date = str(primary.get("announcement_date") or "")[:10]
    if not announcement_date:
        primary_month = str(primary.get("month") or "")
        match = next((rec for rec in source_results if rec.get("accepted") and str(rec.get("month") or "") == primary_month and str(rec.get("announcement_date") or "")), None)
        if match:
            announcement_date = str(match.get("announcement_date"))[:10]
    out = {
        "accepted": True,
        "cross_checked": bool(model_ok),
        "revenue_verified": bool(model_ok),
        "revenue_quality": quality,
        "revenue_model_usable": bool(model_ok),
        "target_month": _target_revenue_months(price_date, 1)[0],
        "month": primary.get("month") or _target_revenue_months(price_date, 1)[0],
        "revenue": primary.get("revenue", ""),
        "revenue_billion": primary.get("revenue_billion"),
        "mom": primary.get("mom", ""),
        "monthly_mom": primary.get("mom", ""),
        "mom_verified": bool(model_ok and primary.get("mom") not in (None, "", "NA", "--")),
        "yoy": primary.get("yoy", ""),
        "revenue_yoy": primary.get("yoy", ""),
        "yoy_verified": bool(model_ok and primary.get("yoy") not in (None, "", "NA", "--")),
        "accum_revenue": primary.get("accum_revenue", ""),
        "accum_yoy": primary.get("accum_yoy", ""),
        "accum_yoy_verified": bool(model_ok and primary.get("accum_yoy") not in (None, "", "NA", "--")),
        "growth_metrics_eligible": bool(model_ok and not primary.get("month_anchor_risk")),
        "growth_sources": primary.get("growth_sources", ""),
        "revenue_status": status,
        "revenue_source": primary.get("source") or "TW_FUNDAMENTAL_MULTI_SOURCE_PENDING",
        "source": primary.get("source") or "TW_FUNDAMENTAL_MULTI_SOURCE_PENDING",
        "revenue_raw_sources": ",".join(raw_sources),
        "cross_sources": primary.get("cross_sources", ""),
        "revenue_month_anchor": primary.get("month_anchor", ""),
        "revenue_month_anchor_risk": bool(primary.get("month_anchor_risk", False)),
        "revenue_source_url": primary.get("source_url", ""),
        # Never substitute price_date; stale revenue must not look newly announced.
        "announcement_date": announcement_date,
        "eps": eps.get("eps") if eps.get("accepted") else "",
        "eps_date": eps.get("eps_date", ""),
        "eps_quarter": eps_meta.get("eps_quarter", eps.get("eps_quarter") or "最近季"),
        "eps_source": eps.get("eps_source") or eps.get("source") or "",
        "eps_stale": bool(eps_meta.get("eps_stale")),
        "eps_usable": bool(eps_meta.get("eps_usable")),
        "eps_age_days": eps_meta.get("eps_age_days"),
        "fund_latency_sec": round(time.time() - t0, 2),
        "growth_accelerating": bool(
            model_ok
            and _safe_float(primary.get("yoy")) is not None
            and _safe_float(primary.get("accum_yoy")) is not None
            and float(_safe_float(primary.get("accum_yoy"), 0.0)) > float(_safe_float(primary.get("yoy"), 0.0)) + 5.0
        ),
        "reason": "月營收來源：" + (",".join(raw_sources) if raw_sources else "無") + ("｜EPS FinMind" if eps.get("accepted") else "｜EPS待同步"),
    }
    return _cache_put(cache_key, out)
