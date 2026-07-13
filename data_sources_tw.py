# -*- coding: utf-8 -*-
from __future__ import annotations
from datetime import date, datetime, time, timedelta
import hashlib
import json
import math
import os
import re
import urllib.parse
import time as time_module
import email.utils
import xml.etree.ElementTree as ET
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo
import pandas as pd
from models import NewsItem, PriceFrame, TickerInfo
from truth_guard import make_truth, parse_date_safe, today_taipei_date, validate_official_block
from data_sources_tw_live_price import fetch_twse_mis_live_price, fetch_google_finance_reference
from foreign_flow_predicto import predict_foreign_flow_v2
from data_sources_tw_yahoo_chip import fetch_yahoo_institutional, fetch_yahoo_margin
from quantum_market_context import fetch_market_proxy_context
from macro_event_calendar import build_macro_context
TW_SAMPLE = {"6770.TW": dict(open=83.20, high=85.20, low=78.10, last=78.30, previous_close=83.20, volume=86000, vwap=80.53, atr14=5.99), "6586.TWO": dict(open=123.50, high=130.50, low=114.00, last=126.00, previous_close=120.50, volume=4200, vwap=123.50, atr14=9.50), "2454.TW": dict(open=4055.0, high=4145.0, low=4025.0, last=4055.0, previous_close=4100.0, volume=6800, vwap=4075.0, atr14=145.0), "2337.TW": dict(open=158.0, high=161.5, low=154.5, last=156.0, previous_close=159.0, volume=15000, vwap=157.8, atr14=7.3), "2308.TW": dict(open=1815.0, high=1855.0, low=1785.0, last=1810.0, previous_close=1835.0, volume=12200, vwap=1816.7, atr14=65.0), "00919.TW": dict(open=23.25, high=23.35, low=23.10, last=23.18, previous_close=23.22, volume=50000, vwap=23.21, atr14=0.24), "5469.TW": dict(open=90.8, high=94.0, low=90.0, last=91.8, previous_close=87.4, volume=19000, vwap=91.93, atr14=4.2)}
BULL = ["獲利", "成長", "EPS", "營收", "買超", "創高", "法說", "AI", "訂單", "擴產", "回補", "強勢", "漲"]
BEAR = ["虧損", "減損", "賣超", "下修", "衰退", "跌", "處置", "警示", "庫存", "法說虧損", "利空"]

_TW_GLOBAL_NEWS_CACHE: tuple[float, List[NewsItem]] | None = None
_TW_GLOBAL_NEWS_CACHE_TTL_SEC = 15 * 60

def _code(symbol: str) -> str:
    return symbol.split(".")[0]
def _num(symbol: str) -> int:
    h = hashlib.sha256(symbol.encode("utf-8")).hexdigest()
    return int(h[:8], 16) + sum(ord(c) for c in _code(symbol))
def _series_from_sample(base: Dict[str, float], symbol: str) -> Dict[str, List[float]]:
    seed = _num(symbol) % 997
    last = float(base["last"])
    closes, highs, lows, vols = [], [], [], []
    slope = ((seed % 31) - 15) / 10000.0
    phase = (seed % 17) / 3.0
    for i in range(60, 0, -1):
        drift = math.sin(i / 4.7 + phase) * 0.018 + (i - 30) * slope
        close = max(0.01, last * (1 + drift))
        spread = max(float(base["atr14"]) * (0.25 + (seed % 7) / 50), last * 0.004)
        closes.append(round(close, 2))
        highs.append(round(close + spread, 2))
        lows.append(round(max(close - spread, 0.01), 2))
        vols.append(float(base["volume"]) * (0.65 + ((seed + i) % 40) / 100.0))
    closes[-1], highs[-1], lows[-1], vols[-1] = float(base["last"]), float(base["high"]), float(base["low"]), float(base["volume"])
    return {"closes": closes, "highs": highs, "lows": lows, "volumes": vols}
def _empty_inst(price_date: str, reason: str = "official institutional fetch failed") -> Dict[str, object]:
    # Main UI must hide this block. Admin/Trace may inspect source/reason.
    return {
        "foreign": None, "foreign_3": None, "foreign_5": None, "foreign_10": None, "foreign_streak": "",
        "trust": None, "trust_3": None, "trust_5": None, "trust_10": None, "trust_streak": "",
        "dealer": None, "dealer_3": None, "dealer_5": None, "dealer_10": None, "dealer_streak": "",
        "source": "OFFICIAL_FETCH_FAILED", "date": "", "accepted": False, "reason": reason, "hide_frontend": True,
    }
def _empty_margin(price_date: str, reason: str = "official margin fetch failed") -> Dict[str, object]:
    # Main UI must hide this block. Admin/Trace may inspect source/reason.
    return {
        "margin": None, "margin_3": None, "margin_5": None, "margin_10": None, "margin_streak": "",
        "short": None, "short_3": None, "short_5": None, "short_10": None, "short_streak": "",
        "ratio": None, "source": "OFFICIAL_FETCH_FAILED", "date": "", "accepted": False, "reason": reason, "hide_frontend": True,
    }
def _proxy_context(symbol: str, price_date: str, closes: List[float], last: float, vwap: float) -> Dict[str, object]:
    seed = _num(symbol)
    trend5 = (closes[-1] - closes[-6]) / closes[-6] if len(closes) >= 6 and closes[-6] else 0.0
    under_vwap = last < vwap
    base = ((seed % 24000) - 12000)
    foreign = int(base * (1.25 if under_vwap else 0.55) + trend5 * 240000)
    trust = int(((seed // 7) % 7000 - 2500) + max(trend5, -0.03) * 60000)
    dealer = int(((seed // 13) % 9000 - 4500) + trend5 * 45000)
    margin = int(((seed // 19) % 16000 - 8000) + (-5500 if under_vwap else 2800))
    short = int(((seed // 29) % 3600 - 1600) + (900 if under_vwap else -600))
    ratio = max(0.05, min(18.0, abs(short) / max(abs(margin), 1) * 9.0 + (seed % 70) / 25.0))
    cover_rate = int(max(0, min(100, 62 + (-trend5 * 600) + (12 if under_vwap else -18) + (seed % 17))))
    bal3 = int(-abs(short) * (8 + seed % 13)) if cover_rate >= 60 else int(abs(short) * (5 + seed % 9))
    bal5 = int(bal3 * (1.4 + (seed % 5) / 10))
    bal10 = int(bal3 * (2.1 + (seed % 7) / 10))
    borrow3 = max(0, int(abs(short) * ((seed % 4) / 10)))
    borrow5 = max(0, int(borrow3 * 1.6))
    borrow10 = max(0, int(borrow3 * 2.4))
    return {
        "foreign": foreign, "trust": trust, "dealer": dealer, "margin": margin, "short": short, "ratio": ratio,
        "borrow_sell_3": borrow3, "borrow_sell_5": borrow5, "borrow_sell_10": borrow10,
        "balance_delta_3": bal3, "balance_delta_5": bal5, "balance_delta_10": bal10,
        "cover_rate": cover_rate, "risk": "低" if cover_rate >= 70 else ("中" if cover_rate >= 40 else "高"),
        "source": "V12_DERIVED_PROXY", "date": price_date, "accepted": False,
        "reason": "proxy only; hidden from official institutional/margin rows",
    }
def _tv_pressure_wait(price_date: str, reason: str = "待接 V9 匯率差公式") -> Dict[str, object]:
    return {
        "direction": "TV外資買賣壓",
        "amount_billion": None,
        "level": "待同步",
        "alert": "待接 V9 匯率差公式",
        "stock_fire": "待判讀",
        "confidence": 0,
        "source": "WAIT_V9_FX_DIFF_FORMULA",
        "date": price_date,
        "accepted": False,
        "reason": reason,
    }
def _latest_two_or_three_closes(symbol: str, period: str = "7d") -> Tuple[List[float], str]:
    import yfinance as yf
    hist = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=False, timeout=8)
    if hist is None or hist.empty or "Close" not in hist:
        return [], ""
    closes = [float(x) for x in hist["Close"].dropna().tail(3)]
    d = parse_date_safe(hist.dropna(subset=["Close"]).index[-1].date().isoformat()) if closes else ""
    return closes, d
def _stock_fire_tag(last: float, prev_close: float, vwap: float, inst: Dict[str, object] | None = None) -> str:
    inst = inst or {}
    formal_inst = bool(inst.get("accepted", False))
    foreign = _to_int(inst.get("foreign")) if formal_inst else None
    price_up = last >= prev_close
    above_vwap = last >= vwap
    if price_up and above_vwap and (foreign is None or foreign >= 0):
        return "主力點火"
    if price_up and (not above_vwap or (foreign is not None and foreign < 0)):
        return "誘多出貨"
    if not above_vwap:
        return "主力熄火"
    return "主力觀察"


# Foreign Flow Snapshot Lock
# --------------------------
# Streamlit reruns frequently. The V9/TV FX-difference model is a market-wide
# pressure snapshot, not a broker feed, so it must not refetch and jump every
# rerun. During market hours it refreshes by a 5-minute bucket; after close it
# freezes for the trading date inside this Python process.
_FOREIGN_FLOW_SNAPSHOT_CACHE: Dict[str, Dict[str, object]] = {}
_FOREIGN_FLOW_BUCKET_SECONDS = 300
_FOREIGN_FLOW_CACHE_FILE = os.path.join(os.path.dirname(__file__), ".tino_foreign_flow_snapshot_cache.json")
_FOREIGN_FLOW_CACHE_LOADED = False


def _foreign_flow_load_disk_cache() -> None:
    """Load persistent foreign-flow snapshots once per process.

    Streamlit Cloud may recreate the Python process between user interactions.
    The V9 FX-difference number is a market pressure snapshot, not a tick feed,
    so after the first accepted snapshot for a bucket/close session we persist it
    locally and reuse it instead of refetching FX quotes that can revise.
    """
    global _FOREIGN_FLOW_CACHE_LOADED
    if _FOREIGN_FLOW_CACHE_LOADED:
        return
    _FOREIGN_FLOW_CACHE_LOADED = True
    try:
        with open(_FOREIGN_FLOW_CACHE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(k, str) and isinstance(v, dict):
                    _FOREIGN_FLOW_SNAPSHOT_CACHE[k] = v
    except Exception:
        pass


def _foreign_flow_save_disk_cache() -> None:
    try:
        # Keep only the latest 40 snapshots to avoid unbounded local growth.
        items = list(_FOREIGN_FLOW_SNAPSHOT_CACHE.items())[-40:]
        tmp = _FOREIGN_FLOW_CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(dict(items), fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, _FOREIGN_FLOW_CACHE_FILE)
    except Exception:
        pass


def _foreign_flow_snapshot_key(price_date: str) -> str:
    try:
        now = datetime.now(ZoneInfo("Asia/Taipei"))
        trade_day = str(today_taipei_date() if now.time() >= time(13, 30) else (price_date or today_taipei_date()))
        mode = _tw_market_status(trade_day)
        # Foreign Flow is market-wide.  After close, one trading day must have
        # exactly one snapshot shared by every queried stock.
        if mode == "intraday" and now.time() < time(13, 30):
            bucket = int(now.timestamp() // _FOREIGN_FLOW_BUCKET_SECONDS)
            return f"{trade_day}:intraday:{bucket}"
        return f"{trade_day}:close_locked"
    except Exception:
        return f"{price_date}:close_locked"


def _tv_market_pressure_snapshot(price_date: str) -> Dict[str, object]:
    _foreign_flow_load_disk_cache()
    key = _foreign_flow_snapshot_key(price_date)
    cached = _FOREIGN_FLOW_SNAPSHOT_CACHE.get(key)
    if isinstance(cached, dict) and cached:
        snap = dict(cached)
        snap["snapshot_hit"] = True
        snap["reason"] = f"{snap.get('reason','匯率差公式')}｜SnapshotLock"
        return snap

    fx, fx_date = _latest_two_or_three_closes("USDTWD=X", "10d")
    fx_symbol = "USDTWD=X"
    if len(fx) < 3:
        fx, fx_date = _latest_two_or_three_closes("TWD=X", "10d")
        fx_symbol = "TWD=X"
    taiex, _ = _latest_two_or_three_closes("^TWII", "10d")
    if len(fx) < 3 or len(taiex) < 3:
        return _tv_pressure_wait(price_date, "匯率/大盤資料待同步｜不顯示假數字")

    fp_unit_ntd_100m = 125.0
    fp_neutral_gate = 50.0
    fp_trend_gate = 150.0
    fx_prev2, fx_prev, fx_now = fx[-3], fx[-2], fx[-1]
    tx_prev2, tx_prev, tx_now = taiex[-3], taiex[-2], taiex[-1]
    fp_base_signed = -((fx_now - fx_prev) / 0.01) * fp_unit_ntd_100m
    fp_prev_base_signed = -((fx_prev - fx_prev2) / 0.01) * fp_unit_ntd_100m
    taiex_pct = (tx_now - tx_prev) / tx_prev * 100.0 if tx_prev else 0.0
    taiex_pct_prev = (tx_prev - tx_prev2) / tx_prev2 * 100.0 if tx_prev2 else 0.0
    base_sell = fp_base_signed < -fp_neutral_gate
    prev_base_sell = fp_prev_base_signed < -fp_neutral_gate

    def boost(is_sell: bool, pct: float) -> float:
        if not is_sell:
            return 1.0
        if pct <= -2.5:
            return 4.25
        if pct <= -1.5:
            return 3.20
        if pct <= -0.8:
            return 2.10
        return 1.0

    fp_boost = boost(base_sell, taiex_pct)
    fp_prev_boost = boost(prev_base_sell, taiex_pct_prev)
    signed = fp_base_signed * fp_boost
    prev_signed = fp_prev_base_signed * fp_prev_boost
    amt = abs(signed)
    prev_amt = abs(prev_signed)
    if signed < -fp_neutral_gate:
        dir_word = "賣壓"
        direction = "預估大盤外資賣壓"
        level = "逃命" if amt >= 1000 else "高壓" if amt >= 600 else "撤退" if amt >= 300 else "警戒"
    elif signed > fp_neutral_gate:
        dir_word = "買盤"
        direction = "預估大盤外資買盤"
        level = "強回流" if amt >= 600 else "回流" if amt >= 300 else "小回補"
    else:
        dir_word = "中性"
        direction = "預估大盤外資中性"
        level = "中性"

    same_dir = (signed < -fp_neutral_gate and prev_signed < -fp_neutral_gate) or (signed > fp_neutral_gate and prev_signed > fp_neutral_gate)
    trend_delta = amt - (prev_amt if same_dir else 0.0)
    market_crash = signed < -fp_neutral_gate and fp_boost >= 2.10
    if amt < fp_neutral_gate:
        trend = ""
    elif market_crash:
        trend = "市場急殺"
    elif not same_dir:
        trend = "新訊號"
    elif trend_delta > fp_trend_gate:
        trend = "擴大" if dir_word == "買盤" else "放大"
    elif trend_delta < -fp_trend_gate:
        trend = "縮小"
    else:
        trend = "持平"
    state = level if not trend else f"{level}{trend}"
    snap = {
        "direction": direction,
        "amount_billion": int(round(amt)) if amt >= fp_neutral_gate else "50億內",
        "level": level,
        "alert": state,
        "confidence": 72 if amt >= fp_neutral_gate else 55,
        "source": "V9_TV_FX_DIFF_FORMULA",
        "date": fx_date or price_date,
        "accepted": True,
        "reason": f"匯率差公式｜{fx_symbol} {fx_prev:.4f}->{fx_now:.4f}｜TAIEX {taiex_pct:+.2f}%｜boost {fp_boost:.2f}｜外資壓力快照",
        "snapshot_key": key,
        "snapshot_hit": False,
    }
    _FOREIGN_FLOW_SNAPSHOT_CACHE[key] = dict(snap)
    _foreign_flow_save_disk_cache()
    return snap

def _ff_as_float(value, default: float = 0.0) -> float:
    """Small local parser for foreign-flow calibration; never raises."""
    try:
        if value in (None, "", "None", "nan"):
            return default
        return float(str(value).replace(",", ""))
    except Exception:
        return default


def _ff_contains_sell(direction: str) -> bool:
    text = str(direction)
    return "賣" in text or "撤退" in text or "高壓" in text or "逃命" in text


def _ff_contains_buy(direction: str) -> bool:
    text = str(direction)
    return "買" in text or "回流" in text or "回補" in text


def _ff_level_from_amount(direction: str, amount_billion: float) -> str:
    """Keep V9/TV wording while classifying calibrated simulated pressure."""
    if _ff_contains_sell(direction):
        if amount_billion >= 1000:
            return "逃命"
        if amount_billion >= 600:
            return "高壓"
        if amount_billion >= 300:
            return "撤退"
        if amount_billion >= 50:
            return "警戒"
        return "中性"
    if _ff_contains_buy(direction):
        if amount_billion >= 600:
            return "強回流"
        if amount_billion >= 300:
            return "回流"
        if amount_billion >= 50:
            return "小回補"
        return "中性"
    return "中性"


def _calibrate_foreign_flow_pressure(tv_pressure: Dict[str, object], context: Dict[str, object] | None = None) -> Dict[str, object]:
    """Conservative intraday calibration for V9/TV FX-difference foreign pressure.

    This is NOT an official institutional-investor number and does not fetch data.
    It only lets already-known futures/macro/VWAP pressure lightly adjust the
    simulated foreign-flow amount so the Radar has forward-warning effect.
    """
    tv = dict(tv_pressure or {})
    if not tv.get("accepted"):
        return tv
    raw_amount = tv.get("amount_billion")
    try:
        amount = abs(float(raw_amount))
    except Exception:
        return tv

    ctx = context or {}
    futures = ctx.get("futures", {}) if isinstance(ctx.get("futures", {}), dict) else {}
    macro = ctx.get("macro", {}) if isinstance(ctx.get("macro", {}), dict) else {}
    snap = ctx.get("price_snapshot", {}) if isinstance(ctx.get("price_snapshot", {}), dict) else {}

    direction = str(tv.get("direction", ""))
    sell_mode = _ff_contains_sell(direction)
    buy_mode = _ff_contains_buy(direction)
    multiplier = 1.0
    reasons = []

    # Weighting principle: strong for direction, restrained for amount.
    net_oi = _ff_as_float(futures.get("net_oi"), 0.0)
    delta = _ff_as_float(futures.get("delta"), 0.0)
    if sell_mode and net_oi <= -60000:
        multiplier += 0.06
        reasons.append("外資期貨淨空高檔")
        if delta < 0:
            multiplier += 0.02
            reasons.append("淨空增加")
    elif buy_mode and net_oi >= -25000:
        multiplier += 0.03
        reasons.append("期貨空單壓力較低")

    strength = str(macro.get("strength", ""))
    calendar = str(macro.get("calendar", ""))
    event_score = _ff_as_float(macro.get("event_score"), 0.0)
    if strength == "高" or "NFP" in calendar or "FOMC" in calendar or "CPI" in calendar:
        multiplier += 0.03
        reasons.append("一級宏觀事件前夕")
    if sell_mode and event_score < 0:
        multiplier += 0.02
        reasons.append("事件敘事偏空")

    vwap_state = str(snap.get("vwap_state") or "")
    if not vwap_state and snap:
        last = _ff_as_float(snap.get("last"), 0.0)
        vwap = _ff_as_float(snap.get("vwap"), last)
        vwap_state = "VWAP 上方" if last >= vwap else "VWAP 下方"
    if sell_mode and vwap_state == "VWAP 上方":
        multiplier += 0.03
        reasons.append("VWAP上方但資金撤退")
    elif sell_mode and vwap_state == "VWAP 下方":
        multiplier += 0.02
        reasons.append("VWAP下方賣壓延續")

    # Never make a broker-like claim from a simulation.
    multiplier = max(0.90, min(multiplier, 1.18))
    calibrated_amount = int(round(amount * multiplier))
    level = _ff_level_from_amount(direction, calibrated_amount)

    old_level = str(tv.get("level", ""))
    old_alert = str(tv.get("alert", "壓力觀察"))
    trend_tail = old_alert.replace(old_level, "") if old_level and old_alert.startswith(old_level) else ""
    tv["amount_billion"] = calibrated_amount if calibrated_amount >= 50 else "50億內"
    tv["level"] = level
    tv["alert"] = f"{level}{trend_tail}" if trend_tail else level
    tv["confidence"] = min(86, int(_ff_as_float(tv.get("confidence"), 72) + (multiplier - 1.0) * 45))
    tv["source"] = "V12_FX_FLOW_CALIBRATED_INLINE"
    tv["official"] = False
    tv["model_role"] = "intraday_foreign_flow_simulation"
    base_reason = str(tv.get("reason", ""))
    tv["reason"] = f"{base_reason}｜盤中校正x{multiplier:.2f}：" + ("、".join(reasons) if reasons else "未觸發放大")
    return tv

def _tv_pressure_context(symbol: str, price_date: str, closes: List[float], last: float, vwap: float, proxy: Dict[str, object], *, previous_close: float | None = None, inst: Dict[str, object] | None = None) -> Dict[str, object]:
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return _tv_pressure_wait(price_date, "offline smoke test｜待接 V9 匯率差公式")
    try:
        tv = dict(_tv_market_pressure_snapshot(price_date))
        if not tv.get("accepted"):
            return tv
        prev_close = float(previous_close if previous_close is not None else (closes[-2] if len(closes) >= 2 else last))
        tv["stock_fire"] = _stock_fire_tag(last, prev_close, vwap, inst)
        # Main UI should not expose cache mechanics, but Admin Trace can inspect it.
        tv["model_role"] = "intraday_foreign_flow_simulation"
        tv["official"] = False
        return tv
    except Exception as exc:
        return _tv_pressure_wait(price_date, f"匯率差公式資料待同步：{type(exc).__name__}｜不顯示假數字")


def _attach_price_snapshot(context: Dict[str, object], *, open_price: float, high: float, low: float, last: float, previous_close: float, volume: float, vwap: float, price_time: str = "", price_source: str = "", market_mode: str = "") -> Dict[str, object]:
    """SSOT price snapshot for all V12 panels.

    Right radar, decision card, audit and learning should read this single
    snapshot instead of re-parsing strings or re-fetching price.
    """
    try:
        snap = {
            "last": float(last),
            "open": float(open_price),
            "high": float(high),
            "low": float(low),
            "previous_close": float(previous_close),
            "volume": float(volume or 0),
            "vwap": float(vwap if vwap is not None else last),
            "time": str(price_time or ""),
            "source": str(price_source or ""),
            "mode": str(market_mode or ""),
        }
        snap["vwap_state"] = "VWAP 上方" if snap["last"] >= snap["vwap"] else "VWAP 下方"
        context["price_snapshot"] = snap
    except Exception:
        # Never break the app because of admin/SSOT metadata.
        pass
    return context

def _flow_context(ticker: TickerInfo, price_date: str, closes: List[float], last: float, vwap: float, previous_close: float | None = None) -> Dict[str, object]:
    proxy = _proxy_context(ticker.resolved_symbol, price_date, closes, last, vwap)
    tv_pressure = _tv_pressure_context(ticker.resolved_symbol, price_date, closes, last, vwap, proxy, previous_close=previous_close)
    try:
        # The official calendar is local/lightweight and must exist even when an
        # external price or proxy source is unavailable.  Live enrichment later
        # adds SOX/NQ/VIX without changing the event clock.
        macro_context = build_macro_context(str(price_date or today_taipei_date()))
    except Exception:
        macro_context = {
            "accepted": False,
            "source": "MACRO_CALENDAR_PENDING",
            "calendar": "宏觀事件日曆待重新同步",
            "events": [],
            "event_uncertainty": 0.0,
            "event_risk": 0.0,
            "pre_event_direction": 0.0,
        }
    return {
        "inst": {**_empty_inst(price_date), "symbol": ticker.resolved_symbol},
        "margin": {**_empty_margin(price_date), "symbol": ticker.resolved_symbol},
        "chip_proxy": proxy,
        "tv_pressure": tv_pressure,
        "bsi": {
            "borrow_sell_3": 0, "borrow_sell_5": 0, "borrow_sell_10": 0,
            "balance_delta_3": proxy["balance_delta_3"], "balance_delta_5": proxy["balance_delta_5"], "balance_delta_10": proxy["balance_delta_10"],
            "cover_rate": proxy["cover_rate"], "risk": proxy["risk"],
            "accepted": False, "source": "WAIT_SBL", "date": price_date, "reason": "借券賣出來源未完成，主畫面以價格與資券階梯判讀",
        },
        "macro": macro_context,
        "futures": {"accepted": False, "source": "WAIT_FUTURES", "date": price_date},
        "fundamental": {"month": "最近月", "revenue": None, "mom": None, "yoy": None, "eps": None, "source": "TW_FUNDAMENTAL_PENDING", "accepted": False},
    }
V9_VERIFIED_TW_CONTEXT = {
    "6770.TW": {
        "fundamental": {"month":"2026/05","revenue":"57.70億","mom":"+14.35%","yoy":"+58.86%","accum_revenue":"243.89億","accum_yoy":"+31.41%","eps":"3.36","event_score":"+0.35","strength":"高","event_tags":"EPS 3.36、年增強、EPS 3.36、財報事件、超預期","source":"V9_VERIFIED_MOPS_EPS_MEMORY","accepted":True},
        "bsi": {"borrow_sell_3":0,"borrow_sell_5":0,"borrow_sell_10":0,"balance_delta_3":-100800,"balance_delta_5":-92800,"balance_delta_10":-53600,"cover_rate":100,"risk":"低","accepted":True,"source":"V9_VERIFIED_BSI_MEMORY","date":"2026-06-26","reason":"空方回補啟動，反彈條件改善"},
        "futures": {"accepted":True,"source":"V9_VERIFIED_TAIFEX_MEMORY","date":"2026/06/29","net_oi":-76627,"delta":-236,"settlement":"T-16 2026-07-15","risk_level":"中高50分","summary":"淨空 -76,627口｜日變化 -236口（淨空增加）｜結算壓盤風險"},
        "macro": {"accepted":True,"source":"V9_MACRO_FORWARD_CALENDAR_GUARD","date":"2026-06-29","event_score":0.35,"strength":"高","eps":"EPS 3.36","eps_tags":"年增強、EPS 3.36、財報事件、超預期","calendar":"未來72小時無一級宏觀公布｜下一個一級事件：NFP 07/02 20:30 台灣（倒數3天4小時）｜FOMC利率決議：07/30 02:00 台灣（倒數30天10小時）","sox":-5.3,"nq":0.9,"vix":18.9},
        "inst": {"foreign":-10379,"foreign_3":-15031,"foreign_5":-1674,"foreign_10":-111978,"foreign_streak":"連賣2天","trust":633,"trust_3":3135,"trust_5":3533,"trust_10":7455,"trust_streak":"連買4天","dealer":-2769,"dealer_3":-626,"dealer_5":915,"dealer_10":9958,"dealer_streak":"連賣2天","source":"FinMind_Institutional","date":"2026-06-26","accepted":True,"reason":"法人同步，使用最近有效交易日","symbol":"6770.TW"},
        "margin": {"margin":-8283,"margin_3":6036,"margin_5":2798,"margin_10":20295,"margin_streak":"連減2天","short":-342,"short_3":283,"short_5":850,"short_10":-1797,"short_streak":"連減2天","ratio":2.20,"source":"FinMind_MARGIN","date":"2026-06-26","accepted":True,"reason":"資券同步，使用最近有效交易日"}},
    "6586.TWO": {"fundamental":{"month":"最近月","revenue":"待官方更新","mom":"","yoy":"","eps":"","event_score":"+0.18","strength":"高","event_tags":"題材/事件盤","source":"V9_EVENT_MEMORY","accepted":True},"macro":{"accepted":True,"source":"V9_MACRO_FORWARD_CALENDAR_GUARD","date":"2026-06-29","event_score":0.18,"strength":"高","eps":"EPS/營收事件看深度分析","eps_tags":"題材/事件盤","calendar":"未來72小時無一級宏觀公布｜下一個一級事件：NFP 07/02 20:30 台灣｜FOMC利率決議：07/30 02:00 台灣","sox":-5.3,"nq":0.9,"vix":18.9}},
    "2308.TW": {"fundamental":{"month":"11504","revenue":"586.92億","mom":"-1.82%","yoy":"+43.92%","accum_revenue":"2,180.44億","accum_yoy":"+36.53%","eps":"","event_score":"+0.10","strength":"中","event_tags":"AI/電源/權值基本面","source":"V9_REVENUE_TRUTH_FALLBACK_MOPS_2026_04","accepted":True}}}
def _apply_v9_verified_contract(ticker: TickerInfo, context: Dict[str, object], price_date: str) -> Dict[str, object]:
    mem = V9_VERIFIED_TW_CONTEXT.get(ticker.resolved_symbol)
    if not mem:
        return context
    # Official institution/margin rows are allowed only when they are concrete rows.
    # V9 verified memory may restore real historical rows with explicit dates;
    # it must never create placeholder text on the main dashboard.
    for key, val in mem.items():
        # V20: foreign amount must come from live FX/TAIEX formula, never from fixed memory.
        if key == "foreign_amount":
            continue
        # Do not restore stale market-wide futures/macro memory to the main radar.
        # If today's TAIFEX/macro fetch fails, the row should say pending rather
        # than showing a verified-looking old net-short number.
        if key == "futures":
            try:
                vd = parse_date_safe(str(val.get("date", "")).replace("/", "-"))
                pd_ = parse_date_safe(str(price_date).replace("/", "-"))
                if vd != pd_:
                    continue
            except Exception:
                continue
        if key == "macro":
            continue
        cur = context.get(key, {}) if isinstance(context.get(key, {}), dict) else {}
        if not cur.get("accepted"):
            v = dict(val)
            v.setdefault("date", price_date)
            if key in {"inst", "margin", "bsi", "futures"}:
                v = validate_official_block(v, price_date, {"inst":"三大法人","margin":"資券","bsi":"借券","futures":"外資期貨"}.get(key, key))
            context[key] = v
    return context
def _streak(values: List[int], pos: str, neg: str) -> str:
    if not values:
        return "待同步"
    up = values[-1] >= 0
    count = 1
    for v in reversed(values[:-1]):
        if (v >= 0) != up:
            break
        count += 1
    return f"連{pos if up else neg}{count}天"
def _sum_last(vals: List[int], n: int) -> int:
    return int(sum(vals[-n:])) if vals else 0
def _shares_to_lots(value: int | float | None) -> int:
    if value is None:
        return 0
    try:
        return int(round(float(value) / 1000.0))
    except Exception:
        return 0
def _tw_market_status(latest_price_date: str) -> str:
    phase = _tw_session_phase(datetime.now(ZoneInfo("Asia/Taipei")))
    if phase == "closed":
        return "closed_reference"
    if phase == "pre_market":
        return "pre_market"
    if phase == "intraday":
        return "intraday"
    if phase == "close_confirm":
        return "close_confirm"
    return "after_close"


def _first_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def _macro_forward_context(price_date: str, *, event_score: float = 0.0, eps: str = "", eps_tags: str = "") -> Dict[str, object]:
    """Shared RC4 calendar + observed cross-market context.

    CPI/PPI/NFP/FOMC are kept in one official calendar for both TW and US.
    Before publication they change uncertainty/confidence only; they do not
    receive a permanent bullish or bearish sign.
    """
    out = build_macro_context(
        str(price_date or today_taipei_date()),
        event_score=event_score,
        eps=eps,
        eps_tags=eps_tags,
    )
    # Attach observed overnight/global proxies to the same market-wide context.
    # Missing values stay None and are ignored by the model.
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
    except Exception:
        pass
    return out

def _parse_taifex_foreign_row(df, *, require_product_name: bool = False) -> Dict[str, object] | None:
    """Strictly parse TAIFEX foreign TXF open-interest net contracts.

    Accepted value must be:
    - commodity: 臺股期貨 / 台股期貨 / TXF when the table includes product names
    - identity: 外資
    - section: 未平倉餘額
    - column: 多空淨額, contract count / 口數, not notional amount

    No generic large-number fallback is allowed.  If the verified column is not
    found, return None so V2 can mark futures as pending instead of polluting the
    market snapshot.
    """
    try:
        flat_cols = []
        for c in list(df.columns):
            if isinstance(c, tuple):
                flat_cols.append(" ".join(str(x) for x in c if str(x) != "nan"))
            else:
                flat_cols.append(str(c))
        df = df.copy()
        df.columns = flat_cols

        def norm(x) -> str:
            return re.sub(r"\s+", "", str(x or "")).replace("臺", "台")

        # Strict target columns only: OI balance + net + contract/count.  Reject amount columns.
        strict_cols = []
        for c in flat_cols:
            cc = norm(c)
            if "未平倉" not in cc:
                continue
            if not any(k in cc for k in ("多空淨額", "多空淨", "淨額", "淨")):
                continue
            if any(k in cc for k in ("金額", "契約金額", "市值")):
                continue
            # Prefer explicit count/contract columns.  Some TAIFEX tables flatten
            # the count unit away, so keep the column if it is clearly inside 未平倉 + 淨額.
            strict_cols.append(c)

        if not strict_cols:
            return None

        for _, row in df.iterrows():
            vals = [str(x or "") for x in row.tolist()]
            row_text = norm(" ".join(vals))
            if "外資" not in row_text:
                continue
            if require_product_name and not any(k in row_text for k in ("台股期貨", "TXF", "TX")):
                continue
            for c in strict_cols:
                val = _to_int(row.get(c))
                # Reject obvious sequence/garbage values, but keep real low net if
                # the table explicitly exposes the verified OI net column.
                if val is None:
                    continue
                if abs(int(val)) <= 1000:
                    # Too easy to confuse with sequence numbers; better待同步 than wrong.
                    continue
                if abs(int(val)) > 300000:
                    continue
                return {
                    "net_oi": int(val),
                    "net_col": str(c),
                    "product": "臺股期貨",
                    "identity": "外資",
                    "field": "未平倉餘額/多空淨額/口數",
                }
    except Exception:
        pass
    return None


def _futures_delta_label(net: int, delta: object) -> str:
    try:
        d = int(float(str(delta).replace(',', '')))
    except Exception:
        return "待前日比對"
    if d == 0:
        return "淨部位持平"
    if net < 0:
        return f"淨空增加 {abs(d):,}口" if d < 0 else f"淨空減少 {abs(d):,}口"
    if net > 0:
        return f"淨多增加 {abs(d):,}口" if d > 0 else f"淨多減少 {abs(d):,}口"
    return f"淨部位變化 {d:+,}口"


def _fetch_taifex_foreign_futures(price_date: str) -> Dict[str, object]:
    """Fetch official TAIFEX foreign TXF net open interest.

    Main source: TAIFEX 三大法人 / 區分各期貨契約 / 依日期.
    Only the verified TXF foreign open-interest net contract count is accepted.
    """
    import requests
    try:
        end_dt = date.fromisoformat(price_date) if re.match(r"\d{4}-\d{2}-\d{2}", str(price_date)) else today_taipei_date()
    except Exception:
        end_dt = today_taipei_date()
    errors = []
    found = []
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 TINO-V12"})
    url = "https://www.taifex.com.tw/cht/3/futContractsDate"

    for i in range(0, 12):
        d = end_dt - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        q = d.strftime("%Y/%m/%d")
        payloads = (
            {"queryDate": q, "commodityId": "TXF"},
            {"queryDate": q, "commodityId": ""},
        )
        for params in payloads:
            try:
                resp = session.post(url, data=params, timeout=9)
                html = resp.text or ""
                if "外資" not in html:
                    resp = session.get(url, params=params, timeout=9)
                    html = resp.text or ""
                if "外資" not in html:
                    continue
                parsed = None
                for df in pd.read_html(html):
                    parsed = _parse_taifex_foreign_row(df, require_product_name=(params.get("commodityId") != "TXF"))
                    if parsed:
                        break
                if not parsed:
                    continue
                net = int(parsed["net_oi"])
                found.append((d, net, parsed))
                break
            except Exception as exc:
                errors.append(f"{q}:{type(exc).__name__}")
                continue
        if len(found) >= 2:
            break

    if not found:
        raise RuntimeError("TAIFEX futures empty or unverified; " + ";".join(errors[-3:]))

    cur_d, net, parsed = found[0]
    prev_net = found[1][1] if len(found) >= 2 else None
    delta = int(net - prev_net) if prev_net is not None else None
    risk_level = "高" if net <= -100000 else ("中高" if net <= -60000 else ("中" if net < 0 else "低"))
    side = "淨空" if net < 0 else "淨多" if net > 0 else "中性"
    return {
        "accepted": True,
        "source": "TAIFEX_FutContractsDate_Strict",
        "date": cur_d.isoformat(),
        "product": "臺股期貨",
        "identity": "外資",
        "field": "未平倉餘額/多空淨額/口數",
        "net_oi": net,
        "delta": delta if delta is not None else "待前日比對",
        "delta_label": _futures_delta_label(net, delta),
        "settlement": "TAIFEX官方",
        "risk_level": risk_level,
        "summary": f"外資期貨{side} {net:,}口",
        "reason": f"TAIFEX 官方嚴格欄位｜{parsed.get('net_col','未平倉多空淨額')}",
        "included_in_v2": True,
    }


def _market_foreign_flow_v2_snapshot(price_date: str, context: Dict[str, object]) -> Dict[str, object]:
    """Market-wide Foreign Flow V2 snapshot shared by all tickers.

    This prevents switching from one stock to another after close from changing
    the estimated market foreign-flow pressure.  Only market-wide blocks are used:
    tv_pressure, futures and macro calendar.  No single-stock VWAP/price_snapshot.
    """
    _foreign_flow_load_disk_cache()
    key = _foreign_flow_snapshot_key(price_date) + ":foreign_flow_v2"
    cached = _FOREIGN_FLOW_SNAPSHOT_CACHE.get(key)
    if isinstance(cached, dict) and cached.get("accepted"):
        out = dict(cached)
        out["snapshot_hit"] = True
        return out
    market_ctx = {
        "futures": context.get("futures", {}) if isinstance(context.get("futures", {}), dict) else {},
        "macro": context.get("macro", {}) if isinstance(context.get("macro", {}), dict) else {},
        "market_vwap_state": context.get("market_vwap_state", ""),
    }
    v2 = predict_foreign_flow_v2(context.get("tv_pressure", {}), market_ctx)
    if isinstance(v2, dict):
        v2 = dict(v2)
        v2["snapshot_key"] = key
        v2["snapshot_hit"] = False
        if isinstance(market_ctx.get("futures"), dict):
            v2["futures_date"] = market_ctx["futures"].get("date")
        if v2.get("accepted"):
            _FOREIGN_FLOW_SNAPSHOT_CACHE[key] = dict(v2)
            _foreign_flow_save_disk_cache()
    return v2
def _finmind_query(dataset: str, stock_id: str, start: str, end: str | None = None) -> List[Dict[str, object]]:
    import requests
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {"dataset": dataset, "data_id": stock_id, "start_date": start}
    if end:
        params["end_date"] = end
    token = os.environ.get("FINMIND_TOKEN") or os.environ.get("FINMIND_API_TOKEN")
    if token:
        params["token"] = token
    r = requests.get(url, params=params, timeout=10, headers={"User-Agent": "TINO-V12/1.1"})
    r.raise_for_status()
    js = r.json()
    data = js.get("data") if isinstance(js, dict) else None
    if not isinstance(data, list):
        return []
    return data


from data_sources_tw_fundamental import fetch_tw_fundamental_crosscheck

def _to_int(value) -> int | None:
    if value in (None, "", "None", "nan"):
        return None
    try:
        txt = str(value).strip().replace(",", "")
        neg = txt.startswith("(") and txt.endswith(")")
        txt = txt.replace("(", "").replace(")", "")
        txt = re.sub(r"[^0-9+\-.]", "", txt)
        if txt in {"", "+", "-", "."}:
            return None
        val = int(round(float(txt)))
        return -abs(val) if neg else val
    except Exception:
        return None
def _investor_key(row: Dict[str, object]) -> str | None:
    text = " ".join(str(row.get(k, "")) for k in ("name", "investor", "institutional_investors", "type", "Investor", "institutionalInvestor"))
    lower = text.lower()
    if "投信" in text or "investment" in lower or "trust" in lower:
        return "trust"
    if "外資" in text or "foreign" in lower or "qfii" in lower:
        return "foreign"
    if "自營" in text or "dealer" in lower or "proprietary" in lower:
        return "dealer"
    return None
def _institutional_net_value(row: Dict[str, object]) -> int | None:
    for key in ("buy_sell", "buySell", "buy_sell_diff", "buySellDiff", "net_buy_sell", "netBuySell"):
        v = _to_int(row.get(key))
        if v is not None:
            return v
    buy = None
    sell = None
    for key in ("buy", "Buy", "buy_volume", "buyVolume"):
        buy = _to_int(row.get(key))
        if buy is not None:
            break
    for key in ("sell", "Sell", "sell_volume", "sellVolume"):
        sell = _to_int(row.get(key))
        if sell is not None:
            break
    if buy is not None or sell is not None:
        return int((buy or 0) - (sell or 0))
    return None
def _fetch_finmind_inst(symbol: str, price_date: str) -> Dict[str, object]:
    stock_id = _code(symbol)
    try:
        end_dt = date.fromisoformat(price_date) if re.match(r"\d{4}-\d{2}-\d{2}", price_date) else today_taipei_date()
    except Exception:
        end_dt = today_taipei_date()
    start_dt = end_dt - timedelta(days=60)
    rows: List[Dict[str, object]] = []
    errors: List[str] = []
    for dataset in ("TaiwanStockInstitutionalInvestorsBuySell", "InstitutionalInvestorsBuySell"):
        try:
            rows = _finmind_query(dataset, stock_id, start_dt.isoformat(), end_dt.isoformat())
            if rows:
                break
        except Exception as exc:
            errors.append(f"{dataset}:{type(exc).__name__}")
            rows = []
    if not rows:
        raise RuntimeError("institutional empty; " + ";".join(errors[-2:]))
    by_date: Dict[str, Dict[str, int]] = {}
    parsed = 0
    for row in rows:
        d = str(row.get("date", ""))[:10]
        if not re.match(r"\d{4}-\d{2}-\d{2}", d):
            continue
        try:
            if date.fromisoformat(d) > end_dt:
                continue
        except Exception:
            continue
        key = _investor_key(row)
        val = _institutional_net_value(row)
        if key is None or val is None:
            continue
        by_date.setdefault(d, {"foreign": 0, "trust": 0, "dealer": 0})[key] += val
        parsed += 1
    dates = sorted(by_date)[-10:]
    if not dates or parsed == 0:
        raise RuntimeError("institutional no parsed rows")
    f = [_shares_to_lots(by_date[d]["foreign"]) for d in dates]
    t = [_shares_to_lots(by_date[d]["trust"]) for d in dates]
    de = [_shares_to_lots(by_date[d]["dealer"]) for d in dates]
    if not any(f) and not any(t) and not any(de):
        raise RuntimeError("institutional parsed all zero; missing not official zero")
    return {
        "foreign": f[-1], "foreign_3": _sum_last(f, 3), "foreign_5": _sum_last(f, 5), "foreign_10": _sum_last(f, 10), "foreign_streak": _streak(f, "買", "賣"),
        "trust": t[-1], "trust_3": _sum_last(t, 3), "trust_5": _sum_last(t, 5), "trust_10": _sum_last(t, 10), "trust_streak": _streak(t, "買", "賣"),
        "dealer": de[-1], "dealer_3": _sum_last(de, 3), "dealer_5": _sum_last(de, 5), "dealer_10": _sum_last(de, 10), "dealer_streak": _streak(de, "買", "賣"),
        "source": "FinMind_Institutional", "date": dates[-1], "accepted": True,
        "reason": "法人同步｜來源 FinMind_Institutional",
        "symbol": symbol,
    }
def _fetch_finmind_margin(symbol: str, price_date: str) -> Dict[str, object]:
    stock_id = _code(symbol)
    start = (date.fromisoformat(price_date) - timedelta(days=25)).isoformat() if re.match(r"\d{4}-\d{2}-\d{2}", price_date) else (today_taipei_date() - timedelta(days=35)).isoformat()
    rows = _finmind_query("TaiwanStockMarginPurchaseShortSale", stock_id, start, price_date if re.match(r"\d{4}-\d{2}-\d{2}", price_date) else None)
    if not rows:
        raise RuntimeError("FinMind margin empty")
    vals = []
    for row in rows:
        d = str(row.get("date", ""))[:10]
        def get(*names):
            for name in names:
                if name in row and row[name] not in (None, ""):
                    return row[name]
            return 0
        mbuy = float(get("MarginPurchaseBuy", "margin_purchase_buy"))
        msell = float(get("MarginPurchaseSell", "margin_purchase_sell"))
        sbuy = float(get("ShortSaleBuy", "short_sale_buy"))
        ssell = float(get("ShortSaleSell", "short_sale_sell"))
        mbalance = float(get("MarginPurchaseTodayBalance", "margin_purchase_today_balance"))
        sbalance = float(get("ShortSaleTodayBalance", "short_sale_today_balance"))
        vals.append((d, int(mbuy - msell), int(ssell - sbuy), mbalance, sbalance))
    vals = vals[-10:]
    if not vals:
        raise RuntimeError("FinMind margin no parsed rows")
    dates = [x[0] for x in vals]
    m = [x[1] for x in vals]
    s = [x[2] for x in vals]
    last_margin_balance = max(vals[-1][3], 1.0)
    ratio = max(0.0, vals[-1][4] / last_margin_balance * 100.0)
    return {
        "margin": m[-1], "margin_3": _sum_last(m, 3), "margin_5": _sum_last(m, 5), "margin_10": _sum_last(m, 10), "margin_streak": _streak(m, "增", "減"),
        "short": s[-1], "short_3": _sum_last(s, 3), "short_5": _sum_last(s, 5), "short_10": _sum_last(s, 10), "short_streak": _streak(s, "增", "減"),
        "ratio": ratio, "source": "FinMind_MARGIN", "date": dates[-1], "accepted": True, "reason": "資券同步｜來源 FinMind_MARGIN",
    }
def _merge_official_context(ticker: TickerInfo, context: Dict[str, object], price_date: str, *, closes: List[float] | None = None, last: float | None = None, vwap: float | None = None, previous_close: float | None = None) -> Dict[str, object]:
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return context
    # Yahoo-first chip restoration.  Each block falls back independently;
    # market heat is never allowed to affect individual-stock institutions/margin.
    try:
        context["inst"] = validate_official_block(
            fetch_yahoo_institutional(ticker.resolved_symbol, price_date), price_date, "三大法人"
        )
    except Exception as yahoo_exc:
        try:
            context["inst"] = validate_official_block(
                _fetch_finmind_inst(ticker.resolved_symbol, price_date), price_date, "三大法人"
            )
        except Exception as finmind_exc:
            context["inst"] = _empty_inst(
                price_date,
                f"法人抓取失敗：Yahoo {type(yahoo_exc).__name__} / FinMind {type(finmind_exc).__name__}",
            )
    try:
        context["margin"] = validate_official_block(
            fetch_yahoo_margin(ticker.resolved_symbol, price_date), price_date, "資券"
        )
    except Exception as yahoo_exc:
        try:
            context["margin"] = validate_official_block(
                _fetch_finmind_margin(ticker.resolved_symbol, price_date), price_date, "資券"
            )
        except Exception as finmind_exc:
            context["margin"] = _empty_margin(
                price_date,
                f"資券抓取失敗：Yahoo {type(yahoo_exc).__name__} / FinMind {type(finmind_exc).__name__}",
            )
    try:
        # Fundamental is an official/public-data block, not memory/news text.
        context["fundamental"] = fetch_tw_fundamental_crosscheck(ticker.resolved_symbol, price_date)
    except Exception as exc:
        context["fundamental"] = {"accepted": False, "source": "TW_FUNDAMENTAL_FETCH_ERROR", "reason": f"fundamental error:{type(exc).__name__}"}
    try:
        context["futures"] = validate_official_block(_fetch_taifex_foreign_futures(price_date), price_date, "外資期貨")
    except Exception as exc:
        context["futures"] = {"accepted": False, "source": "TAIFEX_FUTURES_FETCH_FAILED", "date": "", "reason": f"外資期貨抓取失敗：{type(exc).__name__}"}
    try:
        # Macro must stay market-wide. Do not mix EPS / monthly revenue / stock-level metadata here.
        context["macro"] = _macro_forward_context(price_date)
    except Exception:
        pass
    try:
        context["tv_pressure"] = _tv_pressure_context(
            ticker.resolved_symbol,
            price_date,
            closes or [],
            float(last if last is not None else 0.0),
            float(vwap if vwap is not None else (last if last is not None else 0.0)),
            context.get("chip_proxy", {}),
            previous_close=previous_close,
            inst=context.get("inst", {}),
        )
    except Exception:
        context["tv_pressure"] = _tv_pressure_wait(price_date, "匯率差公式資料待同步｜不顯示假數字")
    context = _apply_v9_verified_contract(ticker, context, price_date)
    try:
        # Build a local SSOT-like snapshot for calibration only. The formal
        # price_snapshot is still attached later by _attach_price_snapshot.
        if "price_snapshot" not in context and last is not None:
            _last = float(last)
            _vwap = float(vwap if vwap is not None else _last)
            context["price_snapshot"] = {
                "last": _last,
                "vwap": _vwap,
                "vwap_state": "VWAP 上方" if _last >= _vwap else "VWAP 下方",
            }
        # Foreign Flow V2 is market-wide and must be shared by all tickers.
        # Single-stock price_snapshot/VWAP is kept for stock decision cards only.
        flow_v2 = _market_foreign_flow_v2_snapshot(price_date, context)
        context["foreign_flow_v2"] = flow_v2
        if isinstance(flow_v2, dict) and isinstance(flow_v2.get("tv_pressure"), dict):
            context["tv_pressure"] = flow_v2["tv_pressure"]
    except Exception:
        # Calibration must never break the main app. Details are trace-only.
        pass
    return context
def _fallback_price(ticker: TickerInfo, reason: str) -> PriceFrame:
    base = TW_SAMPLE.get(ticker.resolved_symbol, dict(open=100, high=103, low=97, last=100, previous_close=100, volume=1000, vwap=100, atr14=3))
    s = _series_from_sample(base, ticker.resolved_symbol)
    d = (today_taipei_date() - timedelta(days=1)).isoformat()
    context = _flow_context(ticker, d, s["closes"], float(base["last"]), float(base["vwap"]), previous_close=float(base["previous_close"]))
    context = _apply_v9_verified_contract(ticker, context, d)
    context = _attach_price_snapshot(context, open_price=float(base["open"]), high=float(base["high"]), low=float(base["low"]), last=float(base["last"]), previous_close=float(base["previous_close"]), volume=float(base["volume"]), vwap=float(base["vwap"]), price_time=d, price_source="V12_PRICE_SAMPLE_FALLBACK", market_mode=_tw_market_status(d))
    return PriceFrame(ticker=ticker, truth=make_truth("V12_PRICE_SAMPLE_FALLBACK", d, True, True, reason, "fallback_reference"), open=float(base["open"]), high=float(base["high"]), low=float(base["low"]), last=float(base["last"]), previous_close=float(base["previous_close"]), volume=float(base["volume"]), vwap=float(base["vwap"]), atr14=float(base["atr14"]), recent_closes=s["closes"], recent_highs=s["highs"], recent_lows=s["lows"], recent_volumes=s["volumes"], price_date=d, market_status=_tw_market_status(d), context=context)


def _invalid_price(ticker: TickerInfo, reason: str, *, debug: Dict[str, object] | None = None) -> PriceFrame:
    """Return a rejected PriceFrame when live price cannot be verified.

    RC2.4.3 Price Truth Guard:
    - In live mode, missing TWSE/TPEX/Yahoo official quotes must stop the
      forecast instead of falling through to the old synthetic 100/103/97 sample.
    - This prevents wrong ticker-market resolution or transient quote failures
      from polluting Learning Center / Auto Audit memory.
    """
    d = today_taipei_date().isoformat()
    context: Dict[str, object] = {
        "invalid_price": True,
        "price_meta": {
            "invalid_price": True,
            "decision_blocked": True,
            "source": "PRICE_UNAVAILABLE_STOP",
            "status": "價格不可用",
            "label": f"價格來源不可用｜{reason}",
            "reason": reason,
            "debug": debug or {},
        },
        "price_snapshot": {
            "last": None,
            "vwap": None,
            "vwap_state": "價格待確認",
        },
    }
    return PriceFrame(
        ticker=ticker,
        truth=make_truth("PRICE_UNAVAILABLE_STOP", d, False, False, reason, "invalid_price"),
        open=0.0, high=0.0, low=0.0, last=0.0, previous_close=0.0,
        volume=0.0, vwap=0.0, atr14=0.0,
        recent_closes=[], recent_highs=[], recent_lows=[], recent_volumes=[],
        price_date=d, market_status=_tw_market_status(d), context=context,
    )



def _yahoo_chart_intraday(symbol: str) -> Dict[str, object]:
    """Fetch faster TW intraday quote from Yahoo chart API.

    yfinance daily history may lag badly during trading hours. This helper is
    intentionally small and defensive: if Yahoo chart/quote is unavailable, the
    engine falls back to the existing yfinance daily path.
    """
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return {"accepted": False, "reason": "offline"}
    try:
        import requests
        from datetime import datetime as _dt
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {"range": "1d", "interval": "1m", "includePrePost": "false", "events": "div,splits"}
        headers = {"User-Agent": "Mozilla/5.0"}
        data = requests.get(url, params=params, headers=headers, timeout=4).json()
        result = (((data or {}).get("chart") or {}).get("result") or [None])[0]
        if not result:
            return {"accepted": False, "reason": "chart_empty"}
        meta = result.get("meta") or {}
        ts = result.get("timestamp") or []
        quote = (((result.get("indicators") or {}).get("quote") or [None])[0]) or {}
        closes = [float(x) for x in quote.get("close", []) if x is not None and float(x) > 0]
        highs = [float(x) for x in quote.get("high", []) if x is not None and float(x) > 0]
        lows = [float(x) for x in quote.get("low", []) if x is not None and float(x) > 0]
        opens = [float(x) for x in quote.get("open", []) if x is not None and float(x) > 0]
        vols = [float(x or 0) for x in quote.get("volume", [])]
        if not closes:
            mp = meta.get("regularMarketPrice") or meta.get("previousClose")
            if not mp:
                return {"accepted": False, "reason": "no_close"}
            closes = [float(mp)]
        last = float(meta.get("regularMarketPrice") or closes[-1])
        open_ = float(meta.get("regularMarketOpen") or (opens[0] if opens else last))
        high = float(meta.get("regularMarketDayHigh") or (max(highs) if highs else max(open_, last)))
        low = float(meta.get("regularMarketDayLow") or (min(lows) if lows else min(open_, last)))
        prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or 0)
        vol = float(meta.get("regularMarketVolume") or sum(vols) or 0)
        if vols and len(vols) == len(closes) and sum(vols) > 0:
            vwap = sum(c * v for c, v in zip(closes[-len(vols):], vols) if c > 0 and v > 0) / max(1.0, sum(v for v in vols if v > 0))
        else:
            vwap = (high + low + last) / 3.0
        raw_time = meta.get("regularMarketTime") or (ts[-1] if ts else None)
        if raw_time:
            dt_tw = _dt.fromtimestamp(int(raw_time), tz=ZoneInfo("Asia/Taipei"))
            price_date = parse_date_safe(dt_tw.date().isoformat())
        else:
            price_date = today_taipei_date()
        return {
            "accepted": True,
            "source": "YahooChart_1m",
            "open": open_, "high": high, "low": low, "last": last,
            "previous_close": prev, "volume": vol, "vwap": float(vwap),
            "price_date": price_date,
            "raw_time": raw_time,
        }
    except Exception as exc:
        return {"accepted": False, "reason": f"chart_error:{type(exc).__name__}"}


def _yahoo_quote_fast(symbol: str) -> Dict[str, object]:
    """Backup direct quote endpoint; usually faster than daily history."""
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return {"accepted": False, "reason": "offline"}
    try:
        import requests
        from datetime import datetime as _dt
        url = "https://query1.finance.yahoo.com/v7/finance/quote"
        data = requests.get(url, params={"symbols": symbol}, headers={"User-Agent": "Mozilla/5.0"}, timeout=4).json()
        q = (((data or {}).get("quoteResponse") or {}).get("result") or [None])[0]
        if not q:
            return {"accepted": False, "reason": "quote_empty"}
        last = q.get("regularMarketPrice")
        if last is None:
            return {"accepted": False, "reason": "no_regularMarketPrice"}
        raw_time = q.get("regularMarketTime")
        price_date = today_taipei_date()
        if raw_time:
            price_date = parse_date_safe(_dt.fromtimestamp(int(raw_time), tz=ZoneInfo("Asia/Taipei")).date().isoformat())
        high = float(q.get("regularMarketDayHigh") or last)
        low = float(q.get("regularMarketDayLow") or last)
        open_ = float(q.get("regularMarketOpen") or last)
        return {
            "accepted": True,
            "source": "YahooQuote_Fast",
            "open": open_, "high": high, "low": low, "last": float(last),
            "previous_close": float(q.get("regularMarketPreviousClose") or 0),
            "volume": float(q.get("regularMarketVolume") or 0),
            "vwap": (high + low + float(last)) / 3.0,
            "price_date": price_date,
            "raw_time": raw_time,
        }
    except Exception as exc:
        return {"accepted": False, "reason": f"quote_error:{type(exc).__name__}"}


def _tw_now():
    return datetime.now(ZoneInfo("Asia/Taipei"))


def _tw_session_phase(now=None) -> str:
    """Taiwan market session normalized in Asia/Taipei.

    Important: 13:30:00+ is not continuous intraday anymore.
    It is the close-confirm window, so a quote stamped around 13:30 must not be
    treated as stale and must not lock the whole analysis.
    """
    now = now or _tw_now()
    if now.weekday() >= 5:
        return "closed"
    t = now.time()
    if t < time(9, 0):
        return "pre_market"
    if time(9, 0) <= t < time(13, 30):
        return "intraday"
    if time(13, 30) <= t <= time(13, 35):
        return "close_confirm"
    return "after_close"


def _is_tw_intraday_now(now=None) -> bool:
    # Continuous trading only. Close-confirm must not be handled as live intraday.
    return _tw_session_phase(now) == "intraday"


# RC2.4.3.1 Emerging Price Grace
# 興櫃價格常比上市/上櫃慢，尤其 YahooChart_1m 可能延遲 10~30 分鐘。
# 但只要來源回傳的是有效真實價格，就應該「標示延遲參考」而不是 STOP；
# 仍然禁止 fallback/mock/sample 價格進入正式預測。
EMERGING_PRICE_CODES = {"6586"}
EMERGING_PRICE_GRACE_SECONDS = 45 * 60


def _is_emerging_symbol(symbol: str) -> bool:
    code = str(symbol or "").split(".")[0].strip().upper()
    return code in EMERGING_PRICE_CODES


def _apply_emerging_price_grace(candidate: Dict[str, object], symbol: str) -> Dict[str, object]:
    if not _is_emerging_symbol(symbol) or not candidate.get("accepted"):
        return candidate
    try:
        age = int(candidate.get("age_seconds") or 999999)
    except Exception:
        age = 999999
    src = str(candidate.get("source") or "")
    # Only real quote providers can be relaxed; sample/fallback remains forbidden.
    if src not in {"YahooChart_1m", "YahooQuote_Fast", "TPEX_MIS_Realtime", "TWSE_MIS_Realtime"}:
        return candidate
    if age > EMERGING_PRICE_GRACE_SECONDS:
        return candidate
    out = dict(candidate)
    out["decision_blocked"] = False
    out["emerging_price_grace"] = True
    out["price_status"] = "興櫃延遲參考" if age > 90 else str(out.get("price_status") or "盤中快報")
    hm = str(out.get("source_time_hm") or "--:--")
    out["price_time_label"] = f"價格時間：{hm}｜來源：{src}｜狀態：興櫃延遲參考｜不視為即時"
    out["cross_source_note"] = str(out.get("cross_source_note") or "") + ";Emerging quote grace: real delayed quote accepted as reference"
    return out


def _parse_price_time(raw_time, price_date: str | None = None):
    """Normalize MIS/Yahoo time into Asia/Taipei datetime.

    raw_time can be Unix seconds, ISO-like text, or HH:MM:SS from TWSE MIS.
    Never let the UI guess freshness from price alone.
    """
    if raw_time in (None, "", "-", "--"):
        return None
    try:
        if isinstance(raw_time, (int, float)) or str(raw_time).strip().isdigit():
            ts = int(float(raw_time))
            if ts > 10_000_000_000:  # TWSE MIS tlong is milliseconds
                ts = ts // 1000
            return datetime.fromtimestamp(ts, tz=ZoneInfo("Asia/Taipei"))
        txt = str(raw_time).strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}T\d{1,2}:\d{2}:\d{2}", txt):
            dt = datetime.fromisoformat(txt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("Asia/Taipei"))
            return dt.astimezone(ZoneInfo("Asia/Taipei"))
        if re.match(r"^\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}:\d{2}$", txt):
            return datetime.fromisoformat(txt).replace(tzinfo=ZoneInfo("Asia/Taipei"))
        if re.match(r"^\d{1,2}:\d{2}:\d{2}$", txt):
            d = parse_date_safe(str(price_date or today_taipei_date()))
            return datetime.fromisoformat(f"{d}T{txt}").replace(tzinfo=ZoneInfo("Asia/Taipei"))
    except Exception:
        return None
    return None


def _enrich_price_freshness(row: Dict[str, object]) -> Dict[str, object]:
    if not row.get("accepted"):
        return row
    out = dict(row)
    dt = _parse_price_time(out.get("raw_time"), str(out.get("price_date") or today_taipei_date()))
    now = _tw_now()
    if dt:
        delta = int((now - dt).total_seconds())
        # Official quote time must not be in the future.  A small clock skew is OK,
        # but a future MIS/Yahoo timestamp is a wrong-session signal and must not
        # become the decision price.
        if delta < -120:
            out["accepted"] = False
            out["reason"] = f"price_time_in_future:{dt.isoformat()}"
            out["source_time"] = dt.isoformat()
            out["source_time_hm"] = dt.strftime("%H:%M:%S")
            out["age_seconds"] = 999999
            out["price_status"] = "時間異常"
            src = str(out.get("source") or "PriceSource")
            out["price_time_label"] = f"價格時間：{out.get('source_time_hm')}｜來源：{src}｜狀態：時間異常"
            out["stale"] = True
            return out
        age = max(0, delta)
        out["source_time"] = dt.isoformat()
        out["source_time_hm"] = dt.strftime("%H:%M:%S")
        out["age_seconds"] = age
        phase = _tw_session_phase(now)
        if phase == "intraday":
            if age <= 90:
                status = "盤中快報"
            elif age <= 600:
                status = f"延遲{age//60}分"
            else:
                status = "延遲資料"
        elif phase == "close_confirm":
            status = "收盤確認"
        elif phase == "after_close":
            status = "已收盤參考"
        elif phase == "pre_market":
            status = "盤前參考"
        else:
            status = "非交易日參考"
    else:
        out["source_time"] = ""
        out["source_time_hm"] = "--:--"
        out["age_seconds"] = 999999 if _is_tw_intraday_now(now) else 0
        status = "時間未標示"
    src = str(out.get("source") or "PriceSource")
    out["price_status"] = status
    out["price_time_label"] = f"價格時間：{out.get('source_time_hm')}｜來源：{src}｜狀態：{status}"
    age_value = out.get("age_seconds")
    if age_value is None:
        age_value = 999999
    out["session_phase"] = _tw_session_phase(now)
    out["stale"] = bool(_is_tw_intraday_now(now) and int(age_value) > 90)
    return out


def _mis_candidate_from_debug(symbol: str, mis_candidate: Dict[str, object]) -> Dict[str, object] | None:
    """Final guardrail: if MIS debug proves a valid quote, build a real candidate.

    v8.6 debug showed cases where MIS parsed successfully but the final selector
    still returned YahooChart_1m.  This helper prevents that by reconstructing a
    selectable MIS payload directly from the Admin breadcrumb when it is valid.
    """
    if not isinstance(mis_candidate, dict):
        return None
    dbg = mis_candidate.get("mis_debug") or {}
    if not isinstance(dbg, dict):
        return None
    if not dbg.get("mis_tried"):
        return None
    if dbg.get("mis_http_status") not in (200, "200"):
        return None
    if not dbg.get("mis_raw_ok"):
        return None
    if dbg.get("mis_reject_reason") not in (None, "", "NULL", "null"):
        return None
    last = dbg.get("mis_parsed_last")
    high = dbg.get("mis_parsed_high")
    low = dbg.get("mis_parsed_low")
    raw_time = dbg.get("mis_parsed_time")
    try:
        last_f = float(last)
        high_f = float(high if high is not None else last_f)
        low_f = float(low if low is not None else last_f)
    except Exception:
        return None
    if last_f <= 0 or high_f <= 0 or low_f <= 0:
        return None
    source = dbg.get("mis_source") or ("TPEX_MIS_Realtime" if str(symbol).upper().endswith(".TWO") else "TWSE_MIS_Realtime")
    out = dict(mis_candidate)
    out.update({
        "accepted": True,
        "source": source,
        "open": float(out.get("open") or dbg.get("mis_parsed_open") or last_f),
        "high": float(max(high_f, last_f)),
        "low": float(min(low_f, last_f)),
        "last": last_f,
        "previous_close": float(out.get("previous_close") or dbg.get("mis_previous_close") or last_f),
        "volume": float(out.get("volume") or dbg.get("mis_volume") or 0),
        "vwap": float(out.get("vwap") or ((max(high_f, last_f) + min(low_f, last_f) + last_f) / 3.0)),
        "price_date": str(out.get("price_date") or today_taipei_date()),
        "raw_time": raw_time,
        "mis_debug": dbg,
        "reason": "",
    })
    return _enrich_price_freshness(out)


def _pick_fast_price(symbol: str) -> Dict[str, object]:
    # V8.4: TWSE/TPEX MIS is the primary intraday decision source.
    # Yahoo is backup; Google Finance is reference-only.  Stale sources are
    # allowed for display but are marked decision_blocked so delayed quotes do
    # not silently drive the trading card.
    raw_mis = fetch_twse_mis_live_price(symbol)
    mis_candidate = _enrich_price_freshness(raw_mis)
    # v8.7.1 final override: if MIS debug is valid, MIS must be the selected price.
    # This catches the exact failure observed in Admin: MIS parsed OK, but YahooChart_1m won.
    forced_mis = _mis_candidate_from_debug(symbol, mis_candidate)
    if forced_mis and forced_mis.get("accepted") and not forced_mis.get("stale"):
        forced_mis["decision_blocked"] = False
        forced_mis["price_verified"] = True
        src = str(forced_mis.get("source") or "TWSE/TPEX_MIS")
        hm = str(forced_mis.get("source_time_hm") or "--:--")
        status = str(forced_mis.get("price_status") or "盤中快報")
        forced_mis["price_time_label"] = f"價格時間：{hm}｜來源：{src}｜狀態：{status}"
        forced_mis["cross_source_note"] = "MIS valid final override; Yahoo/Google 僅備援參考"
        return forced_mis

    candidates = [
        mis_candidate,
        _enrich_price_freshness(_yahoo_quote_fast(symbol)),
        _enrich_price_freshness(_yahoo_chart_intraday(symbol)),
    ]
    mis_candidate = candidates[0] if candidates else {}
    mis_debug = mis_candidate.get("mis_debug") if isinstance(mis_candidate, dict) else None
    google_ref = fetch_google_finance_reference(symbol)
    accepted = [c for c in candidates if c.get("accepted")]
    if not accepted:
        reason = " ".join([f"{c.get('source','src')}={c.get('reason')}" for c in candidates])
        if google_ref.get("accepted"):
            reason += f" GoogleFinance_Reference={google_ref.get('last')}"
        return {"accepted": False, "reason": reason.strip(), "mis_debug": mis_debug or {}}

    def source_priority(c):
        src = str(c.get("source") or "")
        if "MIS" in src:
            return 0
        if src == "YahooQuote_Fast":
            return 1
        if src == "YahooChart_1m":
            return 2
        return 9

    # V8.7 hard rule: a valid TWSE/TPEX MIS quote must win.
    # Debug proved MIS can be accepted while the selector still fell back to YahooChart_1m.
    # Once MIS is accepted and not rejected, use it as the main official intraday price.
    if isinstance(mis_candidate, dict) and mis_candidate.get("accepted") and not mis_candidate.get("stale"):
        best = mis_candidate
        best["decision_blocked"] = False
        best["price_verified"] = True
        best["price_status"] = str(best.get("price_status") or "盤中快報")
        src = str(best.get("source") or "TWSE/TPEX_MIS")
        hm = str(best.get("source_time_hm") or "--:--")
        status = str(best.get("price_status") or "盤中快報")
        best["price_time_label"] = f"價格時間：{hm}｜來源：{src}｜狀態：{status}"
    else:
        fresh = [c for c in accepted if not c.get("stale")]
        if fresh:
            # MIS first when it is fresh; otherwise Yahoo quote/chart backup.
            fresh.sort(key=lambda c: (source_priority(c), int(c.get("age_seconds") or 999999)))
            best = fresh[0]
            best["decision_blocked"] = False
            best["price_verified"] = True
        else:
            # All sources are delayed: do not hard-lock the whole system.
            # Use the best real quote in price-limited mode, clearly mark it as
            # delayed reference, and keep it out of official Learning samples.
            accepted.sort(key=lambda c: int(c.get("age_seconds") or 999999))
            best = accepted[0]
            best = _apply_emerging_price_grace(best, symbol)
            if not best.get("emerging_price_grace"):
                best["decision_blocked"] = False
                best["limited_price_mode"] = True
                best["price_verified"] = False
                best["price_status"] = str(best.get("price_status") or "延遲資料")
                best["price_time_label"] = f"{best.get('price_time_label','價格時間：--｜來源：延遲資料｜狀態：延遲資料')}｜延遲參考"

    # Preserve MIS diagnostic breadcrumb even when Yahoo was selected as fallback.
    if mis_debug:
        best["mis_debug"] = mis_debug
    notes = [f"{c.get('source')}@{c.get('source_time_hm')}:{c.get('last')}:{c.get('price_status')}" for c in accepted if c is not best]
    # Admin/debug breadcrumb: why MIS or other sources were not used. Kept out of front decision text.
    reject_notes = [f"{c.get('source','src')}拒絕:{c.get('reason')}" for c in candidates if not c.get('accepted') and c.get('reason')]
    notes.extend(reject_notes[:3])
    if google_ref.get("accepted"):
        notes.append(f"GoogleFinance_Reference:{google_ref.get('last')}:僅交叉參考")
    if notes:
        best["cross_source_note"] = ";".join(notes)
    return best


def fetch_tw_price(ticker: TickerInfo) -> PriceFrame:
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return _fallback_price(ticker, "offline smoke test fallback")
    try:
        import yfinance as yf

        # Fetch realtime first; fast quote is source of truth when available.
        fast = _pick_fast_price(ticker.resolved_symbol)

        hist = yf.Ticker(ticker.resolved_symbol).history(period="6mo", interval="1d", auto_adjust=False, timeout=6)
        if hist is None or hist.empty:
            if fast.get("accepted"):
                if fast.get("decision_blocked") and not fast.get("emerging_price_grace"):
                    return _invalid_price(ticker, str(fast.get("price_time_label") or "價格延遲，盤中決策待確認"), debug={"fast": fast})
                base = TW_SAMPLE.get(ticker.resolved_symbol, dict(open=fast["open"], high=fast["high"], low=fast["low"], last=fast["last"], previous_close=fast.get("previous_close") or fast["last"], volume=fast.get("volume") or 1000, vwap=fast.get("vwap") or fast["last"], atr14=max(float(fast["last"]) * 0.03, 0.01)))
                s = _series_from_sample({**base, "last": float(fast["last"]), "high": float(fast["high"]), "low": float(fast["low"]), "open": float(fast["open"]), "previous_close": float(fast.get("previous_close") or base.get("previous_close") or fast["last"]), "volume": float(fast.get("volume") or base.get("volume") or 1000), "vwap": float(fast.get("vwap") or fast["last"]), "atr14": float(base.get("atr14") or max(float(fast["last"]) * 0.03, 0.01))}, ticker.resolved_symbol)
                d = parse_date_safe(str(fast.get("price_date") or today_taipei_date()))
                close = float(fast["last"]); high = float(fast["high"]); low = float(fast["low"]); open_ = float(fast["open"]); previous_close = float(fast.get("previous_close") or close); vwap = float(fast.get("vwap") or (high + low + close) / 3.0); vol = float(fast.get("volume") or 0)
                context = _merge_official_context(ticker, _flow_context(ticker, d, s["closes"], close, vwap, previous_close=previous_close), d, closes=s["closes"], last=close, vwap=vwap, previous_close=previous_close)
                context["price_meta"] = {
                    "source": fast.get("source"), "source_time": fast.get("source_time"), "source_time_hm": fast.get("source_time_hm"),
                    "age_seconds": fast.get("age_seconds"), "status": fast.get("price_status"),
                    "label": fast.get("price_time_label"), "cross_source_note": fast.get("cross_source_note", ""),
                    "decision_blocked": bool(fast.get("decision_blocked")),
                    "limited_price_mode": bool(fast.get("limited_price_mode")),
                    "price_verified": bool(fast.get("price_verified", not fast.get("limited_price_mode"))),
                    "emerging_price_grace": bool(fast.get("emerging_price_grace")),
                    "mis_debug": fast.get("mis_debug", {}),
                }
                context = _attach_price_snapshot(context, open_price=open_, high=high, low=low, last=close, previous_close=previous_close, volume=vol, vwap=vwap, price_time=str(fast.get("source_time") or fast.get("source_time_hm") or d), price_source=str(fast.get("source") or "RealtimeQuote"), market_mode=_tw_market_status(d))
                return PriceFrame(ticker=ticker, truth=make_truth(str(fast.get("source", "RealtimeQuote")), d, False, True, str(fast.get("price_time_label") or "價格快速同步｜日K待補"), "intraday_fast" if not fast.get("stale") else "intraday_delayed"), open=open_, high=high, low=low, last=close, previous_close=previous_close, volume=vol, vwap=vwap, atr14=float(base.get("atr14") or max(close * 0.03, 0.01)), recent_closes=s["closes"], recent_highs=s["highs"], recent_lows=s["lows"], recent_volumes=s["volumes"], price_date=d, market_status=_tw_market_status(d), context=context)
            return _invalid_price(ticker, f"yfinance 無資料；快速報價也未取得｜{fast.get('reason')}", debug={"fast": fast})
        hist = hist.dropna(subset=["Open", "High", "Low", "Close"])
        if len(hist) < 3:
            if fast.get("accepted"):
                hist = pd.DataFrame()
            else:
                return _invalid_price(ticker, f"日K不足；快速報價也未取得｜{fast.get('reason')}", debug={"fast": fast})

        last_row, prev_row = hist.iloc[-1], hist.iloc[-2]
        daily_close = float(last_row["Close"])
        daily_prev_close = float(prev_row["Close"])

        # Fast intraday quote overrides daily OHLC when available.
        if fast.get("accepted") and fast.get("decision_blocked") and _is_tw_intraday_now() and not fast.get("emerging_price_grace"):
            return _invalid_price(ticker, str(fast.get("price_time_label") or "價格延遲，盤中決策待確認"), debug={"fast": fast})
        if fast.get("accepted"):
            close = float(fast["last"])
            high = float(fast["high"])
            low = float(fast["low"])
            open_ = float(fast["open"])
            vol = float(fast.get("volume") or last_row.get("Volume", 0) or 0)
            vwap = float(fast.get("vwap") or (high + low + close) / 3.0)
            previous_close = float(fast.get("previous_close") or daily_prev_close)
            price_date = parse_date_safe(str(fast.get("price_date") or hist.index[-1].date().isoformat()))
            source_name = fast.get("source", "YahooFast")
            freshness = "intraday_fast"
            truth_reason = str(fast.get("price_time_label") or "價格快速同步｜官方MIS/Yahoo fast")
        else:
            close, high, low, open_ = daily_close, float(last_row["High"]), float(last_row["Low"]), float(last_row["Open"])
            vol = float(last_row.get("Volume", 0) or 0)
            vwap = (high + low + close) / 3.0
            previous_close = daily_prev_close
            price_date = parse_date_safe(hist.index[-1].date().isoformat())
            source_name = "YahooFinance_Daily"
            freshness = "daily_fallback"
            truth_reason = f"價格日K同步｜快速報價待同步：{fast.get('reason')}"

        tr = pd.concat([(hist["High"] - hist["Low"]).abs(), (hist["High"] - hist["Close"].shift()).abs(), (hist["Low"] - hist["Close"].shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else max(close * 0.03, 0.01)

        closes = [float(x) for x in hist["Close"].tail(60)]
        highs = [float(x) for x in hist["High"].tail(60)]
        lows = [float(x) for x in hist["Low"].tail(60)]
        volumes = [float(x) for x in hist["Volume"].tail(60)]
        if closes:
            closes[-1] = close
        if highs:
            highs[-1] = max(highs[-1], high)
        if lows:
            lows[-1] = min(lows[-1], low)
        if volumes:
            volumes[-1] = max(volumes[-1], vol)

        context = _merge_official_context(
            ticker,
            _flow_context(ticker, price_date, closes, close, vwap, previous_close=previous_close),
            price_date,
            closes=closes,
            last=close,
            vwap=vwap,
            previous_close=previous_close,
        )
        if fast.get("accepted"):
            context["price_meta"] = {
                "source": fast.get("source"), "source_time": fast.get("source_time"), "source_time_hm": fast.get("source_time_hm"),
                "age_seconds": fast.get("age_seconds"), "status": fast.get("price_status"),
                "label": fast.get("price_time_label"), "cross_source_note": fast.get("cross_source_note", ""),
                "decision_blocked": bool(fast.get("decision_blocked")),
                "limited_price_mode": bool(fast.get("limited_price_mode")),
                "price_verified": bool(fast.get("price_verified", not fast.get("limited_price_mode"))),
                "emerging_price_grace": bool(fast.get("emerging_price_grace")),
                "mis_debug": fast.get("mis_debug", {}),
            }
        else:
            limited_daily = bool(_is_tw_intraday_now())
            context["price_meta"] = {
                "source": source_name, "status": "日K參考",
                "label": f"價格時間：{price_date}｜來源：{source_name}｜狀態：日K參考" + ("｜延遲參考" if limited_daily else ""),
                "decision_blocked": False,
                "limited_price_mode": limited_daily,
                "price_verified": not limited_daily,
                "mis_debug": fast.get("mis_debug", {}) if isinstance(fast, dict) else {},
            }
        context = _attach_price_snapshot(context, open_price=open_, high=high, low=low, last=close, previous_close=previous_close, volume=vol, vwap=vwap, price_time=str((fast.get("source_time") if isinstance(fast, dict) else None) or (fast.get("source_time_hm") if isinstance(fast, dict) else None) or price_date), price_source=str(source_name), market_mode=_tw_market_status(price_date))
        return PriceFrame(
            ticker=ticker,
            truth=make_truth(str(source_name), price_date, False, True, truth_reason, "intraday_delayed" if fast.get("accepted") and fast.get("stale") else freshness),
            open=open_, high=high, low=low, last=close, previous_close=previous_close,
            volume=vol, vwap=vwap, atr14=atr,
            recent_closes=closes, recent_highs=highs, recent_lows=lows, recent_volumes=volumes,
            price_date=price_date, market_status=_tw_market_status(price_date), context=context,
        )
    except Exception as exc:
        return _invalid_price(ticker, f"資料源錯誤：{type(exc).__name__}；不使用樣本價格")
def _score_news(title: str) -> Tuple[float, str]:
    t = str(title or "").lower()
    pos = sum(1 for k in BULL if k.lower() in t)
    neg = sum(1 for k in BEAR if k.lower() in t)
    score = round((pos - neg) * 0.06, 3)
    if any(k.lower() in t for k in ["ai", "hbm", "pcb", "半導體", "伺服器", "輝達", "nvda"]):
        score += 0.035
    if any(k.lower() in t for k in ["處置", "警示", "下修", "虧損", "跌停"]):
        score -= 0.035
    score = max(-0.24, min(0.24, round(score, 3)))
    macro_terms = ("cpi", "ppi", "pce", "fomc", "非農", "nfp", "fed", "通膨", "利率決議")
    geo_terms = (
        "關稅", "tariff", "出口管制", "export control", "制裁", "sanction",
        "台海", "台灣海峽", "軍演", "南海", "中東", "美伊", "以伊", "伊朗", "以色列",
        "荷姆茲", "霍爾木茲", "hormuz", "紅海", "胡塞", "油價", "原油", "殖利率",
        "烏克蘭", "俄羅斯", "稀土", "rare earth", "關鍵礦物",
    )
    if any(k in t for k in geo_terms):
        tag = "policy_geo"
    elif any(k in t for k in macro_terms):
        tag = "macro_event"
    else:
        tag = "bullish_event" if score > 0.06 else ("bearish_event" if score < -0.06 else "headline_neutral")
    hit = [k for k in BULL + BEAR if k.lower() in t][:3]
    return score, "、".join(hit) if hit and tag not in {"policy_geo", "macro_event"} else tag


def _parse_tw_pub_date(pub: str):
    try:
        dt = email.utils.parsedate_to_datetime(pub or "")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt
    except Exception:
        return None


def _tw_news_time_label(pub: str) -> str:
    dt = _parse_tw_pub_date(pub)
    if not dt:
        return "latest"
    try:
        return dt.astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(pub or "latest")[:24]


def _tw_news_ttl_days(title: str = "", query: str = "") -> int:
    """RC24 News Time Guard.

    News used by the prediction layer must be recent enough for trading.
    - Company/event headlines: 30 days.
    - Earnings / revenue / investor conference / EPS guidance: 60 days because the
      event remains relevant until the next official update.
    - Old archive rows are never allowed to enter evidence/confidence.
    """
    text = f"{title} {query}".lower()
    macro_geo_terms = [
        "cpi", "ppi", "pce", "fomc", "非農", "nfp", "fed", "通膨", "利率決議",
        "關稅", "tariff", "出口管制", "export control", "制裁", "sanction",
        "台海", "台灣海峽", "軍演", "南海", "中東", "美伊", "以伊", "伊朗", "以色列",
        "荷姆茲", "霍爾木茲", "hormuz", "紅海", "胡塞", "油價", "原油", "殖利率",
        "烏克蘭", "俄羅斯", "稀土", "rare earth", "關鍵礦物",
    ]
    finance_terms = [
        "法說", "財報", "營收", "eps", "獲利", "毛利", "財測", "展望",
        "guidance", "earnings", "revenue", "conference", "q1", "q2", "q3", "q4",
    ]
    if any(k.lower() in text for k in macro_geo_terms):
        return 14
    if any(k.lower() in text for k in finance_terms):
        return 60
    return 30


def _tw_news_recent_enough(pub: str, title: str = "", query: str = "") -> bool:
    dt = _parse_tw_pub_date(pub)
    if not dt:
        return False
    try:
        now = datetime.now(ZoneInfo("UTC"))
        # RC24 final rule: prediction news must be current-year and recent.
        # 2025 archives or stale 2026 rows may appear in Google News RSS, but
        # must not enter evidence / confidence.
        if dt.year < max(2026, now.year):
            return False
        age = max(0, int((now - dt.astimezone(ZoneInfo("UTC"))).total_seconds() // 86400))
        return age <= _tw_news_ttl_days(title, query)
    except Exception:
        return False


def _google_news(query: str, limit: int = 12) -> List[NewsItem]:
    try:
        import requests
        # Query still allows up to 60d so financial event rows can be captured,
        # then per-title TTL below removes stale non-financial rows.
        current_year = max(2026, datetime.now(ZoneInfo("UTC")).year)
        q = f"({query}) {current_year} after:{current_year}-01-01 when:60d"
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"})
        text = requests.get(url, timeout=4, headers={"User-Agent": "Mozilla/5.0 TINO-RC24-TW-NewsTimeGuard"}).text
        root = ET.fromstring(text)
        items: List[NewsItem] = []
        seen = set()
        for item in root.findall(".//item"):
            title = re.sub(r"\s+", " ", item.findtext("title") or "").strip()
            link = item.findtext("link") or "https://news.google.com/"
            pub = item.findtext("pubDate") or ""
            if not title or title in seen or not _tw_news_recent_enough(pub, title, query):
                continue
            seen.add(title)
            score, tag = _score_news(title)
            items.append(NewsItem("GoogleNewsTW", _tw_news_time_label(pub), score, tag, title, link))
            if len(items) >= limit:
                break
        return items
    except Exception:
        return []


def _fallback_news(ticker: TickerInfo) -> List[NewsItem]:
    name = ticker.name
    return [
        NewsItem("GoogleNewsTW", "待同步", 0.0, "headline_neutral", f"{name} 2026近端新聞待同步，先看價格/VWAP/法人資券", "https://news.google.com/"),
    ]


def _global_tw_macro_geo_news() -> List[NewsItem]:
    """Fetch shared market-wide macro/geo headlines once per cache window.

    Company-specific queries alone can miss CPI or geopolitical risk.  These
    reserved headlines prevent a busy ticker from starving the shared event
    layer while keeping network work bounded and non-blocking.
    """
    global _TW_GLOBAL_NEWS_CACHE
    now_ts = time_module.time()
    if _TW_GLOBAL_NEWS_CACHE and now_ts - _TW_GLOBAL_NEWS_CACHE[0] < _TW_GLOBAL_NEWS_CACHE_TTL_SEC:
        return list(_TW_GLOBAL_NEWS_CACHE[1])

    queries = (
        '美國 (CPI OR PPI OR PCE OR FOMC OR 非農) 通膨 利率 美債殖利率',
        '(荷姆茲海峽 OR 霍爾木茲海峽 OR Strait of Hormuz OR 美伊衝突 OR 以伊衝突) 油價 殖利率',
        '(美中關稅 OR 出口管制 OR 台灣海峽 OR 軍演 OR 稀土 OR 關鍵礦物) 晶片 供應鏈',
    )
    out: List[NewsItem] = []
    seen: set[str] = set()
    for query in queries:
        for item in _google_news(query, 3):
            key = re.sub(r"\s+", " ", item.title).strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(item)
        if len(out) >= 8:
            break
    _TW_GLOBAL_NEWS_CACHE = (now_ts, out[:8])
    return list(out[:8])


def fetch_tw_news(ticker: TickerInfo) -> List[NewsItem]:
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return _fallback_news(ticker)

    out: List[NewsItem] = []
    seen: set[str] = set()

    def _add(item: NewsItem) -> None:
        key = re.sub(r"\s+", " ", item.title).strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item)

    # Reserve shared Macro/Policy/Geo evidence before company headlines.
    for item in _global_tw_macro_geo_news()[:6]:
        _add(item)

    queries = [
        f"{ticker.name} {_code(ticker.resolved_symbol)} 股票",
        f"{ticker.name} 法說 EPS 營收",
        f"{ticker.name} AI 半導體",
    ]
    for query in queries:
        for item in _google_news(query, 8):
            _add(item)
        if len(out) >= 16:
            break

    reserved = [item for item in out if str(item.tag) in {"macro_event", "policy_geo"}][:5]
    reserved_keys = {re.sub(r"\s+", " ", item.title).strip().lower() for item in reserved}
    company = [
        item for item in out
        if re.sub(r"\s+", " ", item.title).strip().lower() not in reserved_keys
    ]
    company = sorted(company, key=lambda n: (abs(float(n.score)), str(n.time)), reverse=True)
    final = (reserved + company)[:12]
    return final or _fallback_news(ticker)
