Warning: truncated output (original token count: 14336)
Total output lines: 1257

# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, timedelta, datetime, time as dtime
from dataclasses import replace
import os
import re
from typing import List, Dict, Tuple
import math
import pandas as pd
from zoneinfo import ZoneInfo
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import email.utils
import time


from models import PriceFrame, TickerInfo, NewsItem
from news_timestamp_provenance_v1079 import (
    append_provenance_tag,
    guarded_score,
    resolve_aggregator_timestamp,
)
try:
    from analyst_event_intelligence import classify_analyst_headline
except Exception:
    # Optional RC4.6 enrichment must never block the stable price/news pipeline.
    def classify_analyst_headline(value):
        return "", ""
from truth_guard import make_truth, parse_date_safe
from quantum_market_context import fetch_market_proxy_context
from fundamental_growth_guard import fetch_us_quarterly_metrics, build_us_fundamental_context, detect_us_asset_type
from earnings_calendar_guard_v1076 import merge_us_earnings_calendar
try:
    from macro_event_calendar import build_macro_context
except Exception:
    def build_macro_context(price_date: str = "", **kwargs):
        return {
            "accepted": False,
            "source": "MACRO_CALENDAR_UNAVAILABLE",
            "date": str(price_date or ""),
            "calendar": "宏觀事件待同步",
            "events": [],
            "nearest_event": {},
            "event_uncertainty": 0.0,
            "event_risk": 0.0,
            "confidence_penalty": 0.0,
            "position_scale": 1.0,
            "pre_event_direction": 0.0,
        }

US_SAMPLE = {
    "ONDS": dict(open=2.42, high=2.55, low=2.31, last=2.38, previous_close=2.45, volume=3600000, vwap=2.41, atr14=0.22),
    "MRVL": dict(open=72.1, high=74.8, low=71.0, last=73.4, previous_close=71.8, volume=14500000, vwap=73.0, atr14=3.2),
    "MU": dict(open=129.0, high=135.2, low=128.1, last=133.5, previous_close=126.8, volume=32000000, vwap=132.3, atr14=5.8),
}


US_PUBLIC_MEMORY = {
    "MRVL": {
        "shortPercentOfFloat": 0.0526,
        "sharesShort": 39310000,
        "shortRatio": 0.71,
        "floatShares": 747000000,
        "longName": "Marvell Technology, Inc.",
        "sector": "Technology",
        "industry": "Semiconductors",
        "trailingEps": 2.91,
        "totalRevenue": 2420000000,
        "earningsQuarterlyGrowth": 0.0897,
        "revenueGrowth": 0.0100,
        "trailingPE": 91.67,
        "fiscalQuarterLabel": "Q1",
    },
    "MU": {
        "shortPercentOfFloat": 0.0370,
        "sharesShort": 42000000,
        "shortRatio": 1.20,
        "floatShares": 1120000000,
        "longName": "Micron Technology, Inc.",
        "sector": "Technology",
        "industry": "Semiconductors / Memory",
        "trailingEps": 44.27,
        "totalRevenue": 9542700000,
        "earningsQuarterlyGrowth": 0.7375,
        "revenueGrowth": 3.4572,
        "trailingPE": 25.58,
        "fiscalQuarterLabel": "Q3",
    },
    "ONDS": {
        "shortPercentOfFloat": 0.3329,
        "sharesShort": 41590000,
        "shortRatio": 2.06,
        "floatShares": 124900000,
        "longName": "Ondas Holdings Inc.",
        "sector": "Technology",
        "industry": "Communication Equipment / Drone / Defense",
        "trailingEps": 0.09,
        "totalRevenue": 40000000,
        "earningsQuarterlyGrowth": 0.6646,
        "revenueGrowth": 10.7990,
        "trailingPE": 87.00,
        "fiscalQuarterLabel": "Q2",
    },
}


def _merge_public_memory(symbol: str, info: Dict[str, object]) -> Dict[str, object]:
    """V9-style public memory overlay.

    Yahoo sometimes omits short float / PE / next earnings fields on Cloud. V9 kept
    verified public context instead of letting the right radar go empty. The overlay
    fills only missing/empty fields; live Yahoo values still win.
    """
    mem = US_PUBLIC_MEMORY.get(symbol.upper(), {})
    out = dict(info or {})
    for k, v in mem.items():
        if out.get(k) in (None, "", "NA"):
            out[k] = v
    return out



def _clean_num(v, default=None):
    try:
        if v in (None, '', 'NA'):
            return default
        if isinstance(v, str):
            v = v.replace(',', '').replace('%','').strip()
        x = float(v)
        if math.isfinite(x):
            return x
    except Exception:
        pass
    return default

def _fmt_source(v):
    return str(v or '').strip() or 'US_PUBLIC'

def _sector_persona(symbol: str, info: Dict[str, object], news_titles: str = '') -> Dict[str, str]:
    blob = ' '.join([symbol, str(info.get('longName','')), str(info.get('sector','')), str(info.get('industry','')), news_titles]).upper()
    # Order matters: ONDS contains "Communication Equipment"; substring "IP" inside equipment
    # must not classify it as semiconductor IP.
    if any(k in blob for k in ['DRONE','DEFENSE','AEROSPACE','UAV','UNMANNED']):
        return {
            'badge': '國防/無人機事件盤｜高波動題材｜盤中用 VWAP 驗證',
            'label': '國防/無人機事件盤',
            'bias': '事件股｜用VWAP驗證',
            'chip': '題材與訂單是主軸，Short Float 只是燃料，不是無腦追價理由。',
        }
    semicon_tokens = ['MEMORY','DRAM','NAND','HBM','MICRON','SEMICONDUCTOR','SEMICONDUCTORS','CHIP','SILICON','INTERFACE']
    is_ip = bool(re.search(r'\bIP\b', blob))
    is_ai = bool(re.search(r'\bAI\b', blob))
    if any(k in blob for k in semicon_tokens) or is_ip or (is_ai and 'TECHNOLOGY' in blob and symbol.upper() in {'MU','MRVL','NVDA','AMD','AVGO','TSM'}):
        return {
            'badge': '半導體 / 記憶體 / AI供應鏈｜盤中用 VWAP 驗證',
            'label': '半導體 / 記憶體 / AI供應鏈',
            'bias': 'AI敘事加分｜用VWAP驗證',
            'chip': '主軸是半導體、記憶體或AI供應鏈，仍以財報、VWAP與量價確認。',
        }
    return {
        'badge': '美股產業定位觀察｜盤中用 VWAP 驗證',
        'label': '美股產業定位觀察',
        'bias': '先看VWAP與正式收盤',
        'chip': '美股先看產業、財報、Short Float、VWAP，不套台股法人資券。',
    }

def _get_us_info(symbol: str) -> Dict[str, object]:
    if os.environ.get('TINO_OFFLINE_TEST') == '1':
        return _merge_public_memory(symbol, {})
    info = {}
    ticker_obj = None
    try:
        import yfinance as yf
        ticker_obj = yf.Ticker(symbol)
        info = dict(ticker_obj.get_info() or {})
    except Exception:
        try:
            import yfinance as yf
            ticker_obj = ticker_obj or yf.Ticker(symbol)
            info = dict(ticker_obj.info or {})
        except Exception:
            info = {}
    # Resolve the calendar from live Yahoo routes BEFORE applying the public
    # metrics fallback. A date in US_PUBLIC_MEMORY is a historical snapshot,
    # not a live calendar; letting it enter the resolver can make it outrank a
    # newer event by being one day closer (e.g. MU 09/23 vs Yahoo 09/30).
    info = merge_us_earnings_calendar(symbol, info, ticker_obj=ticker_obj)
    info = _merge_public_memory(symbol, info)
    # ETFs do not have one-company quarterly revenue/EPS.  Skip the extra
    # fundamentals request entirely to keep the universal route lightweight.
    if detect_us_asset_type(info) != "etf":
        quarterly = fetch_us_quarterly_metrics(symbol)
        if quarterly:
            info['_quarterly_metrics'] = quarterly
    return info

def _fetch_finviz_short(symbol: str) -> Dict[str, object]:
    if os.environ.get('TINO_OFFLINE_TEST') == '1':
        return {}
    try:
        import requests
        headers={'User-Agent':'Mozilla/5.0 TINO-V9-ShortFloat'}
        html=requests.get('https://finviz.com/quote.ashx?t='+symbol,headers=headers,timeout=8).text
        txt=re.sub(r'<[^>]+>',' ',html)
        txt=re.sub(r'\s+',' ',txt)
        out={}
        m=re.search(r'Short Float\s*/\s*Ratio\s*([0-9.]+)%\s*/\s*([0-9.]+)',txt,re.I)
        if m:
            out['short_float']=float(m.group(1)); out['short_ratio']=float(m.group(2)); out['short_source']='Finviz Short Float / Ratio'
        else:
            m=re.search(r'Short Float\s*([0-9.]+)%',txt,re.I)
            if m: out['short_float']=float(m.group(1)); out['short_source']='Finviz Short Float'
        m=re.search(r'Shs Float\s*([0-9.]+)([MB])',txt,re.I)
        if m:
            mult=1_000_000 if m.group(2).upper()=='M' else 1_000_000_000
            out['float_shares']=float(m.group(1))*mult
        return out
    except Exception:
        return {}

def _us_short_context(symbol: str, info: Dict[str, object], last: float, low: float, high: float, atr: float) -> Dict[str, object]:
    sf = _clean_num(info.get('shortPercentOfFloat'), None)
    if sf is not None and sf <= 1.5:
        sf = sf * 100.0
    shares_short = _clean_num(info.get('sharesShort'), None)
    short_ratio = _clean_num(info.get('shortRatio'), None)
    float_shares = _clean_num(info.get('floatShares'), None)
    source = 'YahooFinance quoteSummary'
    fz = _fetch_finviz_short(symbol)
    if fz.get('short_float') is not None and (sf is None or float(fz['short_float']) > float(sf) * 1.6 or sf == 0):
        sf = float(fz['short_float']); source = fz.get('short_source','Finviz Short Float')
    if short_ratio is None and fz.get('short_ratio') is not None:
        short_ratio = float(fz['short_ratio'])
    if float_shares is None and fz.get('float_shares') is not None:
        float_shares = float(fz['float_shares'])
    if shares_short is None and sf is not None and float_shares:
        shares_short = float_shares * sf / 100.0
        source = source + '｜derived sharesShort'
    # V9-like short cost zone: high short float uses higher squeeze band; normal uses VWAP/ATR band.
    sfv = float(sf or 0.0)
    if sfv >= 20:
        cost_low = max(0.01, low * 1.224)
        cost_high = max(cost_low, high * 1.56)
        trigger = cost_high
    elif sfv >= 8:
        cost_low = max(0.01, low * 1.10)
        cost_high = max(cost_low, high + atr * 2.2)
        trigger = cost_high
    else:
        cost_low = max(0.01, low * 0.666)
        cost_high = max(cost_low, last - atr * 0.10)
        trigger = high + atr * 0.65
    return {
        'accepted': sf is not None,
        'short_float': round(float(sf),2) if sf is not None else None,
        'shares_short': int(shares_short) if shares_short else None,
        'short_ratio': round(float(short_ratio),2) if short_ratio is not None else None,
        'float_shares': int(float_shares) if float_shares else None,
        'short_source': source if sf is not None else 'Yahoo/Finviz short float pending',
        'cost_low': round(cost_low,2), 'cost_high': round(cost_high,2), 'trigger': round(trigger,2),
        'source': source if sf is not None else 'US_SHORT_PENDING',
        'date': '',
    }

def _us_macro_context(price_date: str = "") -> Dict[str, object]:
    """One RC4 macro SSOT for the US route: official events + observed proxies."""
    out = build_macro_context(str(price_date or ""))
    try:
        proxies = fetch_market_proxy_context(str(price_date or ""))
        for key in (
            "sox", "nq", "qqq", "vix", "vix_change", "smh", "mu",
            "tsm_adr", "tx_night", "as_of", "symbols",
        ):
            if key in proxies:
                out[key] = proxies.get(key)
        if proxies.get("accepted"):
            out["market_proxy_source"] = proxies.get("source")
            out["source"] = "TINO_RC4_MACRO_CALENDAR+" + str(proxies.get("source") or "US_MARKET_PUBLIC")
    except Exception:
        # Calendar remains valid even when a market proxy endpoint is unavailable.
        pass
    return out

def _us_market_status_now() -> str:
    """V9-style US session router using America/New_York official trading windows.

    Regular session: 09:30-16:00 ET.
    Extended-hours reference: pre-market 04:00-09:30 ET, after-hours 16:00-20:00 ET.
    This function must be recalculated on every query, never cached at app boot.
    """
    try:
        now = datetime.now(ZoneInfo("America/New_York"))
        if now.weekday() >= 5:
            return "closed_reference"
        hm = now.hour * 60 + now.minute
        if 4 * 60 <= hm < 9 * 60 + 30:
            return "pre_market"
        if 9 * 60 + 30 <= hm < 16 * 60:
            return "intraday"
        if 16 * 60 <= hm < 20 * 60:
            return "after_hours"
        return "closed_reference"
    except Exception:
        return "closed_reference"


def _us_session_label(status: str) -> str:
    return {
        "pre_market": "盤前",
        "intraday": "盤中",
        "after_hours": "盤後",
        "closed_reference": "休市",
    }.get(str(status or ""), "休市")


def _safe_ts_label(ts) -> str:
    try:
        if hasattr(ts, "to_pydatetime"):
            dt = ts.to_pydatetime()
        else:
            dt = ts
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=ZoneInfo("America/New_York"))
        return dt.astimezone(ZoneInfo("Asia/Taipei")).strftime("%m/%d %H:%M 台灣")
    except Exception:
        return "時間待同步"


def _session_window(status: str) -> tuple[int, int] | None:
    return {
        "pre_market": (4 * 60, 9 * 60 + 30),
        "intraday": (9 * 60 + 30, 16 * 60),
        "after_hours": (16 * 60, 20 * 60),
    }.get(str(status or ""))


def _session_rows(frame: pd.DataFrame, status: str, now_ny: datetime | None = None) -> pd.DataFrame:
    """Return bars from exactly one US trading session.

    Pre-market, regular-session and after-hours volume/VWAP are different
    evidence.  Returning an empty frame is safer than silently mixing them.
    """
    window = _session_window(status)
    if frame is None or frame.empty or window is None:
        return frame.iloc[0:0] if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    now_ny = now_ny or datetime.now(ZoneInfo("America/New_York"))
    try:
        idx_ny = (
            frame.index.tz_convert("America/New_York")
            if getattr(frame.index, "tz", None) is not None
            else frame.index.tz_localize("America/New_York")
        )
        minutes = idx_ny.hour * 60 + idx_ny.minute
        mask = (
            (idx_ny.date == now_ny.date())
            & (minutes >= window[0])
            & (minutes < window[1])
        )
        return frame.loc[mask]
    except Exception:
        return frame.iloc[0:0]


def _latest_regular_reference(
    frame: pd.DataFrame,
    status: str,
    now_ny: datetime | None = None,
) -> Dict[str, object]:
    """Return the latest completed US regular-session OHLCV before a live quote.

    Yahoo's daily candle can lag one completed session around pre-market.  In
    that case comparing the live quote with the stale daily close produces a
    multi-day move labelled as a pre-market move.  Intraday bars are an
    independent reference: pre-market/intraday use the prior completed regular
    session, while after-hours may use today's completed regular session.
    """
    if frame is None or frame.empty:
        return {}
    now_ny = now_ny or datetime.now(ZoneInfo("America/New_York"))
    try:
        idx_ny = (
            frame.index.tz_convert("America/New_York")
            if getattr(frame.index, "tz", None) is not None
            else frame.index.tz_localize("America/New_York")
        )
        minutes = idx_ny.hour * 60 + idx_ny.minute
        regular = frame.loc[(minutes >= 9 * 60 + 30) & (minutes < 16 * 60)].copy()
        if regular.empty:
            return {}
        regular_idx = idx_ny[(minutes >= 9 * 60 + 30) & (minutes < 16 * 60)]
        current_day = now_ny.date()
        allowed = (
            regular_idx.date <= current_day
            if status == "after_hours"
            else regular_idx.date < current_day
        )
        regular = regular.loc[allowed]
        regular_idx = regular_idx[allowed]
        if regular.empty:
            return {}
        ref_day = regular_idx[-1].date()
        day_rows = regular.loc[regular_idx.date == ref_day]
        if day_rows.empty:
            return {}
        return {
            "reference_close": round(float(day_rows["Close"].dropna().iloc[-1]), 4),
            "reference_open": round(float(day_rows["Open"].dropna().iloc[0]), 4),
            "reference_high": round(float(day_rows["High"].dropna().max()), 4),
            "reference_low": round(float(day_rows["Low"].dropna().min()), 4),
            "reference_volume": int(float(day_rows["Volume"].fillna(0).sum())),
            "reference_date": ref_day.isoformat(),
            "reference_source": "YahooFinance_IntradayRegularClose",
        }
    except Exception:
        return {}


def _formal_us_history(
    hist: pd.DataFrame,
    status: str,
    now_ny: datetime | None = None,
) -> pd.DataFrame:
    """Strip an unconfirmed current-day daily candle during regular trading."""
    if hist is None or hist.empty:
        return hist
    now_ny = now_ny or datetime.now(ZoneInfo("America/New_York"))
    out = hist
    if status in {"pre_market", "intraday"}:
        try:
            last_index = hist.index[-1]
            if hasattr(last_index, "tz_convert") and getattr(last_index, "tzinfo", None) is not None:
                last_date = last_index.tz_convert("America/New_York").date()
            else:
                last_date = last_index.date()
            if last_date == now_ny.date() and len(hist) >= 2:
                out = hist.iloc[:-1]
        except Exception:
            pass
    return out


def _fetch_us_extended_quote(symbol: str, previous_close: float, status: str) -> Dict[str, object]:
    """Fetch pre-market / after-hours / intraday quote without replacing official daily K history.

    Yahoo 1m/5m with prepost=True is used only as a live/extended-hours snapshot.
    Daily candles still provide formal MA/streak/regular-session close.
    """
    out = {
        "accepted": False,
        "source": "YahooFinance_PrePost_pending",
        "status": status,
        "label": _us_session_label(status),
    }
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return out
    if status not in {"pre_market", "intraday", "after_hours"}:
        return out
    try:
        import yfinance as yf
        tk = yf.Ticker(symbol)
        # 1m occasionally comes back empty outside regular session; 5m is a safe fallback.
        interval = "1m"
        h = tk.history(period="5d", interval=interval, prepost=True, auto_adjust=False, timeout=8)
        if h is None or h.empty:
            interval = "5m"
            h = tk.history(period="5d", interval=interval, prepost=True, auto_adjust=False, timeout=8)
        if h is None or h.empty:
            return out
        h = h.dropna(subset=["Close"])
        if h.empty:
            return out
        regular_reference = _latest_regular_reference(h, status)
        session_h = _session_rows(h, status)
        if session_h.empty:
            out["reason"] = "NO_BARS_IN_REQUESTED_SESSION"
            return out
        last_row = session_h.iloc[-1]
        last_ts = session_h.index[-1]
        last = float(last_row["Close"])
        prev = float(regular_reference.get("reference_close") or previous_close or last)
        chg = last - prev
        chgp = (chg / prev * 100.0) if prev else 0.0
        open_value = (
            float(session_h["Open"].dropna().iloc[0])
            if "Open" in session_h and not session_h["Open"].dropna().empty
            else last
        )
        high = float(session_h["High"].dropna().max()) if "High" in session_h and not session_h["High"].dropna().empty else last
        low = float(session_h["Low"].dropna().min()) if "Low" in session_h and not session_h["Low"].dropna().empty else last
        vol = float(session_h["Volume"].fillna(0).sum()) if "Volume" in session_h else 0.0
        vwap = None
        try:
            pv = (session_h["Close"].astype(float) * session_h["Volume"].fillna(0).astype(float)).sum()
            vv = session_h["Volume"].fillna(0).astype(float).sum()
            if vv > 0:
                vwap = float(pv / vv)
        except Exception:
            pass
        try:
            trade_date = (
                last_ts.tz_convert("America/New_York").date().isoformat()
                if getattr(last_ts, "tzinfo", None) is not None
                else last_ts.date().isoformat()
            )
        except Exception:
            trade_date = ""
        out.update({
            "accepted": True,
            "source": f"YahooFinance_{interval}_PrePost",
            "open": round(open_value, 4),
            "last": round(last, 4),
            "high": round(high, 4),
            "low": round(max(low, 0.01), 4),
            "volume": int(vol) if vol else None,
            "vwap": round(vwap, 4) if vwap is not None else None,
            "vwap_accepted": vwap is not None,
            "vwap_scope": status,
            "reference_close": round(prev, 4),
            "reference_close_date": str(regular_reference.get("reference_date") or ""),
            "reference_source": str(regular_reference.get("reference_source") or ""),
            "reference_open": regular_reference.get("reference_open"),
            "reference_high": regular_reference.get("reference_high"),
            "reference_low": regular_reference.get("reference_low"),
            "reference_volume": regular_reference.get("reference_volume"),
            "change": round(chg, 4),
            "change_pct": round(chgp, 2),
            "trade_date": trade_date,
            "timestamp": _safe_ts_label(last_ts),
            "status": status,
            "label": _us_session_label(status),
        })
    except Exception as exc:
        out["error"] = type(exc).__name__
    return out

def _resolved_us_ticker(ticker: TickerInfo, info: Dict[str, object]) -> TickerInfo:
    """Resolve stock/ETF from metadata without any ticker-specific list."""
    asset_type = detect_us_asset_type(info)
    name = str(info.get("longName") or info.get("shortName") or ticker.name or ticker.resolved_symbol)
    exchange = str(info.get("fullExchangeName") or info.get("exchange") or ticker.exchange or "US")
    try:
        return replace(ticker, asset_type=asset_type, name=name, exchange=exchange)
    except Exception:
        return ticker


def _us_etf_persona(ticker: TickerInfo, info: Dict[str, object]) -> Dict[str, str]:
    category = str(info.get("category") or info.get("legalType") or "ETF")
    return {
        "badge": "ETF / 資產組合｜以價格熱度、成分與市場風險驗證",
        "label": f"ETF / {category}",
        "bias": "ETF熱度觀察｜不套單一公司EPS/PE",
        "chip": "ETF不套單一公司財報；泡沫雷達僅使用價格熱度、事件預期與市場風險。",
    }


def _fallback_price(ticker: TickerInfo, reason: str) -> PriceFrame:
    b = US_SAMPLE.get(ticker.resolved_symbol, dict(open=50, high=52, low=48, last=50, previous_close=50, volume=1000000, vwap=50, atr14=2.5))
    last = b["last"]
    closes = [round(last * (1 + math.sin(i/5)*0.025), 2) for i in range(60,0,-1)]
    highs = [round(x + b["atr14"]*0.4, 2) for x in closes]
    lows = [round(max(x - b["atr14"]*0.4, 0.01), 2) for x in closes]
    vols = [b["volume"] * (0.7 + i/140) for i in range(60,0,-1)]
    closes[-1] = b["last"]; highs[-1] = b["high"]; lows[-1] = b["low"]; vols[-1] = b["volume"]
    d = (date.today() - timedelta(days=1)).isoformat()
    info = _get_us_info(ticker.resolved_symbol)
    ticker = _resolved_us_ticker(ticker, info)
    is_etf = ticker.asset_type == "etf"
    short = {"accepted": False, "source": "US_ETF", "date": d} if is_etf else _us_short_context(ticker.resolved_symbol, info, b["last"], b["low"], b["high"], b["atr14"])
    short["date"] = d
    persona = _us_etf_persona(ticker, info) if is_etf else _sector_persona(ticker.resolved_symbol, info)
    ctx = {
        "macro": _us…2336 tokens truncated…mory market", "AI memory demand"],
        "peers": ["SK hynix Samsung HBM Micron", "NVIDIA HBM supply Micron"],
    },
    "MRVL": {
        "company": ["Marvell Technology earnings", "Marvell AI infrastructure", "Marvell custom silicon", "Marvell optical DSP"],
        "industry": ["custom silicon AI accelerator", "data center interconnect AI"],
        "peers": ["Marvell AWS Microsoft Google custom silicon", "Broadcom Marvell AI ASIC"],
    },
    "NVDA": {
        "company": ["NVIDIA earnings", "NVIDIA Blackwell", "NVIDIA Rubin", "NVIDIA AI chips"],
        "industry": ["AI chip demand", "GPU supply chain", "HBM Blackwell supply"],
        "peers": ["NVIDIA TSMC Microsoft Amazon AI", "NVIDIA export controls China"],
    },
    "AMD": {
        "company": ["Advanced Micro Devices earnings", "AMD MI350", "AMD MI400", "AMD AI accelerator"],
        "industry": ["AI accelerator market", "data center GPU demand"],
        "peers": ["AMD NVIDIA AI chips", "AMD Microsoft OpenAI AI"],
    },
    "AVGO": {
        "company": ["Broadcom earnings", "Broadcom AI custom silicon", "Broadcom VMware", "Broadcom networking chips"],
        "industry": ["AI networking chips", "custom ASIC AI"],
        "peers": ["Broadcom Google TPU", "Broadcom Marvell custom silicon"],
    },
    "TSM": {
        "company": ["TSMC earnings", "TSMC AI demand", "TSMC CoWoS", "TSMC Arizona"],
        "industry": ["semiconductor foundry demand", "CoWoS capacity AI"],
        "peers": ["TSMC NVIDIA Apple AMD"],
    },
    "AAPL": {
        "company": ["Apple earnings", "Apple AI", "Apple iPhone demand", "Apple supply chain"],
        "industry": ["smartphone demand", "consumer electronics AI"],
        "peers": ["Apple China tariff", "Apple TSMC supply chain"],
    },
    "ONDS": {
        "company": ["Ondas Holdings earnings", "Ondas Holdings drone", "Ondas defense order", "Ondas autonomous systems"],
        "industry": ["drone defense contracts", "UAV defense market"],
        "peers": ["Ondas Airobotics drone", "defense drone procurement"],
    },
}

_DAILY_HEADLINE_QUERIES = [
    # Each route is always queried once.  Do not stop after the first busy macro
    # feed, otherwise a major Hormuz / Taiwan Strait event can be starved.
    "US stock market Fed CPI PPI PCE ISM NFP Treasury yields",
    "US Iran Israel war Strait of Hormuz blockade oil Treasury yields Middle East Red Sea Houthi",
    "Trump tariff China semiconductor export control Taiwan Strait rare earth critical minerals AI supply chain",
]

_US_POLICY_GEO_TERMS = [
    "trump", "tariff", "tariffs", "china", "export control", "export controls", "sanction", "sanctions",
    "semiconductor restriction", "chip ban", "entity list", "taiwan strait", "south china sea",
    "middle east", "iran", "israel", "us-iran", "strait of hormuz", "hormuz", "red sea", "houthi",
    "oil", "crude", "opec", "treasury yield", "bond yields", "war", "geopolitical", "rare earth", "critical minerals",
]
_US_MACRO_TERMS = [
    "fed", "fomc", "cpi", "ppi", "pce", "nfp", "payrolls", "inflation", "rate cut",
    "rate cuts", "rate hike", "treasury yield", "10-year", "dxy", "ism", "pmi",
]
_US_BULL_TERMS = [
    "beat", "beats", "raises", "raised", "raise", "strong demand", "surge", "surges", "surged",
    "jumps", "jumped", "soaring", "record", "upgrade", "upgraded", "price target raised",
    "guidance raised", "wins", "contract", "order", "ai demand", "revenue growth", "earnings beat",
    "quadrupling", "boom", "rally", "outperform", "buy rating", "accelerating",
]
_US_BEAR_TERMS = [
    "miss", "misses", "cuts", "cut", "weak demand", "slump", "slumps", "downgrade", "downgraded",
    "guidance cut", "weak guidance", "soft guidance", "underwhelming guidance", "tepid guidance",
    "probe", "investigation", "ban", "tariff", "export control", "falls", "fell",
    "drops", "dropped", "plunges", "plunge", "selloff", "lawsuit", "warns", "warning", "delay",
    "stock offering", "share offering", "secondary offering", "follow-on offering",
    "global depositary shares", "global depositary receipts", "gds", "gdr",
    "capital raise", "dilution",
]
_US_AI_SEMI_TERMS = [
    "ai", "hbm", "dram", "nand", "memory", "blackwell", "rubin", "gpu", "semiconductor",
    "chip", "chips", "custom silicon", "asic", "data center", "accelerator", "cowoS", "tsmc",
]
_US_EARNINGS_TERMS = ["earnings", "revenue", "guidance", "q1", "q2", "q3", "q4", "quarter", "outlook"]

_US_CATALYST_TERMS: Dict[str, Tuple[str, ...]] = {
    "order": ("order", "contract", "award", "selected by", "customer win", "purchase agreement"),
    "capacity": ("capacity", "production", "manufacturing", "fab", "foundry", "supply agreement", "expands"),
    "partnership": ("partnership", "collaboration", "teams with", "jointly", "strategic agreement"),
    "product_demo": ("demonstrates", "demo", "showcase", "showcases", "unveils", "launches", "introduces", "technology demo", "technology demonstration"),
    "earnings_guidance": ("earnings", "revenue", "guidance", "outlook", "forecast", "margin"),
    "analyst": ("price target", "upgrade", "downgrade", "rating", "initiates coverage"),
}


def _us_catalyst_family(title: str) -> str:
    text = re.sub(r"\s+", " ", str(title or "").lower()).strip()
    for family in ("order", "capacity", "partnership", "product_demo", "earnings_guidance", "analyst"):
        if any(
            re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text)
            for term in _US_CATALYST_TERMS[family]
        ):
            return family
    return "company_event"


def _us_source_authority(publisher: str) -> Tuple[str, float]:
    """Return a conservative source tier and score multiplier."""
    text = str(publisher or "").lower()
    tier_1 = ("reuters", "associated press", "sec.gov", "business wire", "globenewswire", "pr newswire")
    official = ("investor relations", "technology, inc", "corporation", "corp.", "holdings, inc")
    tier_2 = ("bloomberg", "cnbc", "wall street journal", "financial times", "barron's", "marketwatch")
    if any(term in text for term in tier_1) or any(term in text for term in official):
        return "source_tier1", 1.0
    if any(term in text for term in tier_2):
        return "source_tier2", 0.88
    return "source_tier3", 0.72

def _us_company_base_name(ticker: TickerInfo) -> str:
    sym = str(ticker.resolved_symbol or ticker.symbol or "").upper()
    info = US_PUBLIC_MEMORY.get(sym, {})
    name = str(info.get("longName") or ticker.name or sym).strip()
    # Google News works better with English company names for US tickers.
    fallback = {
        "MU": "Micron Technology", "MRVL": "Marvell Technology", "NVDA": "NVIDIA", "AMD": "Advanced Micro Devices",
        "AVGO": "Broadcom", "TSM": "TSMC", "AAPL": "Apple", "MSFT": "Microsoft", "AMZN": "Amazon", "GOOGL": "Alphabet Google",
        "META": "Meta Platforms", "ONDS": "Ondas Holdings",
    }.get(sym)
    return fallback or name or sym


_US_ENTITY_ALIASES: Dict[str, Tuple[str, ...]] = {
    "AAPL": ("apple", "aapl", "iphone", "ipad", "ios"),
    "MU": ("micron", "micron technology", "nasdaq mu"),
    "MRVL": ("marvell", "marvell technology", "mrvl"),
    "NVDA": ("nvidia", "nvda", "blackwell", "rubin"),
    "AMD": ("advanced micro devices", "amd", "mi350", "mi400"),
    "AVGO": ("broadcom", "avgo", "vmware"),
    "TSM": ("tsmc", "taiwan semiconductor", "nyse tsm"),
    "MSFT": ("microsoft", "msft"),
    "AMZN": ("amazon", "amzn", "aws"),
    "GOOGL": ("alphabet", "google", "googl"),
    "META": ("meta platforms", "facebook", "meta"),
    "ONDS": ("ondas", "ondas holdings", "onds", "airobotics"),
}

_US_INDUSTRY_TERMS: Dict[str, Tuple[str, ...]] = {
    "AAPL": ("iphone", "ipad", "ios", "smartphone", "consumer electronics", "app store"),
    "MU": ("hbm", "dram", "nand", "memory pricing", "memory chip"),
    "MRVL": ("custom silicon", "optical dsp", "data center interconnect", "ai asic", "ethernet"),
    "NVDA": ("gpu", "ai accelerator", "blackwell", "rubin", "cuda"),
    "AMD": ("gpu", "ai accelerator", "mi350", "mi400", "epyc"),
    "AVGO": ("custom silicon", "ai networking", "vmware", "asic"),
    "TSM": ("foundry", "cowoS", "advanced packaging", "wafer", "2nm"),
    "ONDS": ("drone", "uav", "autonomous systems", "defense procurement"),
}


def _us_entity_aliases(ticker: TickerInfo) -> Tuple[str, ...]:
    sym = str(ticker.resolved_symbol or "").upper()
    base = _us_company_base_name(ticker).lower().strip()
    values = [alias.lower().strip() for alias in _US_ENTITY_ALIASES.get(sym, ())]
    if len(base) >= 3:
        values.append(base)
        root = re.sub(r"\b(incorporated|inc|corporation|corp|holdings|plc|limited|ltd)\b", "", base)
        root = re.sub(r"\s+", " ", root).strip()
        if len(root) >= 4:
            values.append(root)
    values.append(sym.lower())
    return tuple(dict.fromkeys(value for value in values if value))


def _contains_alias(text: str, alias: str) -> bool:
    alias = str(alias or "").strip().lower()
    if not alias:
        return False
    if re.fullmatch(r"[a-z0-9.\-]+", alias):
        return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text) is not None
    return alias in text


def _us_news_relevant_to_ticker(
    ticker: TickerInfo,
    item: NewsItem,
    bucket: str,
) -> bool:
    """Reject search-bucket pollution before it can affect Company News.

    Google can return a BMW or other unrelated headline for an Apple query
    because both mention tariffs/China.  A company row must name the entity;
    an industry row must name the entity or a ticker-specific sector product.
    Daily market weather is intentionally shared and always retained.
    """
    if bucket == "daily":
        return True
    text = re.sub(r"\s+", " ", str(getattr(item, "title", "") or "").lower()).strip()
    if not text:
        return False
    if any(_contains_alias(text, alias) for alias in _us_entity_aliases(ticker)):
        return True
    # Official IR/press-release headlines sometimes omit the company name from
    # the title because the publisher itself is the company.  Treat a matching
    # publisher identity as direct entity evidence.
    source_text = re.sub(r"\s+", " ", str(getattr(item, "source", "") or "").lower()).strip()
    if any(_contains_alias(source_text, alias) for alias in _us_entity_aliases(ticker)):
        return True
    if bucket != "industry":
        return False
    sym = str(ticker.resolved_symbol or "").upper()
    terms = _US_INDUSTRY_TERMS.get(sym, ())
    return any(term.lower() in text for term in terms)


def _filter_us_news_for_ticker(ticker: TickerInfo, items: List[NewsItem]) -> List[NewsItem]:
    out: List[NewsItem] = []
    for item in items or []:
        tag = str(item.tag or "").lower()
        if "daily_headline" in tag:
            bucket = "daily"
        elif "us_industry" in tag:
            bucket = "industry"
        else:
            bucket = "company"
        if _us_news_relevant_to_ticker(ticker, item, bucket):
            out.append(item)
    return out


def _us_news_profile_queries(ticker: TickerInfo) -> List[Tuple[str, str, int]]:
    sym = str(ticker.resolved_symbol or ticker.symbol or "").upper()
    base = _us_company_base_name(ticker)
    prof = _US_QUERY_PROFILES.get(sym, {})
    queries: List[Tuple[str, str, int]] = []
    # Universal exact-entity catalyst route. It is generated from the resolved
    # company name, so new US tickers receive the same announcement / order /
    # capacity / partnership / product-demo coverage as profiled symbols.
    catalyst_query = (
        f'"{base}" announces OR launches OR unveils OR demonstrates OR '
        "partnership OR collaboration OR expands capacity OR production OR contract OR order"
    )
    queries.append((catalyst_query, "company", 4))
    # Keep query count bounded for speed. Company / industry news is fetched
    # after Daily Headline so global market weather never gets starved by a
    # very active ticker such as MU/NVDA.
    company = list(prof.get("company") or [f"{base} earnings", f"{base} stock", f"{base} guidance"])
    industry = prof.get("industry") or []
    peers = prof.get("peers") or []
    analyst_query = f"{base} downgrade price target cut Morgan Stanley JPMorgan analyst rating"
    forward_risk_query = (
        f"{base} guidance weak soft underwhelming outlook not enough "
        "expectations demand slowdown margin decline offering capital raise "
        "GDS GDR dilution investigation"
    )
    # Keep only three company calls for speed: two operating/company routes plus
    # one dedicated analyst route so target changes cannot be starved.
    company_queries = company[:2]
    if not any("price target" in str(q).lower() or "analyst" in str(q).lower() for q in company_queries):
        company_queries.append(analyst_query)
    else:
        company_queries.extend(company[2:3])
    # Negative/forward route goes first so it cannot be starved by the global
    # 14-row cap on a busy symbol such as NVDA/MU.
    queries.append((forward_risk_query, "company", 3))
    for q in company_queries[:3]:
        queries.append((q, "company", 3))
    for q in industry[:2]:
        queries.append((q, "industry", 2))
    for q in peers[:1]:
        queries.append((q, "industry", 2))
    return queries


def _parse_google_pub_date(pub: str):
    try:
        dt = email.utils.parsedate_to_datetime(pub or "")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt
    except Exception:
        return None


def _us_news_age_days(pub: str) -> int | None:
    dt = _parse_google_pub_date(pub)
    if not dt:
        return None
    try:
        now = datetime.now(ZoneInfo("UTC"))
        return max(0, int((now - dt.astimezone(ZoneInfo("UTC"))).total_seconds() // 86400))
    except Exception:
        return None


def _us_news_time_label(pub: str) -> str:
    dt = _parse_google_pub_date(pub)
    if not dt:
        return "latest"
    try:
        return dt.astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(pub or "latest")[:24]


def _us_news_recent_enough(pub: str, bucket: str) -> bool:
    dt = _parse_google_pub_date(pub)
    if not dt:
        # Keep undated Google rows only as low-confidence references.
        return True
    now = datetime.now(ZoneInfo("UTC"))
    # RC24 rule: only 2026-current recent news; old 2024/2025 articles must not influence prediction.
    if dt.year < max(2026, now.year):
        return False
    age = _us_news_age_days(pub)
    if age is None:
        return True
    max_age = 14 if bucket == "daily" else 60 if bucket in {"company", "industry"} else 30
    return age <= max_age


def _score_us_news(title: str, bucket: str = "company") -> Tuple[float, str]:
    """Event-aware US headline scoring.

    RC24 final-mile rule:
    - Company news should not stay score=0 when the headline clearly says
      earnings beat / guidance / HBM / AI demand / downgrade / export control.
    - Macro / Policy / Geo headlines are risk context, not direct price calls;
      they get smaller scores but higher semantic tags.
    """
    text = str(title or "").lower()
    score = 0.0
    _, analyst_action = classify_analyst_headline(text)
    # Target/rating changes enter the dedicated price/flow confirmation engine.
    if bucket != "daily" and analyst_action:
        return 0.0, f"us_company_analyst_target_{analyst_action}"
    pos = sum(1 for k in _US_BULL_TERMS if k.lower() in text)
    neg = sum(1 for k in _US_BEAR_TERMS if k.lower() in text)
    ai_semi = sum(1 for k in _US_AI_SEMI_TERMS if k.lower() in text)
    earnings = sum(1 for k in _US_EARNINGS_TERMS if k.lower() in text)
    policy = any(k in text for k in _US_POLICY_GEO_TERMS)
    macro = any(k in text for k in _US_MACRO_TERMS)

    # Direct company / industry evidence.
    score += min(0.22, pos * 0.075)
    score -= min(0.22, neg * 0.075)
    forward_negative = (
        "guidance cut", "cuts guidance", "lowered guidance", "weak guidance", "soft guidance",
        "underwhelming guidance", "guidance disappoints", "guidance falls short", "tepid guidance",
        "lower outlook", "weak outlook", "soft outlook", "lack of a stronger outlook", "not enough",
        "below estimates", "misses estimates", "demand slowdown", "order cancellation",
        "demand slows", "inventory build", "margin decline", "price target cut", "downgrade",
        "offering", "capital raise", "global depositary shares",
        "global depositary receipts", "gds", "gdr", "dilution",
        "investigation", "probe",
    )
    forward_hits = sum(1 for term in forward_negative if term in text)
    forward_positive = (
        "raises guidance", "raised guidance", "guidance raised", "boosts outlook",
        "strong outlook", "guides above", "above consensus",
    )
    forward_positive_hits = sum(1 for term in forward_positive if term in text)
    if forward_hits:
        score -= min(0.18, 0.075 * forward_hits)
    if ai_semi and bucket in {"company", "industry"}:
        score += min(0.07, ai_semi * 0.018)
    if earnings and bucket == "company":
        score += 0.025 if score >= 0 else 0.0
    if forward_hits:
        # Forward risk wins over a backward-looking beat in the headline.  It
        # triggers reassessment; price/flow confirmation still decides Direction.
        score = min(score, -0.08)

    # Market weather is less directional; use it to flag risk / context.
    if bucket == "daily":
        if policy:
            score -= 0.055
        if macro:
            score += 0.0
    elif policy:
        score -= 0.035

    if bucket == "daily":
        if policy:
            tag = "daily_headline_policy_geo"
        elif macro:
            tag = "daily_headline_macro"
        elif ai_semi:
            tag = "daily_headline_ai_semis"
        else:
            tag = "daily_headline"
    elif bucket == "industry":
        if ai_semi:
            tag = "us_industry_ai_semis"
        elif policy:
            tag = "us_industry_policy_geo"
        else:
            tag = "us_industry_news"
    else:
        if earnings and forward_hits:
            tag = "us_company_earnings_forward_risk"
        elif earnings and forward_positive_hits:
            tag = "us_company_earnings_forward_raise"
        elif earnings:
            tag = "us_company_earnings"
        elif ai_semi:
            tag = "us_company_ai_semis"
        elif policy:
            tag = "us_company_policy_geo"
        else:
            tag = "us_company_news"

    if bucket == "company":
        tag += f"|catalyst_{_us_catalyst_family(title)}"

    if abs(score) >= 0.06:
        tag = ("bullish_" if score > 0 else "bearish_") + tag
    return round(max(-0.32, min(0.32, score)), 4), tag

def _google_news_us(query: str, bucket: str, limit: int = 4) -> List[NewsItem]:
    # Add the active year + time operators so Google News avoids stale archives.
    current_year = max(2026, datetime.now(ZoneInfo("UTC")).year)
    if bucket == "daily":
        q = f"({query}) {current_year} after:{current_year}-01-01 when:14d"
    else:
        q = f"({query}) {current_year} after:{current_year}-01-01 when:60d"
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    items: List[NewsItem] = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 TINO-RC24-US-News"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            xml = resp.read(180000)
        root = ET.fromstring(xml)
        for node in root.findall(".//item")[: max(limit * 2, limit)]:
            title = re.sub(r"\s+", " ", node.findtext("title") or "").strip()
            link = node.findtext("link") or "https://news.google.com/"
            pub = node.findtext("pubDate") or ""
            source_node = node.find("source")
            publisher = re.sub(r"\s+", " ", source_node.text or "").strip() if source_node is not None else ""
            if not title or not _us_news_recent_enough(pub, bucket):
                continue
            score, tag = _score_us_news(title, bucket)
            source_tier, multiplier = _us_source_authority(publisher)
            if bucket != "daily":
                score = round(score * multiplier, 4)
                tag += f"|{source_tier}"
            provenance = resolve_aggregator_timestamp(pub, link)
            items.append(NewsItem(
                f"GoogleNewsUS/{publisher}" if publisher else "GoogleNewsUS",
                provenance.display_time,
                guarded_score(score, provenance),
                append_provenance_tag(tag, provenance),
                title,
                provenance.publisher_url or link,
            ))
            if len(items) >= limit:
                break
    except Exception:
        return []
    return items


def fetch_us_news(ticker: TickerInfo, force_refresh: bool = False) -> List[NewsItem]:
    sym = str(ticker.resolved_symbol or ticker.symbol or "").upper()
    cache_key = f"v1104_us_catalyst_first:{sym}:{date.today().isoformat()}"
    now_ts = time.time()
    cached = _US_NEWS_CACHE.get(cache_key)
    if not force_refresh and cached and now_ts - cached[0] < _US_NEWS_CACHE_TTL_SEC:
        # Revalidate old in-process cache entries after a code deploy; the
        # search route alone is never proof that a title belongs to the stock.
        return _filter_us_news_for_ticker(ticker, list(cached[1]))

    base = _us_company_base_name(ticker)
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        out = [
            NewsItem("GoogleNewsUS", "sample", -0.06, "daily_headline_macro", "US stock market Fed CPI NFP 2026 watch", "https://news.google.com/"),
            NewsItem("GoogleNewsUS", "sample", 0.10, "bullish_us_company_earnings", f"{base} earnings and guidance 2026 watch", "https://news.google.com/"),
        ]
        _US_NEWS_CACHE[cache_key] = (now_ts, out)
        return out

    out: List[NewsItem] = []
    seen: set[str] = set()

    def _add(item: NewsItem) -> bool:
        key = re.sub(r"[^a-z0-9]+", " ", item.title.lower()).strip()[:120]
        if not key or key in seen:
            return False
        seen.add(key)
        out.append(item)
        return True

    # Daily market weather applies to all US tickers. Fetch first and reserve slots
    # so a busy ticker will not starve global Macro/Policy headlines.
    for query in _DAILY_HEADLINE_QUERIES:
        # Two rows per route keeps latency/bandwidth bounded while guaranteeing
        # Macro, Geo and Policy each receive a reserved search attempt.
        for item in _google_news_us(query, "daily", limit=2):
            if _us_news_relevant_to_ticker(ticker, item, "daily"):
                _add(item)

    for query, bucket, limit in _us_news_profile_queries(ticker):
        for item in _google_news_us(query, bucket, limit=limit):
            if _us_news_relevant_to_ticker(ticker, item, bucket):
                _add(item)
        if len(out) >= 14:
            break

    # Direct company catalysts outrank generic industry and market weather. A
    # neutral official demo must not vanish behind larger macro scores.
    def _priority(item: NewsItem):
        tag = str(item.tag or "").lower()
        bucket_priority = 3 if "us_company" in tag else 2 if "us_industry" in tag else 1
        source_priority = 3 if "source_tier1" in tag else 2 if "source_tier2" in tag else 1
        return bucket_priority, source_priority, abs(float(item.score)), str(item.time)

    out = sorted(out, key=_priority, reverse=True)[:14]

    # Guarantee the evidence layer is never empty, but make fallback neutral and explicit.
    if not out:
        out = [NewsItem("GoogleNewsUS", "latest", 0.0, "us_company_news_wait", f"{base} 2026 English news syncing; use Macro Core / VWAP until updated", "https://news.google.com/")]
    _US_NEWS_CACHE[cache_key] = (now_ts, out)
    return out
