# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from typing import Dict, Any
from zoneinfo import ZoneInfo
from datetime import datetime

from truth_guard import parse_date_safe, today_taipei_date


def _code(symbol: str) -> str:
    return str(symbol).split(".")[0]


def _num(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "--", "null", "None"}:
        return None
    try:
        return float(text)
    except Exception:
        return None


def _first_num_from_level_text(value) -> float | None:
    """Parse TWSE MIS bid/ask fields like '89.50_89.60_...' safely."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-", "--"}:
        return None
    for part in text.replace("|", "_").split("_"):
        n = _num(part)
        if n is not None and n > 0:
            return n
    return None


def _market_prefix(symbol: str) -> str:
    # TPEX/OTC quote uses otc_XXXX.tw; TWSE listed uses tse_XXXX.tw.
    return "otc" if str(symbol).upper().endswith(".TWO") else "tse"


def _mis_source_name(prefix: str) -> str:
    return "TPEX_MIS_Realtime" if prefix == "otc" else "TWSE_MIS_Realtime"


def _mis_debug_base(symbol: str, prefix: str, ex_ch: str) -> Dict[str, Any]:
    """Build a compact Admin-only breadcrumb for MIS diagnosis.

    This object is returned inside the price candidate and is intended for
    Admin/Debug panels only.  It must not be rendered in the V9 front panel.
    """
    return {
        "mis_tried": True,
        "mis_market": "TPEX" if prefix == "otc" else "TWSE",
        "mis_symbol": ex_ch,
        "mis_source": _mis_source_name(prefix),
        "mis_http_status": None,
        "mis_raw_ok": False,
        "mis_raw_rows": 0,
        "mis_row_keys": [],
        "mis_parsed_last": None,
        "mis_parsed_high": None,
        "mis_parsed_low": None,
        "mis_parsed_time": None,
        "mis_last_source": None,
        "mis_reject_reason": None,
    }


def _attach_mis_debug(payload: Dict[str, object], debug: Dict[str, Any], *, reason: str | None = None) -> Dict[str, object]:
    if reason:
        debug["mis_reject_reason"] = reason
        payload.setdefault("reason", reason)
    payload["mis_debug"] = debug
    return payload


def _build_raw_time(row: Dict[str, Any], fetched_at: datetime) -> tuple[str, str]:
    """Return (price_date, raw_time) for V12 freshness guard.

    TWSE MIS may provide either:
    - d + t fields, for example d=20260702, t=11:13:12
    - tlong in milliseconds
    Some response variants omit t/d even when quote fields are valid.  In that
    case we use the HTTP fetch time as quote-received time so a live official
    MIS snapshot is not incorrectly marked as stale and replaced by Yahoo's
    delayed chart data.
    """
    d_raw = str(row.get("d") or "").strip()
    t_raw = str(row.get("t") or "").strip()
    tlong = str(row.get("tlong") or "").strip()

    if tlong and tlong.isdigit():
        try:
            ts = int(tlong)
            # MIS tlong is normally milliseconds.
            if ts > 10_000_000_000:
                ts = ts // 1000
            dt = datetime.fromtimestamp(ts, tz=ZoneInfo("Asia/Taipei"))
            return dt.date().isoformat(), dt.isoformat()
        except Exception:
            pass

    if len(d_raw) == 8 and t_raw and ":" in t_raw:
        price_date = parse_date_safe(f"{d_raw[:4]}-{d_raw[4:6]}-{d_raw[6:8]}")
        return price_date, f"{price_date} {t_raw}"

    # Last resort: valid MIS quote row but no quote-time fields.
    return fetched_at.date().isoformat(), fetched_at.isoformat()


def _parse_mis_row(symbol: str, row: Dict[str, Any], prefix: str, fetched_at: datetime, debug: Dict[str, Any] | None = None) -> Dict[str, object]:
    last = _num(row.get("z"))
    bid = _first_num_from_level_text(row.get("b"))
    ask = _first_num_from_level_text(row.get("a"))

    # If z is '-' but bid/ask are live, keep the official snapshot usable.
    # Prefer mid for reference; if only one side exists, use that side.
    last_source = "last"
    if last is None or last <= 0:
        if bid and ask:
            last = round((bid + ask) / 2.0, 2)
            last_source = "bid_ask_mid"
        elif ask:
            last = ask
            last_source = "ask_proxy"
        elif bid:
            last = bid
            last_source = "bid_proxy"
    if last is None or last <= 0:
        payload = {"accepted": False, "source": _mis_source_name(prefix), "reason": "twse_mis_no_valid_last_bid_ask"}
        if debug is not None:
            debug["mis_parsed_last"] = None
            return _attach_mis_debug(payload, debug, reason="twse_mis_no_valid_last_bid_ask")
        return payload

    open_ = _num(row.get("o")) or last
    high = _num(row.get("h")) or max(open_, last)
    low = _num(row.get("l")) or min(open_, last)
    prev = _num(row.get("y")) or 0.0
    # TWSE MIS volume fields differ by endpoint.  v is usually total volume in board lots.
    lots = _num(row.get("v")) or _num(row.get("tv")) or 0.0
    volume = lots * 1000.0 if lots and lots < 10_000_000 else lots
    price_date, raw_time = _build_raw_time(row, fetched_at)
    if debug is not None:
        debug.update({
            "mis_parsed_last": float(last),
            "mis_parsed_open": float(open_),
            "mis_parsed_high": float(max(high, last)),
            "mis_parsed_low": float(min(low, last)),
            "mis_previous_close": float(prev),
            "mis_volume": float(volume or 0),
            "mis_parsed_time": raw_time,
            "mis_last_source": last_source,
        })

    return {
        "accepted": True,
        "source": _mis_source_name(prefix),
        "open": float(open_),
        "high": float(max(high, last)),
        "low": float(min(low, last)),
        "last": float(last),
        "previous_close": float(prev),
        "volume": float(volume or 0),
        "vwap": float((max(high, last) + min(low, last) + last) / 3.0),
        "price_date": price_date,
        "raw_time": raw_time,
        "mis_symbol": f"{prefix}_{_code(symbol)}.tw",
        "mis_last_source": last_source,
        "mis_debug": debug or {},
    }


def fetch_twse_mis_live_price(symbol: str) -> Dict[str, object]:
    """Official TWSE/TPEX MIS realtime quote for Taiwan intraday price.

    V8.5 fix:
    - Do not lose official MIS quotes just because the response omits d/t.
    - Parse tlong milliseconds when present.
    - Accept bid/ask snapshot if z is '-' but live levels exist.
    - Return explicit reject reason for Admin/debug, while keeping frontend clean.
    """
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return {"accepted": False, "source": "TWSE_MIS_Realtime", "reason": "offline"}
    try:
        import requests
        import time as _time

        code = _code(symbol)
        prefix = _market_prefix(symbol)
        ex_ch = f"{prefix}_{code}.tw"
        debug = _mis_debug_base(symbol, prefix, ex_ch)
        fetched_at = datetime.now(ZoneInfo("Asia/Taipei"))
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            "Referer": "https://mis.twse.com.tw/stock/index.jsp",
            "Connection": "keep-alive",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        sess = requests.Session()
        sess.headers.update(headers)
        # Warm up cookies; fibest page helps some TWSE/TPEX MIS deployments.
        for warm_url in (
            "https://mis.twse.com.tw/stock/index.jsp",
            f"https://mis.twse.com.tw/stock/fibest.jsp?stock={code}",
        ):
            try:
                sess.get(warm_url, timeout=2)
            except Exception:
                pass

        params = {"ex_ch": ex_ch, "json": "1", "delay": "0", "_": int(_time.time() * 1000)}
        resp = sess.get("https://mis.twse.com.tw/stock/api/getStockInfo.jsp", params=params, timeout=5)
        debug["mis_http_status"] = getattr(resp, "status_code", None)
        raw_text = resp.text or ""
        try:
            data = resp.json()
            debug["mis_raw_ok"] = True
        except Exception:
            reason = f"twse_mis_non_json:{resp.status_code}:{raw_text[:80]}"
            return _attach_mis_debug({"accepted": False, "source": _mis_source_name(prefix), "reason": reason}, debug, reason=reason)

        arr = (data or {}).get("msgArray") or []
        debug["mis_raw_rows"] = len(arr)
        row = arr[0] if arr else None
        if row:
            try:
                debug["mis_row_keys"] = sorted(list(row.keys()))[:40]
            except Exception:
                debug["mis_row_keys"] = []
        if not row:
            # Keep the exact symbol visible in Admin Debug if needed.
            reason = f"twse_mis_empty:{ex_ch}"
            return _attach_mis_debug({"accepted": False, "source": _mis_source_name(prefix), "reason": reason}, debug, reason=reason)

        parsed = _parse_mis_row(symbol, row, prefix, fetched_at, debug)
        if not parsed.get("accepted"):
            reason = f"{parsed.get('reason')}:{ex_ch}"
            parsed["reason"] = reason
            if isinstance(parsed.get("mis_debug"), dict):
                parsed["mis_debug"]["mis_reject_reason"] = reason
        return parsed
    except Exception as exc:
        prefix = _market_prefix(symbol)
        ex_ch = f"{prefix}_{_code(symbol)}.tw"
        debug = _mis_debug_base(symbol, prefix, ex_ch)
        reason = f"twse_mis_error:{type(exc).__name__}"
        return _attach_mis_debug({"accepted": False, "source": _mis_source_name(prefix), "reason": reason}, debug, reason=reason)



def fetch_google_finance_reference(symbol: str) -> Dict[str, object]:
    """Best-effort Google Finance reference quote.

    Google Finance is not a stable official API.  V12 uses this only as a
    third-source reference note; it must never override TWSE/TPEX MIS or Yahoo.
    """
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return {"accepted": False, "reason": "offline"}
    try:
        import re
        import requests
        code = _code(symbol)
        exch = "TWO" if str(symbol).upper().endswith(".TWO") else "TPE"
        url = f"https://www.google.com/finance/quote/{code}:{exch}"
        html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3).text
        # Common Google Finance visible price block: <div class="YMlKec fxKbKc">NT$74.90</div>
        m = re.search(r'class="YMlKec[^"]*"[^>]*>\s*(?:NT\$|TWD|\$)?\s*([0-9,]+(?:\.[0-9]+)?)\s*<', html)
        if not m:
            return {"accepted": False, "reason": "google_finance_no_price"}
        last = _num(m.group(1))
        if last is None or last <= 0:
            return {"accepted": False, "reason": "google_finance_invalid_price"}
        return {
            "accepted": True,
            "source": "GoogleFinance_Reference",
            "last": float(last),
            "price_date": today_taipei_date(),
            "raw_time": None,
            "reference_only": True,
        }
    except Exception as exc:
        return {"accepted": False, "reason": f"google_finance_error:{type(exc).__name__}"}
