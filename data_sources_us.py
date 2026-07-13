# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, timedelta, datetime, time as dtime
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
try:
    from analyst_event_intelligence import classify_analyst_headline
except Exception:
    # Optional RC4.6 enrichment must never block the stable price/news pipeline.
    def classify_analyst_headline(value):
        return "", ""
from truth_guard import make_truth, parse_date_safe
from quantum_market_context import fetch_market_proxy_context
from macro_event_calendar import build_macro_context

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
        "nextEarningsDate": "2026-08-27",
        "earningsDays": 59,
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
        "nextEarningsDate": "2026-09-23",
        "earningsDays": 86,
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
        "nextEarningsDate": "2026-08-12",
        "earningsDays": 44,
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
    try:
        import yfinance as yf
        info = dict(yf.Ticker(symbol).get_info() or {})
    except Exception:
        try:
            import yfinance as yf
            info = dict(yf.Ticker(symbol).info or {})
        except Exception:
            info = {}
    return _merge_public_memory(symbol, info)

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
        h = tk.history(period="5d", interval="1m", prepost=True, auto_adjust=False, timeout=8)
        if h is None or h.empty:
            h = tk.history(period="5d", interval="5m", prepost=True, auto_adjust=False, timeout=8)
        if h is None or h.empty:
            return out
        h = h.dropna(subset=["Close"])
        if h.empty:
            return out
        last_row = h.iloc[-1]
        last_ts = h.index[-1]
        last = float(last_row["Close"])
        prev = float(previous_close or last)
        chg = last - prev
        chgp = (chg / prev * 100.0) if prev else 0.0
        # Use current NY date rows for extended/intraday high-low-volume snapshot.
        try:
            idx_ny = h.index.tz_convert("America/New_York") if getattr(h.index, "tz", None) is not None else h.index.tz_localize("America/New_York")
            today_mask = idx_ny.date == datetime.now(ZoneInfo("America/New_York")).date()
            day_h = h.loc[today_mask] if any(today_mask) else h.tail(390)
        except Exception:
            day_h = h.tail(390)
        high = float(day_h["High"].dropna().max()) if "High" in day_h and not day_h["High"].dropna().empty else last
        low = float(day_h["Low"].dropna().min()) if "Low" in day_h and not day_h["Low"].dropna().empty else last
        vol = float(day_h["Volume"].fillna(0).sum()) if "Volume" in day_h else 0.0
        vwap = last
        try:
            pv = (day_h["Close"].astype(float) * day_h["Volume"].fillna(0).astype(float)).sum()
            vv = day_h["Volume"].fillna(0).astype(float).sum()
            if vv > 0:
                vwap = float(pv / vv)
        except Exception:
            pass
        out.update({
            "accepted": True,
            "source": "YahooFinance_1m_PrePost",
            "last": round(last, 4),
            "high": round(high, 4),
            "low": round(max(low, 0.01), 4),
            "volume": int(vol) if vol else None,
            "vwap": round(vwap, 4),
            "change": round(chg, 4),
            "change_pct": round(chgp, 2),
            "timestamp": _safe_ts_label(last_ts),
            "status": status,
            "label": _us_session_label(status),
        })
    except Exception as exc:
        out["error"] = type(exc).__name__
    return out

def _fallback_price(ticker: TickerInfo, reason: str) -> PriceFrame:
    b = US_SAMPLE.get(ticker.resolved_symbol, dict(open=50, high=52, low=48, last=50, previous_close=50, volume=1000000, vwap=50, atr14=2.5))
    last = b["last"]
    closes = [round(last * (1 + math.sin(i/5)*0.025), 2) for i in range(60,0,-1)]
    highs = [round(x + b["atr14"]*0.4, 2) for x in closes]
    lows = [round(max(x - b["atr14"]*0.4, 0.01), 2) for x in closes]
    vols = [b["volume"] * (0.7 + i/140) for i in range(60,0,-1)]
    closes[-1] = b["last"]; highs[-1] = b["high"]; lows[-1] = b["low"]; vols[-1] = b["volume"]
    d = (date.today() - timedelta(days=1)).isoformat()
    info=_get_us_info(ticker.resolved_symbol)
    short=_us_short_context(ticker.resolved_symbol, info, b["last"], b["low"], b["high"], b["atr14"])
    short["date"]=d
    persona=_sector_persona(ticker.resolved_symbol, info)
    ctx={"macro":_us_macro_context(d),"short":short,"persona":persona,"fundamental":_us_fundamental_context(ticker.resolved_symbol, info, d),"inst":{"accepted":False,"source":"US","date":d},"margin":{"accepted":False,"source":"US","date":d}}
    return PriceFrame(ticker, make_truth("US_PRICE_SAMPLE", d, True, True, reason, "fallback_reference"), b["open"], b["high"], b["low"], b["last"], b["previous_close"], b["volume"], b["vwap"], b["atr14"], closes, highs, lows, vols, d, _us_market_status_now(), ctx)



def _us_fundamental_context(symbol: str, info: Dict[str, object], price_date: str) -> Dict[str, object]:
    q = info.get('fiscalQuarterLabel') or info.get('mostRecentQuarter') or info.get('lastFiscalYearEnd') or '最新財報'
    eps = _clean_num(info.get('trailingEps'), None)
    rev = _clean_num(info.get('totalRevenue'), None)
    qgrowth = _clean_num(info.get('earningsQuarterlyGrowth'), None)
    rgrowth = _clean_num(info.get('revenueGrowth'), None)
    pe = _clean_num(info.get('trailingPE'), None)
    next_date = info.get('nextEarningsDate') or info.get('earningsDate') or ''
    days = _clean_num(info.get('earningsDays'), None)
    return {
        'accepted': bool(eps is not None or rev is not None), 'source':'YahooFinance quoteSummary + V9 public memory', 'date': price_date,
        'quarter': str(q or '最新財報'), 'eps': eps, 'revenue': rev,
        'qoq': qgrowth*100 if qgrowth is not None and abs(qgrowth) < 5 else qgrowth,
        'yoy': rgrowth*100 if rgrowth is not None and abs(rgrowth) < 5 else rgrowth,
        'pe': pe, 'next_earnings': str(next_date or ''), 'earnings_days': int(days) if days is not None else None,
    }

def fetch_us_price(ticker: TickerInfo) -> PriceFrame:
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return _fallback_price(ticker, "offline smoke test fallback")
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker.resolved_symbol).history(period="6mo", interval="1d", auto_adjust=False, timeout=8)
        if hist is None or hist.empty or len(hist) < 3:
            return _fallback_price(ticker, "yfinance 無資料，使用美股樣本方向參考")
        hist = hist.dropna(subset=["Open", "High", "Low", "Close"])
        last = hist.iloc[-1]; prev = hist.iloc[-2]
        regular_close = float(last["Close"])
        previous_close = float(prev["Close"])
        status = _us_market_status_now()
        ext = _fetch_us_extended_quote(ticker.resolved_symbol, previous_close, status)
        # Formal daily K remains the backbone for trend / MA / regular-session validation.
        # During pre-market / after-hours / intraday, use extended snapshot only for current tactical price.
        live_last = float(ext.get("last") or regular_close) if ext.get("accepted") else regular_close
        live_high = max(float(last["High"]), float(ext.get("high") or regular_close)) if ext.get("accepted") else float(last["High"])
        live_low = min(float(last["Low"]), float(ext.get("low") or regular_close)) if ext.get("accepted") else float(last["Low"])
        live_volume = float(ext.get("volume") or last.get("Volume", 0) or 0) if ext.get("accepted") else float(last.get("Volume",0) or 0)
        live_vwap = float(ext.get("vwap") or ((live_high + live_low + live_last) / 3.0)) if ext.get("accepted") else (float(last["High"])+float(last["Low"])+regular_close)/3
        tr = pd.concat([(hist["High"]-hist["Low"]).abs(), (hist["High"]-hist["Close"].shift()).abs(), (hist["Low"]-hist["Close"].shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else max(regular_close*0.04, .01)
        d = parse_date_safe(hist.index[-1].date().isoformat())
        info = _get_us_info(ticker.resolved_symbol)
        short = _us_short_context(ticker.resolved_symbol, info, live_last, live_low, live_high, atr)
        short["date"] = d
        price_meta = {
            "label": ext.get("timestamp") or "",
            "source": ext.get("source") if ext.get("accepted") else "YahooFinance_Daily",
            "session": status,
            "session_label": ext.get("label") or _us_session_label(status),
            "regular_close": round(regular_close, 4),
            "previous_close": round(previous_close, 4),
            "extended_accepted": bool(ext.get("accepted")),
        }
        ctx = {
            "macro": _us_macro_context(d),
            "short": short,
            "persona": _sector_persona(ticker.resolved_symbol, info),
            "fundamental": _us_fundamental_context(ticker.resolved_symbol, info, d),
            "inst": {"accepted":False,"source":"US","date":d},
            "margin": {"accepted":False,"source":"US","date":d},
            "us_session": ext,
            "price_meta": price_meta,
        }
        truth_source = "YahooFinance_1m_PrePost" if ext.get("accepted") else "YahooFinance"
        truth_reason = f"{_us_session_label(status)}價格快照｜正式日K保留" if ext.get("accepted") else "價格最新｜日K"
        return PriceFrame(ticker, make_truth(truth_source, d, False, True, truth_reason, "latest"), float(last["Open"]), live_high, live_low, live_last, previous_close, live_volume, live_vwap, atr, [float(x) for x in hist["Close"].tail(60)], [float(x) for x in hist["High"].tail(60)], [float(x) for x in hist["Low"].tail(60)], [float(x) for x in hist["Volume"].tail(60)], d, status, ctx)
    except Exception as exc:
        return _fallback_price(ticker, f"資料源錯誤：{type(exc).__name__}")



# --- RC24 US News Query Router / Time Engine / Daily Headline -----------------
_US_NEWS_CACHE: Dict[str, Tuple[float, List[NewsItem]]] = {}
_US_NEWS_CACHE_TTL_SEC = 30 * 60

_US_QUERY_PROFILES: Dict[str, Dict[str, List[str]]] = {
    "MU": {
        "company": ["Micron Technology earnings", "Micron HBM", "Micron DRAM NAND", "Micron guidance"],
        "industry": ["HBM memory pricing", "DRAM NAND memory market", "AI memory demand"],
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
    "guidance cut", "probe", "investigation", "ban", "tariff", "export control", "falls", "fell",
    "drops", "dropped", "plunges", "plunge", "selloff", "lawsuit", "warns", "warning", "delay",
]
_US_AI_SEMI_TERMS = [
    "ai", "hbm", "dram", "nand", "memory", "blackwell", "rubin", "gpu", "semiconductor",
    "chip", "chips", "custom silicon", "asic", "data center", "accelerator", "cowoS", "tsmc",
]
_US_EARNINGS_TERMS = ["earnings", "revenue", "guidance", "q1", "q2", "q3", "q4", "quarter", "outlook"]

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


def _us_news_profile_queries(ticker: TickerInfo) -> List[Tuple[str, str, int]]:
    sym = str(ticker.resolved_symbol or ticker.symbol or "").upper()
    base = _us_company_base_name(ticker)
    prof = _US_QUERY_PROFILES.get(sym, {})
    queries: List[Tuple[str, str, int]] = []
    # Keep query count bounded for speed. Company / industry news is fetched
    # after Daily Headline so global market weather never gets starved by a
    # very active ticker such as MU/NVDA.
    company = list(prof.get("company") or [f"{base} earnings", f"{base} stock", f"{base} guidance"])
    industry = prof.get("industry") or []
    peers = prof.get("peers") or []
    analyst_query = f"{base} price target Morgan Stanley JPMorgan analyst rating"
    # Keep only three company calls for speed: two operating/company routes plus
    # one dedicated analyst route so target changes cannot be starved.
    company_queries = company[:2]
    if not any("price target" in str(q).lower() or "analyst" in str(q).lower() for q in company_queries):
        company_queries.append(analyst_query)
    else:
        company_queries.extend(company[2:3])
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
    if ai_semi and bucket in {"company", "industry"}:
        score += min(0.07, ai_semi * 0.018)
    if earnings and bucket == "company":
        score += 0.025 if score >= 0 else 0.0

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
        if earnings:
            tag = "us_company_earnings"
        elif ai_semi:
            tag = "us_company_ai_semis"
        elif policy:
            tag = "us_company_policy_geo"
        else:
            tag = "us_company_news"

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
            if not title or not _us_news_recent_enough(pub, bucket):
                continue
            score, tag = _score_us_news(title, bucket)
            items.append(NewsItem("GoogleNewsUS", _us_news_time_label(pub), score, tag, title, link))
            if len(items) >= limit:
                break
    except Exception:
        return []
    return items


def fetch_us_news(ticker: TickerInfo) -> List[NewsItem]:
    sym = str(ticker.resolved_symbol or ticker.symbol or "").upper()
    cache_key = f"rc24_us_news_v3_score_time:{sym}:{date.today().isoformat()}"
    now_ts = time.time()
    cached = _US_NEWS_CACHE.get(cache_key)
    if cached and now_ts - cached[0] < _US_NEWS_CACHE_TTL_SEC:
        return list(cached[1])

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
            _add(item)

    for query, bucket, limit in _us_news_profile_queries(ticker):
        for item in _google_news_us(query, bucket, limit=limit):
            _add(item)
        if len(out) >= 14:
            break

    # Sort high-impact evidence first while keeping recency already enforced.
    out = sorted(out, key=lambda n: (abs(float(n.score)), str(n.time)), reverse=True)[:14]

    # Guarantee the evidence layer is never empty, but make fallback neutral and explicit.
    if not out:
        out = [NewsItem("GoogleNewsUS", "latest", 0.0, "us_company_news_wait", f"{base} 2026 English news syncing; use Macro Core / VWAP until updated", "https://news.google.com/")]
    _US_NEWS_CACHE[cache_key] = (now_ts, out)
    return out
