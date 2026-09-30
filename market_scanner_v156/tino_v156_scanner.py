# =========================================================
# 🌌 TINO Colab Market Scanner V156 — Top10 / Recommend5 Edition
# 目的：掃描台灣上市櫃普通股，尋找「動能、結構、風險報酬」同時合格的觀察標的
# 核心原則：
# 1) 不把任意分數包裝成機率
# 2) KNN 採距離加權 + 時間衰減 + Bayesian shrinkage
# 3) 回測採訊號日收盤後，下一交易日開盤進、收盤出，避免偷看未來
# 4) 進場價、交易成本、停損、目標價分開計算
# 5) 加入資料新鮮度、官方收盤價交叉驗證、樣本品質與失真防護
# =========================================================
# Colab 首次執行請打開下一行安裝
# !pip install -q -U yfinance FinMind numpy pandas==2.2.2 requests tqdm
from __future__ import annotations
import json
import logging
import math
import random
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import requests
import yfinance as yf
from requests.adapters import HTTPAdapter
from tqdm.auto import tqdm
from urllib3.util.retry import Retry
# =========================
# 0. 參數設定
# =========================
@dataclass(frozen=True)
class Config:
    scan_version: str = "TINO_MARKET_SCANNER_V157_SECTOR_DISCOVERY"
    period: str = "3y"
    interval: str = "1d"
    chunk_size: int = 40
    sleep_min: float = 0.35
    sleep_max: float = 0.80
    download_retries: int = 2
    max_download_failure_ratio: float = 0.20  # 超過即視為資料源/網路異常，不發布部分掃描
    min_avg_vol_shares: int = 1_000_000
    min_history_days: int = 120
    min_backtest_trades: int = 20
    knn_lookback: int = 360
    knn_k: int = 25
    knn_prior_strength: float = 12.0
    knn_recency_half_life: float = 126.0
    # 一般股票，非當沖；券商折扣可自行調整 broker_fee_discount
    broker_fee_rate: float = 0.001425
    broker_fee_discount: float = 1.0
    stock_sell_tax_rate: float = 0.003
    slippage_rate_each_side: float = 0.0005
    shortlist_n: int = 10
    recommend_n: int = 5
    top_n_full_detail: int = 5
    active_pool_n: int = 300
    # 候選池先寬後嚴：Top10 後才做推薦仲裁
    max_entry_gap_pct: float = 2.0
    min_profit_factor: float = 1.05
    min_avg_net_ret_pct: float = 0.0
    min_market_edge_pct: float = 0.05
    max_stale_calendar_days: int = 8
    official_close_warn_ratio: float = 0.015
    official_close_reject_ratio: float = 0.05
    # 盤中執行時，丟掉可能尚未完成的今日 Daily Bar
    drop_incomplete_daily_bar: bool = True
    run_self_tests: bool = True
CFG = Config()
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
RUN_DT = datetime.now(TAIPEI_TZ)
RUN_TS = RUN_DT.strftime("%Y-%m-%d %H:%M:%S %Z")
BUY_FEE = CFG.broker_fee_rate * CFG.broker_fee_discount
SELL_FEE = CFG.broker_fee_rate * CFG.broker_fee_discount
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("TINO_V156")
logging.getLogger("yfinance").setLevel(logging.ERROR)
# =========================
# 1. 共用工具
# =========================
def safe_float(x: Any, default: float = np.nan) -> float:
    """兼容 scalar / Series / DataFrame / ndarray。"""
    try:
        if isinstance(x, pd.DataFrame):
            if x.empty:
                return default
            x = x.iloc[-1, -1]
        elif isinstance(x, pd.Series):
            if x.empty:
                return default
            x = x.iloc[-1]
        elif isinstance(x, np.ndarray):
            if x.size == 0:
                return default
            x = x.reshape(-1)[-1]
        if isinstance(x, str):
            x = x.replace(",", "").strip()
            if x in {"", "--", "-", "N/A", "nan", "None"}:
                return default
        v = float(x)
        if not np.isfinite(v):
            return default
        return v
    except (TypeError, ValueError, IndexError):
        return default
def clamp(v: float, lo: float, hi: float) -> float:
    return float(min(max(v, lo), hi))
def weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    mask = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    if mask.sum() == 0:
        return np.nan
    values = values[mask]
    weights = weights[mask]
    order = np.argsort(values)
    values = values[order]
    weights = weights[order]
    cdf = np.cumsum(weights) / np.sum(weights)
    return float(np.interp(q, cdf, values))
def wilson_lower_bound(wins: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    adj = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return clamp((centre - adj) / denom, 0.0, 1.0)
def is_tw_market_open(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(TAIPEI_TZ)
    if now.weekday() >= 5:
        return False
    return dt_time(8, 45) <= now.time() <= dt_time(14, 30)
def tw_tick_size(price: float) -> float:
    """台股一般股票升降單位。"""
    if price < 10:
        return 0.01
    if price < 50:
        return 0.05
    if price < 100:
        return 0.10
    if price < 500:
        return 0.50
    if price < 1000:
        return 1.00
    return 5.00
def round_to_tw_tick(price: float, mode: str = "nearest") -> float:
    if not np.isfinite(price) or price <= 0:
        return np.nan
    tick = tw_tick_size(price)
    units = price / tick
    if mode == "down":
        units = math.floor(units + 1e-12)
    elif mode == "up":
        units = math.ceil(units - 1e-12)
    else:
        units = round(units)
    decimals = 2 if tick < 0.1 else 1 if tick < 1 else 0
    return round(units * tick, decimals)
def conservative_tw_price_bounds(reference_price: float) -> Tuple[float, float]:
    """保守的 tick-rounded 邊界；避免用固定 1.095 / 0.905 失真。"""
    up = round_to_tw_tick(reference_price * 1.10, mode="down")
    dn = round_to_tw_tick(reference_price * 0.90, mode="up")
    return up, dn
def build_http_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.7,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 TINO-Colab-Super-Scanner/154",
            "Accept": "application/json,text/plain,*/*",
        }
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session
HTTP = build_http_session()
def fetch_json(url: str, timeout: int = 20) -> Any:
    response = HTTP.get(url, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, (list, dict)):
        raise ValueError(f"Unexpected JSON type: {type(data)}")
    return data
def is_common_stock(code: str, name: str) -> bool:
    """掃描普通股主體；排除 00 開頭 ETF 與明顯基金/ETN。"""
    if not (len(code) == 4 and code.isdigit()):
        return False
    if code.startswith("00"):
        return False
    upper_name = name.upper()
    blocked = ("ETF", "ETN", "指數", "債券基金", "受益證券")
    return not any(token in upper_name for token in blocked)
def pick_item_number(item: Dict[str, Any], aliases: Iterable[str]) -> float:
    for key in aliases:
        if key in item:
            v = safe_float(item.get(key), np.nan)
            if np.isfinite(v):
                return v
    return np.nan
# =========================
# 2. Universe 與官方快照
# =========================
def get_stock_universe() -> Tuple[list[str], Dict[str, str], Dict[str, Dict[str, Any]], list[str]]:
    tickers: list[str] = []
    name_map: Dict[str, str] = {}
    official_snapshot: Dict[str, Dict[str, Any]] = {}
    # Use industry labels when provided by exchange feeds; missing labels stay explicit.
    sector_map: Dict[str, str] = {}
    warnings: list[str] = []
    sources = [
        (
            "TWSE",
            "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
            "Code",
            "Name",
            ".TW",
        ),
        (
            "TPEx",
            "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes",
            "SecuritiesCompanyCode",
            "CompanyName",
            ".TWO",
        ),
    ]
    for source, url, code_key, name_key, suffix in sources:
        try:
            rows = fetch_json(url)
            if not isinstance(rows, list):
                raise ValueError("API payload is not a list")
            source_count = 0
            for item in rows:
                if not isinstance(item, dict):
                    continue
                code = str(item.get(code_key, "")).strip()
                name = str(item.get(name_key, "")).strip()
                if not is_common_stock(code, name):
                    continue
                ticker = f"{code}{suffix}"
                tickers.append(ticker)
                name_map[ticker] = name or ticker
                official_snapshot[ticker] = {
                    "close": pick_item_number(
                        item,
                        ["ClosingPrice", "Close", "ClosePrice", "LatestPrice"],
                    ),
                    "volume": pick_item_number(
                        item,
                        ["TradeVolume", "TradingShares", "Volume", "成交股數"],
                    ),
                    "source": source,
                }
                industry = next((str(item.get(k, "")).strip() for k in (
                    "Industry", "IndustryName", "SecuritiesIndustryName",
                    "IndustryCategory", "IndustryNameC", "產業別", "產業名稱"
                ) if str(item.get(k, "")).strip()), "")
                if industry:
                    sector_map[ticker] = industry
                source_count += 1
            if source_count == 0:
                warnings.append(f"{source} API 成功但普通股筆數為 0")
        except Exception as exc:
            warnings.append(f"{source} universe 讀取失敗：{exc}")
    tickers = sorted(set(tickers))
    # TWSE basic-company feed is an official fallback for listed-company sectors.
    try:
        rows = fetch_json("https://openapi.twse.com.tw/v1/opendata/t187ap03_L")
        if isinstance(rows, list):
            for item in rows:
                code = str(item.get("公司代號") or item.get("Code") or item.get("公司代碼") or "").strip()
                industry = str(item.get("產業別") or item.get("Industry") or item.get("產業名稱") or "").strip()
                ticker = f"{code}.TW"
                if code and industry and ticker in name_map:
                    sector_map[ticker] = industry
    except Exception as exc:
        warnings.append(f"TWSE 產業分類補充資料略過：{type(exc).__name__}")
    try:
        rows = fetch_json("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_company")
        if isinstance(rows, list):
            for item in rows:
                code = str(item.get("SecuritiesCompanyCode") or item.get("CompanyCode") or item.get("公司代號") or "").strip()
                industry = str(item.get("IndustryName") or item.get("Industry") or item.get("SecuritiesIndustryName") or item.get("產業別") or "").strip()
                ticker = f"{code}.TWO"
                if code and industry and ticker in name_map:
                    sector_map[ticker] = industry
    except Exception as exc:
        warnings.append(f"TPEx 產業分類補充資料略過：{type(exc).__name__}")
    # Preserve the historical four-value API through the attached map field.
    for ticker in tickers:
        official_snapshot.setdefault(ticker, {})["sector"] = sector_map.get(ticker, "未分類")
    return tickers, name_map, official_snapshot, warnings
# =========================
# 3. yfinance 資料下載與正規化
# =========================
def yf_download_raw(tickers: Any, period: str, threads: Any) -> pd.DataFrame:
    return yf.download(
        tickers=tickers,
        period=period,
        interval=CFG.interval,
        group_by="ticker",
        auto_adjust=True,
        repair=True,
        actions=False,
        threads=threads,
        progress=False,
        timeout=25,
    )
def download_chunk(chunk: list[str]) -> pd.DataFrame:
    last_exc: Optional[Exception] = None
    for attempt in range(CFG.download_retries + 1):
        try:
            bulk = yf_download_raw(chunk, CFG.period, threads=True)
            if bulk is not None and not bulk.empty:
                return bulk
        except Exception as exc:
            last_exc = exc
        time.sleep(0.8 * (attempt + 1))
    if last_exc:
        logger.debug("Chunk download failed: %s", last_exc)
    return pd.DataFrame()
def download_single(ticker: str) -> pd.DataFrame:
    try:
        return yf_download_raw(ticker, CFG.period, threads=False)
    except Exception:
        return pd.DataFrame()
def normalize_yf_df(raw: pd.DataFrame, ticker: str, chunk_len: int = 1) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    try:
        df: pd.DataFrame
        if isinstance(raw.columns, pd.MultiIndex):
            lv0 = raw.columns.get_level_values(0)
            lv1 = raw.columns.get_level_values(1)
            if ticker in lv0:
                df = raw[ticker].copy()
            elif ticker in lv1:
                df = raw.xs(ticker, axis=1, level=1).copy()
            else:
                # 單檔下載仍可能回傳 Price/Ticker MultiIndex
                unique_tickers = set(map(str, lv1))
                if chunk_len == 1 and len(unique_tickers) == 1:
                    df = raw.xs(next(iter(unique_tickers)), axis=1, level=1).copy()
                else:
                    return pd.DataFrame()
        else:
            df = raw.copy()
        df.columns = [str(c).title() for c in df.columns]
        required = ["Open", "High", "Low", "Close", "Volume"]
        if not all(c in df.columns for c in required):
            return pd.DataFrame()
        for c in required:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.sort_index()
        df = df[~df.index.duplicated(keep="last")]
        df = df.dropna(subset=required)
        df = df[(df["Open"] > 0) & (df["High"] > 0) & (df["Low"] > 0) & (df["Close"] > 0)]
        df = df[df["Volume"] > 0]
        # 避免盤中 Daily Bar 尚未完成卻被當成正式收盤資料
        if CFG.drop_incomplete_daily_bar and is_tw_market_open() and not df.empty:
            last_date = pd.Timestamp(df.index[-1]).date()
            if last_date == datetime.now(TAIPEI_TZ).date():
                df = df.iloc[:-1].copy()
        return df
    except Exception:
        return pd.DataFrame()
def get_close_series(raw: pd.DataFrame, ticker: str = "") -> pd.Series:
    df = normalize_yf_df(raw, ticker, chunk_len=1)
    if df.empty:
        return pd.Series(dtype=float)
    return df["Close"].dropna()
# =========================
# 4. 大盤背景
# =========================
def get_market_context() -> Dict[str, Any]:
    ctx: Dict[str, Any] = {
        "market_env": "UNKNOWN",
        "market_score": 50.0,
        "twii_last": np.nan,
        "twii_ret20": np.nan,
        "twii_ret1d_pct": np.nan,
        "twii_ret3d_pct": np.nan,
        "twii_ret5d_pct": np.nan,
        "vix": np.nan,
        "stress_score": 50.0,
        "tx_night_force": 0.0,
        "tx_night_date": "NA",
        "tw_night_status": "台指夜盤：未取得",
        "warnings": [],
    }
    # 台灣加權指數：避免用 0050 ETF 取代整體市場
    try:
        raw = yf_download_raw("^TWII", "8mo", threads=False)
        close = get_close_series(raw, "^TWII")
        if len(close) >= 65:
            ma20 = close.rolling(20).mean().iloc[-1]
            ma60 = close.rolling(60).mean().iloc[-1]
            ma20_prev = close.rolling(20).mean().iloc[-6]
            ret20 = close.iloc[-1] / close.iloc[-21] - 1.0
            slope20 = ma20 / ma20_prev - 1.0 if ma20_prev > 0 else 0.0
            if close.iloc[-1] > ma20 > ma60 and slope20 > 0:
                env = "BULL 多頭"
                score = 72 + clamp(ret20 * 100, -8, 8)
            elif close.iloc[-1] < ma20 < ma60 and slope20 < 0:
                env = "BEAR 空頭"
                score = 28 + clamp(ret20 * 100, -8, 8)
            else:
                env = "RANGE 震盪"
                score = 50 + clamp(ret20 * 100 * 1.5, -12, 12)
            ctx.update(
                {
                    "market_env": env,
                    "market_score": clamp(score, 0, 100),
                    "twii_last": float(close.iloc[-1]),
                    "twii_ret20": float(ret20 * 100),
                    "twii_ret1d_pct": float((close.iloc[-1] / close.iloc[-2] - 1) * 100),
                    "twii_ret3d_pct": float((close.iloc[-1] / close.iloc[-4] - 1) * 100),
                    "twii_ret5d_pct": float((close.iloc[-1] / close.iloc[-6] - 1) * 100),
                }
            )
    except Exception as exc:
        ctx["warnings"].append(f"TWII 讀取失敗：{exc}")
    # VIX 是壓力指標，不稱為「恐慌機率」
    try:
        raw = yf_download_raw("^VIX", "1mo", threads=False)
        close = get_close_series(raw, "^VIX")
        if not close.empty:
            vix = float(close.iloc[-1])
            ctx["vix"] = vix
            ctx["stress_score"] = clamp((vix - 12.0) / (35.0 - 12.0) * 100.0, 0.0, 100.0)
    except Exception as exc:
        ctx["warnings"].append(f"VIX 讀取失敗：{exc}")
    # FinMind 官方欄位：futures_id='TX'，夜盤 trading_session='after_market'
    try:
        from FinMind.data import DataLoader
        dl = DataLoader()
        raw = dl.taiwan_futures_daily(
            futures_id="TX",
            start_date=(RUN_DT - timedelta(days=10)).strftime("%Y-%m-%d"),
            end_date=RUN_DT.strftime("%Y-%m-%d"),
        )
        if raw is not None and not raw.empty:
            raw = raw.copy()
            raw["date"] = raw["date"].astype(str)
            raw["volume"] = pd.to_numeric(raw.get("volume"), errors="coerce").fillna(0)
            raw["close"] = pd.to_numeric(raw.get("close"), errors="coerce")
            common_dates = sorted(
                set(raw.loc[raw["trading_session"] == "position", "date"])
                & set(raw.loc[raw["trading_session"] == "after_market", "date"])
            )
            if common_dates:
                d = common_dates[-1]
                same_day = raw[raw["date"] == d]
                day = same_day[same_day["trading_session"] == "position"].sort_values("volume").tail(1)
                night = same_day[same_day["trading_session"] == "after_market"].sort_values("volume").tail(1)
                if not day.empty and not night.empty:
                    day_close = safe_float(day.iloc[-1]["close"])
                    night_close = safe_float(night.iloc[-1]["close"])
                    if day_close > 0 and np.isfinite(night_close):
                        force = clamp((night_close / day_close - 1.0) * 100.0, -8.0, 8.0)
                        ctx["tx_night_force"] = force
                        ctx["tx_night_date"] = d
                        ctx["tw_night_status"] = f"台指夜盤 {d}｜相對日盤 {force:+.2f}%"
    except Exception as exc:
        ctx["warnings"].append(f"FinMind 台指夜盤略過：{exc}")
    return ctx
# =========================
# 5. 特徵工程
# =========================
def add_core_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    prev_close = out["Close"].shift(1)
    tr = pd.concat(
        [
            out["High"] - out["Low"],
            (out["High"] - prev_close).abs(),
            (out["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    # Wilder ATR，比 rolling mean 更接近標準 ATR 定義
    out["tr"] = tr
    out["atr14"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().replace(0, np.nan)
    out["atr_pct"] = out["atr14"] / out["Close"]
    typical_price = (out["High"] + out["Low"] + out["Close"]) / 3.0
    vol_sum20 = out["Volume"].rolling(20).sum().replace(0, np.nan)
    out["vwap20"] = (typical_price * out["Volume"]).rolling(20).sum() / vol_sum20
    out["ma5"] = out["Close"].rolling(5).mean()
    out["ma20"] = out["Close"].rolling(20).mean()
    out["ma60"] = out["Close"].rolling(60).mean()
    out["vol_ma20"] = out["Volume"].rolling(20).mean().replace(0, np.nan)
    out["vol_ratio"] = (out["Volume"] / out["vol_ma20"]).clip(0, 8)
    log_ret5 = np.log(out["Close"] / out["Close"].shift(5))
    momentum_z = log_ret5 / (out["atr_pct"] * math.sqrt(5)).replace(0, np.nan)
    out["m_force"] = np.tanh(momentum_z / 2.0)
    out["deviation"] = out["Close"] / out["vwap20"]
    day_range = (out["High"] - out["Low"]).replace(0, np.nan)
    out["close_pos"] = ((out["Close"] - out["Low"]) / day_range).clip(0, 1)
    out["gap_pct"] = out["Open"] / prev_close - 1.0
    out["ret1"] = out["Close"].pct_change()
    # 訊號在 t 日收盤後產生；交易在 t+1 開盤進、t+1 收盤出
    next_open = out["Open"].shift(-1)
    next_close = out["Close"].shift(-1)
    out["fwd_ret_gross"] = next_close / next_open - 1.0
    entry_cost = next_open * (1 + BUY_FEE + CFG.slippage_rate_each_side)
    exit_value = next_close * (1 - SELL_FEE - CFG.stock_sell_tax_rate - CFG.slippage_rate_each_side)
    out["fwd_ret_net"] = exit_value / entry_cost - 1.0
    return out
# =========================
# 6. KNN：加權、收縮、樣本品質
# =========================
def calc_knn_state(df: pd.DataFrame, current: Dict[str, float]) -> Dict[str, float]:
    features = ["m_force", "deviation", "vol_ratio", "atr_pct", "close_pos"]
    needed = features + ["fwd_ret_net", "fwd_ret_gross"]
    valid = df.dropna(subset=needed).tail(CFG.knn_lookback).copy()
    if len(valid) < max(12, CFG.knn_k // 2):
        return {
            "pA": 33.3,
            "pB": 33.4,
            "pC": 33.3,
            "up_target_ret": max(current["atr_pct"] * 0.75, 0.012),
            "knn_n": len(valid),
            "effective_n": 0.0,
            "knn_quality": 0.0,
            "median_distance": np.nan,
            "knn_status": "INSUFFICIENT",
        }
    mat = valid[features].copy()
    mat["vol_ratio"] = mat["vol_ratio"].clip(upper=5.0)
    cur = pd.Series(
        {
            "m_force": current["m_force"],
            "deviation": current["deviation"],
            "vol_ratio": min(current["vol_ratio"], 5.0),
            "atr_pct": current["atr_pct"],
            "close_pos": current["close_pos"],
        }
    )
    med = mat.median()
    mad = (mat - med).abs().median()
    robust_scale = (mad * 1.4826).replace(0, np.nan)
    fallback_scale = mat.std(ddof=0).replace(0, 1.0)
    robust_scale = robust_scale.fillna(fallback_scale).replace(0, 1.0)
    z_mat = (mat - med) / robust_scale
    z_cur = (cur - med) / robust_scale
    dist = np.sqrt(((z_mat - z_cur) ** 2).mean(axis=1))
    valid["dist"] = dist
    k = min(CFG.knn_k, len(valid))
    samples = valid.nsmallest(k, "dist").copy()
    # 距離權重 + 時間衰減
    distance_weight = 1.0 / np.square(samples["dist"].to_numpy() + 0.25)
    ages = (len(valid) - 1) - np.array([valid.index.get_loc(idx) for idx in samples.index])
    recency_weight = np.power(0.5, ages / CFG.knn_recency_half_life)
    weights = distance_weight * recency_weight
    weights = np.where(np.isfinite(weights), weights, 0.0)
    atr_based_threshold = np.clip(samples["atr_pct"].to_numpy() * 0.35, 0.005, 0.025)
    fwd_net = samples["fwd_ret_net"].to_numpy()
    cls_a = fwd_net > atr_based_threshold
    cls_c = fwd_net < -atr_based_threshold
    cls_b = ~(cls_a | cls_c)
    # Prior 用整段 valid 的母體分布，降低 K=25 時 4% 一跳的鋸齒化
    valid_threshold = np.clip(valid["atr_pct"].to_numpy() * 0.35, 0.005, 0.025)
    base_fwd = valid["fwd_ret_net"].to_numpy()
    base_a = float(np.mean(base_fwd > valid_threshold))
    base_c = float(np.mean(base_fwd < -valid_threshold))
    base_b = max(0.0, 1.0 - base_a - base_c)
    total_w = float(np.sum(weights))
    prior = CFG.knn_prior_strength
    def posterior(mask: np.ndarray, base_rate: float) -> float:
        observed = float(np.sum(weights[mask]))
        return (observed + prior * base_rate) / (total_w + prior)
    p_a = posterior(cls_a, base_a)
    p_b = posterior(cls_b, base_b)
    p_c = posterior(cls_c, base_c)
    norm = p_a + p_b + p_c
    p_a, p_b, p_c = p_a / norm, p_b / norm, p_c / norm
    effective_n = (total_w * total_w / np.sum(weights * weights)) if np.sum(weights * weights) > 0 else 0.0
    median_distance = float(np.median(samples["dist"]))
    distance_quality = math.exp(-median_distance / 2.0)
    sample_quality = min(effective_n / 12.0, 1.0)
    knn_quality = 100.0 * (0.55 * sample_quality + 0.45 * distance_quality)
    positive_mask = cls_a & (samples["fwd_ret_gross"].to_numpy() > 0)
    if positive_mask.sum() >= 3:
        up_target_ret = weighted_quantile(
            samples.loc[positive_mask, "fwd_ret_gross"].to_numpy(),
            weights[positive_mask],
            0.60,
        )
    else:
        up_target_ret = max(current["atr_pct"] * 0.75, 0.012)
    up_target_ret = clamp(up_target_ret, 0.008, 0.095)
    status = "GOOD" if knn_quality >= 55 else "FAIR" if knn_quality >= 35 else "WEAK"
    return {
        "pA": p_a * 100.0,
        "pB": p_b * 100.0,
        "pC": p_c * 100.0,
        "up_target_ret": up_target_ret,
        "knn_n": float(k),
        "effective_n": float(effective_n),
        "knn_quality": float(knn_quality),
        "median_distance": median_distance,
        "knn_status": status,
    }
# =========================
# 7. 無偷看未來的簡化回測與 Kelly
# =========================
def calc_backtest_metrics(df: pd.DataFrame) -> Dict[str, float]:
    signal = (
        (df["m_force"] > 0.10)
        & (df["Close"] > df["vwap20"])
        & (df["ma5"] >= df["ma20"] * 0.995)
        & (df["vol_ratio"] >= 0.80)
        & (df["close_pos"] >= 0.45)
    )
    trades = df.loc[signal, "fwd_ret_net"].dropna().copy()
    n = int(len(trades))
    if n == 0:
        return {
            "trade_n": 0,
            "win_rate": 0.0,
            "wilson_win_lb": 0.0,
            "avg_net_ret": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "kelly_pct": 0.0,
            "bt_quality": 0.0,
            "bt_status": "INSUFFICIENT",
        }
    wins = trades[trades > 0]
    losses = trades[trades < 0]
    win_n = int(len(wins))
    win_rate = win_n / n
    wilson_lb = wilson_lower_bound(win_n, n)
    avg_win = float(wins.mean()) if not wins.empty else 0.0
    avg_loss = abs(float(losses.mean())) if not losses.empty else 0.0
    payoff = avg_win / avg_loss if avg_loss > 0 else 0.0
    profit_factor = float(wins.sum() / abs(losses.sum())) if not losses.empty and losses.sum() != 0 else 0.0
    avg_net = float(trades.mean())
    equity = (1.0 + trades).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    max_drawdown = abs(float(drawdown.min())) if not drawdown.empty else 0.0
    # 保守 Kelly：用 Wilson 下界、1/4 Kelly、樣本數折減，且最多 10%
    if n >= CFG.min_backtest_trades and payoff > 0:
        raw_kelly = wilson_lb - (1.0 - wilson_lb) / payoff
        sample_factor = min(n / 60.0, 1.0)
        kelly_pct = clamp(raw_kelly, 0.0, 0.40) * 0.25 * sample_factor * 100.0
        kelly_pct = min(kelly_pct, 10.0)
    else:
        kelly_pct = 0.0
    sample_score = min(n / 60.0, 1.0)
    edge_score = clamp((avg_net + 0.002) / 0.008, 0.0, 1.0)
    pf_score = clamp((profit_factor - 0.8) / 1.2, 0.0, 1.0)
    bt_quality = 100.0 * (0.40 * sample_score + 0.35 * edge_score + 0.25 * pf_score)
    status = "GOOD" if bt_quality >= 60 else "FAIR" if bt_quality >= 35 else "WEAK"
    return {
        "trade_n": float(n),
        "win_rate": win_rate * 100.0,
        "wilson_win_lb": wilson_lb * 100.0,
        "avg_net_ret": avg_net * 100.0,
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown * 100.0,
        "kelly_pct": kelly_pct,
        "bt_quality": bt_quality,
        "bt_status": status,
    }
# =========================
# 8. 資料品質與候選建構
# =========================
def calc_data_quality(
    ticker: str,
    df: pd.DataFrame,
    official: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    latest_date = pd.Timestamp(df.index[-1]).date()
    stale_days = (datetime.now(TAIPEI_TZ).date() - latest_date).days
    official_close = safe_float((official or {}).get("close"), np.nan)
    yf_close = safe_float(df["Close"].iloc[-1])
    # 盤中因刻意丟掉未完成今日 Bar，不拿即時官方價與昨日收盤硬比
    mismatch_ratio = np.nan
    if not is_tw_market_open() and np.isfinite(official_close) and official_close > 0:
        mismatch_ratio = abs(yf_close / official_close - 1.0)
    quality = 100.0
    flags: list[str] = []
    if stale_days > 4:
        quality -= min(35, (stale_days - 4) * 7)
        flags.append(f"資料距今 {stale_days} 天")
    if np.isfinite(mismatch_ratio):
        if mismatch_ratio > CFG.official_close_warn_ratio:
            quality -= min(35, mismatch_ratio * 500)
            flags.append(f"Yahoo/官方收盤差 {mismatch_ratio*100:.2f}%")
    if len(df) < 260:
        quality -= 12
        flags.append("歷史樣本不足一年")
    one_day_ret = df["Close"].pct_change().abs()
    anomaly_count = int((one_day_ret > 0.20).sum())
    if anomaly_count > 0:
        quality -= min(15, anomaly_count * 3)
        flags.append(f"極端跳價 {anomaly_count} 筆")
    return {
        "data_date": str(latest_date),
        "stale_days": stale_days,
        "official_close": official_close,
        "official_mismatch_pct": mismatch_ratio * 100 if np.isfinite(mismatch_ratio) else np.nan,
        "data_quality": clamp(quality, 0, 100),
        "data_flags": "；".join(flags) if flags else "OK",
    }
def classify_tag(current: Dict[str, float]) -> str:
    if current["vol_ratio"] >= 1.8 and current["close_pos"] >= 0.65 and current["deviation"] > 1.0:
        return "🔥 帶量突破"
    if current["vol_ratio"] >= 1.2 and current["close_pos"] >= 0.55 and current["m_force"] > 0.35:
        return "📈 動能延伸"
    if current["close_pos"] >= 0.70 and current["deviation"] >= 0.995:
        return "💎 承接偏強"
    return "🟡 結構轉強"
def calc_score(row: Dict[str, Any]) -> float:
    momentum = clamp((row["m_force"] + 0.15) / 1.0, 0, 1) * 18
    structure = (
        clamp((row["close_pos"] - 0.35) / 0.55, 0, 1) * 7
        + clamp(1.0 - abs(row["deviation"] - 1.03) / 0.10, 0, 1) * 5
        + (5 if row["ma5"] >= row["ma20"] else 0)
    )
    volume = clamp((row["vol_ratio"] - 0.75) / 1.75, 0, 1) * 10
    knn = clamp((row["pA"] - row["pC"] + 20) / 50, 0, 1) * 12 + row["knn_quality"] / 100 * 8
    edge = clamp((row["setup_edge_pct"] + 0.2) / 2.2, 0, 1) * 12 + clamp((row["reward_risk"] - 0.8) / 2.2, 0, 1) * 8
    backtest = row["bt_quality"] / 100 * 10
    quality = row["data_quality"] / 100 * 7
    score = momentum + structure + volume + knn + edge + backtest + quality
    if row["deviation"] > 1.12:
        score -= min(12, (row["deviation"] - 1.12) * 120)
    if row["pC"] >= 40:
        score -= 6
    if row["market_env"].startswith("BEAR"):
        score -= 5
    if row["stress_score"] >= 70:
        score -= 3
    if row["tx_night_force"] <= -1.2:
        score -= 3
    if row["trade_n"] < CFG.min_backtest_trades:
        score -= 4
    return round(clamp(score, 0, 100), 1)
def classify_execution_status(row: Dict[str, Any]) -> Tuple[str, str]:
    """將候選分成可執行、等待回檔、目標過期與回測否決。"""
    backtest_pass = bool(row["backtest_pass"])
    if not backtest_pass:
        return "REJECTED_BACKTEST", "歷史平均報酬／PF／Kelly 未通過，禁止列為正式買進"
    if row["t1"] <= row["last_p"]:
        return "EXPIRED_TARGET", "現價已達或超過 T1，原交易劇本失效，等待重新計算"
    if row["entry_gap_pct"] > CFG.max_entry_gap_pct:
        return "WAIT_PULLBACK", f"現價高於理想進場區 {row['entry_gap_pct']:.1f}%，不可追價"
    if row["market_edge_pct"] < CFG.min_market_edge_pct:
        return "WAIT_RESET", "按現價計算的成本後 Edge 不足"
    return "ACTIONABLE", "現價、Edge 與歷史驗證均通過，可依停損紀律執行"
def calc_recommendation_score(row: Dict[str, Any]) -> float:
    """Top10 候選池內的二次仲裁分數；回測與可執行性擁有高權重。"""
    base = float(row["score"])
    base += clamp(row["market_edge_pct"], -3, 3) * 4.0
    base += clamp((row["profit_factor"] - 1.0) * 12.0, -12, 12)
    base += clamp(row["avg_net_ret"] * 5.0, -10, 10)
    base += clamp((row["wilson_win_lb"] - 30.0) / 5.0, -5, 5)
    status_bonus = {
        "ACTIONABLE": 18.0,
        "WAIT_PULLBACK": 7.0,
        "WAIT_RESET": 1.0,
        "EXPIRED_TARGET": -8.0,
        "REJECTED_BACKTEST": -18.0,
    }
    base += status_bonus.get(str(row.get("execution_status")), -5.0)
    if row["effective_n"] < 15:
        base -= (15 - row["effective_n"]) * 0.6
    return round(clamp(base, 0, 100), 1)
def build_candidate(
    ticker: str,
    name: str,
    raw_df: pd.DataFrame,
    market_ctx: Dict[str, Any],
    official: Optional[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], str]:
    if len(raw_df) < CFG.min_history_days:
        return None, "history_fail"
    data_quality = calc_data_quality(ticker, raw_df, official)
    mismatch = data_quality["official_mismatch_pct"]
    if data_quality["stale_days"] > CFG.max_stale_calendar_days:
        return None, "stale_fail"
    if np.isfinite(mismatch) and mismatch > CFG.official_close_reject_ratio * 100:
        return None, "official_mismatch_fail"
    df = add_core_features(raw_df)
    last = df.iloc[-1]
    prev = df.iloc[-2]
    required_last = ["Close", "vwap20", "ma5", "ma20", "atr14", "atr_pct", "vol_ratio", "m_force", "deviation", "close_pos"]
    if any(not np.isfinite(safe_float(last.get(c))) for c in required_last):
        return None, "calc_fail"
    avg_vol_10d = safe_float(df["Volume"].tail(10).mean(), 0.0)
    if avg_vol_10d < CFG.min_avg_vol_shares:
        return None, "vol_fail"
    current = {
        "last_p": safe_float(last["Close"]),
        "vwap20": safe_float(last["vwap20"]),
        "ma5": safe_float(last["ma5"]),
        "ma20": safe_float(last["ma20"]),
        "atr14": safe_float(last["atr14"]),
        "atr_pct": safe_float(last["atr_pct"]),
        "vol_ratio": safe_float(last["vol_ratio"], 1.0),
        "m_force": safe_float(last["m_force"], 0.0),
        "deviation": safe_float(last["deviation"], 1.0),
        "close_pos": safe_float(last["close_pos"], 0.5),
        "gap_pct": safe_float(last["gap_pct"], 0.0),
    }
    knn = calc_knn_state(df, current)
    bt = calc_backtest_metrics(df)
    last_p = current["last_p"]
    atr14 = current["atr14"]
    limit_up, limit_dn = conservative_tw_price_bounds(last_p)
    overextended = current["deviation"] > 1.08 or current["gap_pct"] > 0.025
    if overextended:
        support_anchor = max(current["vwap20"] * 1.002, current["ma5"] * 0.995)
        entry = min(last_p, support_anchor)
    else:
        entry = last_p
    entry = round_to_tw_tick(entry, "nearest")
    low10 = safe_float(df["Low"].tail(10).min(), entry - atr14 * 1.5)
    raw_stop = max(current["vwap20"] * 0.975, low10 * 0.995, entry - atr14 * 1.35)
    # 避免停損過緊或過寬：限制在 0.7~2.2 ATR
    raw_stop = clamp(raw_stop, entry - atr14 * 2.2, entry - atr14 * 0.7)
    stop_loss = max(limit_dn, round_to_tw_tick(raw_stop, "down"))
    if stop_loss >= entry:
        stop_loss = round_to_tw_tick(entry - tw_tick_size(entry), "down")
    target_ret = max(knn["up_target_ret"], current["atr_pct"] * 0.65)
    t1 = min(limit_up, round_to_tw_tick(entry * (1 + target_ret), "down"))
    t2_raw = entry + (t1 - entry) * 1.618
    t2 = min(limit_up, round_to_tw_tick(t2_raw, "down"))
    entry_cost = entry * (1 + BUY_FEE + CFG.slippage_rate_each_side)
    t1_value = t1 * (1 - SELL_FEE - CFG.stock_sell_tax_rate - CFG.slippage_rate_each_side)
    stop_value = stop_loss * (1 - SELL_FEE - CFG.stock_sell_tax_rate - CFG.slippage_rate_each_side)
    flat_value = entry * (1 - SELL_FEE - CFG.stock_sell_tax_rate - CFG.slippage_rate_each_side)
    up_ret_pct = (t1_value / entry_cost - 1.0) * 100.0
    flat_ret_pct = (flat_value / entry_cost - 1.0) * 100.0
    down_ret_pct = (stop_value / entry_cost - 1.0) * 100.0
    setup_edge_pct = (
        knn["pA"] / 100 * up_ret_pct
        + knn["pB"] / 100 * flat_ret_pct
        + knn["pC"] / 100 * down_ret_pct
    )
    # 以目前現價直接買進重新計算，不再把等待回檔的 Edge 冒充為現價 Edge
    market_entry_cost = last_p * (1 + BUY_FEE + CFG.slippage_rate_each_side)
    market_up_ret_pct = (t1_value / market_entry_cost - 1.0) * 100.0
    market_flat_value = last_p * (1 - SELL_FEE - CFG.stock_sell_tax_rate - CFG.slippage_rate_each_side)
    market_flat_ret_pct = (market_flat_value / market_entry_cost - 1.0) * 100.0
    market_down_ret_pct = (stop_value / market_entry_cost - 1.0) * 100.0
    market_edge_pct = (
        knn["pA"] / 100 * market_up_ret_pct
        + knn["pB"] / 100 * market_flat_ret_pct
        + knn["pC"] / 100 * market_down_ret_pct
    )
    entry_gap_pct = (last_p / entry - 1.0) * 100.0 if entry > 0 else 999.0
    gross_reward = max(t1 - entry, 0.0)
    gross_risk = max(entry - stop_loss, tw_tick_size(entry))
    reward_risk = gross_reward / gross_risk
    backtest_pass = (
        bt["trade_n"] >= CFG.min_backtest_trades
        and bt["avg_net_ret"] > CFG.min_avg_net_ret_pct
        and bt["profit_factor"] >= CFG.min_profit_factor
        and bt["kelly_pct"] > 0
    )
    # 候選池採寬條件，確保能先比較 Top10；正式推薦由後段仲裁，不代表通過買進。
    trend_ok = current["ma5"] >= current["ma20"] * 0.985
    shortlist_pass = (
        current["m_force"] > 0.0
        and current["deviation"] >= 0.97
        and trend_ok
        and reward_risk >= 0.75
        and knn["knn_quality"] >= 18
        and setup_edge_pct > -0.75
    )
    if not shortlist_pass:
        return None, "edge_fail"
    # 融資線僅保留為假設示意，不參與分數
    margin_call_proxy = round_to_tw_tick(entry * 0.78, "down")
    liquidation_proxy = round_to_tw_tick(entry * 0.72, "down")
    row: Dict[str, Any] = {
        "ticker": ticker,
        "code": ticker.split(".")[0],
        "name": name,
        "data_date": data_quality["data_date"],
        "last_p": last_p,
        "ma5": current["ma5"],
        "ma20": current["ma20"],
        "vwap20": current["vwap20"],
        "atr14": atr14,
        "atr_pct": current["atr_pct"],
        "vol_ratio": current["vol_ratio"],
        "avg_vol_10d": avg_vol_10d,
        "m_force": current["m_force"],
        "deviation": current["deviation"],
        "close_pos": current["close_pos"],
        "gap_pct": current["gap_pct"],
        "tag": classify_tag(current),
        "entry": entry,
        "stop_loss": stop_loss,
        "t1": t1,
        "t2": t2,
        "limit_up_guard": limit_up,
        "limit_dn_guard": limit_dn,
        "reward_risk": reward_risk,
        "up_net_ret_pct": up_ret_pct,
        "flat_net_ret_pct": flat_ret_pct,
        "down_net_ret_pct": down_ret_pct,
        "setup_edge_pct": setup_edge_pct,
        "market_edge_pct": market_edge_pct,
        "entry_gap_pct": entry_gap_pct,
        "market_up_ret_pct": market_up_ret_pct,
        "market_flat_ret_pct": market_flat_ret_pct,
        "market_down_ret_pct": market_down_ret_pct,
        "backtest_pass": backtest_pass,
        **knn,
        **bt,
        **data_quality,
        "market_env": market_ctx.get("market_env", "UNKNOWN"),
        "market_score": market_ctx.get("market_score", 50.0),
        "stress_score": market_ctx.get("stress_score", 50.0),
        "vix": market_ctx.get("vix", np.nan),
        "tx_night_force": market_ctx.get("tx_night_force", 0.0),
        "tw_night_status": market_ctx.get("tw_night_status", "NA"),
        "margin_call_proxy_130": margin_call_proxy,
        "liquidation_proxy_120": liquidation_proxy,
        "margin_proxy_note": "假設融資成數60%、以建議進場價估算；未計整戶擔保、利息與券商處分規則",
    }
    row["score"] = calc_score(row)
    status, reason = classify_execution_status(row)
    row["execution_status"] = status
    row["execution_reason"] = reason
    row["recommendation_score"] = calc_recommendation_score(row)
    row["recommendable"] = status in {"ACTIONABLE", "WAIT_PULLBACK", "WAIT_RESET"}
    return row, "success"


# =========================
# 8A. V157 Market Discovery enrichment (read-only inputs; no prediction-model mutation)
# =========================
def _universe_bar_metrics(ticker: str, df: pd.DataFrame, sector: str) -> Dict[str, Any]:
    """Compact cross-sectional features from the already-downloaded OHLC bars."""
    close = pd.to_numeric(df["Close"], errors="coerce").dropna()
    volume = pd.to_numeric(df["Volume"], errors="coerce").dropna()
    out: Dict[str, Any] = {"ticker": ticker, "sector": sector or "未分類"}
    for days in (1, 3, 5):
        out[f"ret{days}d_pct"] = (close.iloc[-1] / close.iloc[-days - 1] - 1) * 100 if len(close) > days else np.nan
    prior5 = volume.iloc[-6:-1].mean() if len(volume) >= 6 else np.nan
    out["volume_accel_pct"] = (volume.iloc[-1] / prior5 - 1) * 100 if prior5 and prior5 > 0 else np.nan
    out["last_close"] = safe_float(close.iloc[-1]) if len(close) else np.nan
    out["avg_vol_20d"] = safe_float(volume.tail(20).mean()) if len(volume) >= 20 else np.nan
    out["ma20"] = safe_float(close.tail(20).mean()) if len(close) >= 20 else np.nan
    out["ma60"] = safe_float(close.tail(60).mean()) if len(close) >= 60 else np.nan
    out["avg_dollar_volume_20d"] = out["last_close"] * out["avg_vol_20d"] if np.isfinite(out["last_close"]) and np.isfinite(out["avg_vol_20d"]) else np.nan
    # Broad guardrail and rank score are deliberately cheap; KNN/Backtest run only after this funnel.
    trend_ok = (np.isfinite(out["ma20"]) and out["last_close"] >= out["ma20"] * 0.90
                and np.isfinite(out["ma60"]) and out["last_close"] >= out["ma60"] * 0.82)
    liquid_ok = np.isfinite(out["avg_vol_20d"]) and out["avg_vol_20d"] >= CFG.min_avg_vol_shares
    momentum_ok = np.isfinite(out["ret5d_pct"]) and out["ret5d_pct"] >= -18
    out["fast_filter_pass"] = bool(trend_ok and liquid_ok and momentum_ok)
    dollar_score = clamp(50 + math.log10(max(out["avg_dollar_volume_20d"], 1) / 100_000_000) * 12, 0, 100) if np.isfinite(out["avg_dollar_volume_20d"]) else 0
    out["fast_filter_score"] = round(0.50 * clamp(50 + safe_float(out["ret5d_pct"], -50) * 2, 0, 100)
                                      + 0.20 * clamp(50 + safe_float(out["volume_accel_pct"], 0) * 0.20, 0, 100)
                                      + 0.30 * dollar_score, 2)
    return out


def fetch_bulk_institutional_flow() -> pd.DataFrame:
    """One bulk FinMind query, never one request per ticker; unavailable stays NA."""
    try:
        from FinMind.data import DataLoader
        dl = DataLoader()
        end_date = RUN_DT.date()
        start_date = (RUN_DT - timedelta(days=35)).date()
        frame = dl.taiwan_stock_institutional_investors(
            start_date=start_date.isoformat(), end_date=end_date.isoformat()
        )
        if frame is None or frame.empty:
            return pd.DataFrame()
        required = {"date", "stock_id", "buy", "sell"}
        if not required.issubset(frame.columns):
            logger.warning("FinMind 法人資料欄位不完整；Flow 維持 unavailable")
            return pd.DataFrame()
        out = frame.copy()
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
        out["net"] = pd.to_numeric(out["buy"], errors="coerce").fillna(0) - pd.to_numeric(out["sell"], errors="coerce").fillna(0)
        out["code"] = out["stock_id"].astype(str).str.zfill(4)
        return out.dropna(subset=["date"]).groupby(["code", "date"], as_index=False)["net"].sum()
    except Exception as exc:
        logger.info("法人 Flow 未取得，保留明確缺值：%s", type(exc).__name__)
        return pd.DataFrame()


def _flow_features(flow: pd.DataFrame, code: str, avg_volume: float) -> Dict[str, Any]:
    if flow.empty or not np.isfinite(avg_volume) or avg_volume <= 0:
        return {"inst_net_3d": np.nan, "inst_net_5d": np.nan, "inst_net_10d": np.nan,
                "inst_accel_pct": np.nan, "inst_flow_status": "UNAVAILABLE"}
    part = flow[flow["code"] == str(code).zfill(4)].sort_values("date").tail(10)
    if part.empty:
        return {"inst_net_3d": np.nan, "inst_net_5d": np.nan, "inst_net_10d": np.nan,
                "inst_accel_pct": np.nan, "inst_flow_status": "UNAVAILABLE"}
    vals = part["net"].astype(float).to_numpy()
    n3, n5, n10 = float(vals[-3:].sum()), float(vals[-5:].sum()), float(vals.sum())
    prev3 = float(vals[-6:-3].sum()) if len(vals) >= 6 else np.nan
    accel = ((n3 - prev3) / avg_volume * 100) if np.isfinite(prev3) else np.nan
    if n5 > 0 and np.isfinite(prev3) and n3 > prev3:
        status = "ACCELERATING_BUY"
    elif n5 > 0 and (not np.isfinite(prev3) or n3 >= prev3):
        status = "ACCUMULATING"
    elif n5 > 0:
        status = "DECELERATING"
    else:
        status = "DISTRIBUTION"
    return {"inst_net_3d": n3, "inst_net_5d": n5, "inst_net_10d": n10,
            "inst_accel_pct": accel, "inst_flow_status": status}


def enrich_market_discovery(
    candidates: list[Dict[str, Any]], bar_metrics: Dict[str, Dict[str, Any]],
    market_ctx: Dict[str, Any], flow: pd.DataFrame,
) -> None:
    """Attach sector/leader/RS/flow evidence and a transparent shadow score."""
    groups: Dict[str, list[Dict[str, Any]]] = {}
    for item in bar_metrics.values():
        if item["sector"] != "未分類":
            groups.setdefault(item["sector"], []).append(item)
    sector_stats: Dict[str, Dict[str, float]] = {}
    for sector, members in groups.items():
        sector_stats[sector] = {}
        for key in ("ret1d_pct", "ret3d_pct", "ret5d_pct", "volume_accel_pct"):
            vals = [safe_float(x.get(key)) for x in members]
            vals = [v for v in vals if np.isfinite(v)]
            sector_stats[sector][key] = float(np.mean(vals)) if vals else np.nan
        r1 = [safe_float(x.get("ret1d_pct")) for x in members]
        r1 = [v for v in r1 if np.isfinite(v)]
        sector_stats[sector]["breadth_pct"] = 100 * sum(v > 0 for v in r1) / len(r1) if r1 else np.nan
        ranked = sorted(members, key=lambda x: (safe_float(x.get("ret5d_pct"), -999), safe_float(x.get("volume_accel_pct"), -999)), reverse=True)
        for rank, member in enumerate(ranked, 1):
            member["leader_rank"] = rank
            member["sector_size"] = len(ranked)

    market_returns = {d: safe_float(market_ctx.get(f"twii_ret{d}d_pct")) for d in (1, 3, 5)}
    for row in candidates:
        ticker = str(row.get("ticker", ""))
        metric = bar_metrics.get(ticker, {})
        sector = metric.get("sector", "未分類")
        sec = sector_stats.get(sector, {})
        peers = [x for x in groups.get(sector, []) if x.get("ticker") != ticker]
        avg_vol = safe_float(row.get("avg_vol_10d"), np.nan)
        flow_features = _flow_features(flow, ticker.split(".")[0], avg_vol)
        for days in (1, 3, 5):
            stock_ret = safe_float(metric.get(f"ret{days}d_pct"))
            peer_returns = [safe_float(x.get(f"ret{days}d_pct")) for x in peers]
            peer_returns = [v for v in peer_returns if np.isfinite(v)]
            sector_ret = float(np.mean(peer_returns)) if peer_returns else np.nan
            market_ret = market_returns[days]
            row[f"rs_market_{days}d_pct"] = stock_ret - market_ret if np.isfinite(stock_ret) and np.isfinite(market_ret) else np.nan
            row[f"rs_sector_{days}d_pct"] = stock_ret - sector_ret if np.isfinite(stock_ret) and np.isfinite(sector_ret) else np.nan
        row.update({
            "sector": sector,
            "sector_1d_pct": sec.get("ret1d_pct", np.nan),
            "sector_3d_pct": sec.get("ret3d_pct", np.nan),
            "sector_5d_pct": sec.get("ret5d_pct", np.nan),
            "sector_breadth_pct": sec.get("breadth_pct", np.nan),
            "sector_volume_accel_pct": sec.get("volume_accel_pct", np.nan),
            "sector_member_count": len(groups.get(sector, [])),
            "leader_rank": metric.get("leader_rank", np.nan),
            "leader_label": ("LEADER" if metric.get("leader_rank") == 1 else "CO_LEADER" if metric.get("leader_rank", 999) <= 3 else "FOLLOWER") if sector != "未分類" else "UNCLASSIFIED",
            **flow_features,
        })
        # Each component is independently exposed on a 0–100 scale; this score is shadow only.
        stock_edge = clamp(50 + safe_float(row.get("m_force"), 0) * 35 + safe_float(row.get("rs_market_5d_pct"), 0) * 1.2, 0, 100)
        sector_edge = clamp(50 + safe_float(row.get("sector_5d_pct"), 0) * 2 + (safe_float(row.get("sector_breadth_pct"), 50) - 50) * 0.35, 0, 100) if sector != "未分類" else np.nan
        leader_edge = clamp(100 - (safe_float(row.get("leader_rank"), 20) - 1) * 12, 0, 100) if sector != "未分類" else np.nan
        flow_status = row["inst_flow_status"]
        flow_edge = {"ACCELERATING_BUY": 90, "ACCUMULATING": 72, "DECELERATING": 42, "DISTRIBUTION": 18}.get(flow_status, np.nan)
        gap = safe_float(row.get("entry_gap_pct"), np.nan)
        entry_edge = clamp(100 - max(0, gap) * 22 - max(0, 1 - safe_float(row.get("reward_risk"), 0)) * 25, 0, 100)
        components = {"stock": (stock_edge, 30), "sector": (sector_edge, 20), "leader": (leader_edge, 20), "flow": (flow_edge, 15), "entry": (entry_edge, 15)}
        available = [(v, w) for v, w in components.values() if np.isfinite(v)]
        coverage = sum(w for _, w in available)
        row.update({
            "stock_edge_score": stock_edge, "sector_edge_score": sector_edge,
            "leader_edge_score": leader_edge, "institutional_flow_edge_score": flow_edge,
            "entry_edge_score": entry_edge,
            "v157_score_coverage_pct": coverage,
            "v157_total_score": round(sum(v * w for v, w in available) / coverage, 1) if coverage else np.nan,
            "v157_score_mode": "SHADOW_UNCALIBRATED",
        })


def _atomic_json(path: Path, payload: Dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temp.replace(path)


def _load_previous_results(output_dir: Path) -> pd.DataFrame:
    """Read only the prior successful local Colab snapshot, if one exists."""
    manifest_path = output_dir / "run_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") != "success":
            return pd.DataFrame()
        candidates = [output_dir / name for name in manifest.get("artifacts", [])
                      if str(name).startswith("tino_market_scanner_v156_")
                      and str(name).endswith(".csv")
                      and "diagnostics" not in str(name)]
        return pd.read_csv(candidates[0]) if candidates and candidates[0].exists() else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def validate_previous_recommendations(previous: pd.DataFrame, histories: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """T+1 close-bar audit. Same-day target/stop collision is marked ambiguous."""
    if previous.empty or "is_recommended5" not in previous.columns:
        return pd.DataFrame(columns=["ticker", "selection_date", "validation_status"])
    chosen = previous[previous["is_recommended5"].astype(str).str.lower().isin({"true", "1"})]
    rows = []
    for _, old in chosen.iterrows():
        ticker = str(old.get("ticker", ""))
        bars = histories.get(ticker)
        selection_date = pd.to_datetime(old.get("data_date"), errors="coerce")
        if bars is None or bars.empty or pd.isna(selection_date):
            rows.append({"ticker": ticker, "selection_date": str(old.get("data_date", "")), "validation_status": "PENDING_DATA"})
            continue
        dates = pd.to_datetime(bars.index).tz_localize(None)
        future = bars.loc[dates > selection_date.tz_localize(None)]
        if future.empty:
            rows.append({"ticker": ticker, "selection_date": str(old.get("data_date", "")), "validation_status": "PENDING_NEXT_BAR"})
            continue
        bar = future.iloc[0]
        entry, target, stop = (safe_float(old.get(k)) for k in ("entry", "t1", "stop_loss"))
        touched_entry = safe_float(bar.get("Low")) <= entry if np.isfinite(entry) else False
        hit_target = safe_float(bar.get("High")) >= target if np.isfinite(target) else False
        hit_stop = safe_float(bar.get("Low")) <= stop if np.isfinite(stop) else False
        if not touched_entry:
            status = "NOT_TRIGGERED"
        elif hit_target and hit_stop:
            status = "AMBIGUOUS_TARGET_STOP_SAME_BAR"
        elif hit_stop:
            status = "STOP_TOUCHED"
        elif hit_target:
            status = "T1_TOUCHED"
        else:
            status = "ENTRY_TOUCHED_NO_TARGET"
        entry_to_close = (safe_float(bar.get("Close")) / entry - 1) * 100 if entry > 0 else np.nan
        rows.append({"ticker": ticker, "selection_date": str(old.get("data_date", "")),
                     "validation_date": pd.Timestamp(future.index[0]).date().isoformat(),
                     "entry": entry, "t1": target, "stop_loss": stop,
                     "next_open": safe_float(bar.get("Open")), "next_high": safe_float(bar.get("High")),
                     "next_low": safe_float(bar.get("Low")), "next_close": safe_float(bar.get("Close")),
                     "entry_to_close_pct": entry_to_close, "validation_status": status})
    return pd.DataFrame(rows)


def add_rotation_labels(candidates: list[Dict[str, Any]], previous: pd.DataFrame) -> None:
    old = {}
    if not previous.empty and "ticker" in previous.columns:
        old = {str(r.get("ticker")): r for _, r in previous.iterrows()}
    for row in candidates:
        prior = old.get(str(row.get("ticker")))
        current_rank = safe_float(row.get("leader_rank"), np.nan)
        prior_rank = safe_float(prior.get("leader_rank"), np.nan) if prior is not None else np.nan
        if prior is None or not np.isfinite(current_rank) or not np.isfinite(prior_rank):
            row["leader_rotation"] = "NO_PRIOR_COMPARABLE_SNAPSHOT"
        elif current_rank < prior_rank:
            row["leader_rotation"] = "LEADER_GAINING"
        elif current_rank > prior_rank:
            row["leader_rotation"] = "LEADER_LOSING"
        else:
            row["leader_rotation"] = "LEADER_RANK_STABLE"
# =========================
# 9. 報告輸出
# =========================
def fmt_num(v: Any, digits: int = 2, na: str = "NA") -> str:
    x = safe_float(v, np.nan)
    return f"{x:.{digits}f}" if np.isfinite(x) else na
def print_strategy_report(df_top: pd.DataFrame) -> None:
    if df_top.empty:
        print("⚠️ 今日未找到符合條件標的。")
        return
    print("\n" + "=" * 92)
    print(f"🚦 TINO V156 Top10→Recommend5 戰略報告｜Top {len(df_top)}")
    print("=" * 92)
    for _, r in df_top.iterrows():
        print(f"\n## {r['ticker']} {r['name']}｜資料日 {r['data_date']}")
        print(
            f"現價 {r['last_p']:.2f}｜5MA {r['ma5']:.2f}｜20MA {r['ma20']:.2f}｜"
            f"20日量價均價 {r['vwap20']:.2f}｜ATR14 {r['atr14']:.2f}"
        )
        print(
            f"狀態 {r['tag']}｜執行 {r['execution_status']}｜推薦分 {r['recommendation_score']:.1f}/100｜資料品質 {r['data_quality']:.0f}/100｜"
            f"官方價差 {fmt_num(r['official_mismatch_pct'])}%"
        )
        print(
            f"大盤 {r['market_env']}｜市場分 {r['market_score']:.0f}｜VIX壓力 {r['stress_score']:.0f}/100｜"
            f"{r['tw_night_status']}"
        )
        print("\n1. 【KNN 情境：非保證機率】")
        print(
            f"A順風 {r['pA']:.1f}%｜B盤整 {r['pB']:.1f}%｜C失敗 {r['pC']:.1f}%｜"
            f"鄰居 {int(r['knn_n'])}｜有效樣本 {r['effective_n']:.1f}｜品質 {r['knn_quality']:.0f}/100"
        )
        print("\n2. 【交易計畫】")
        print(
            f"進場參考 {r['entry']:.2f}｜T1 {r['t1']:.2f}｜T2 {r['t2']:.2f}｜"
            f"Stop {r['stop_loss']:.2f}｜風報比 {r['reward_risk']:.2f}"
        )
        print(
            f"理想價成本後：A {r['up_net_ret_pct']:+.2f}%｜B {r['flat_net_ret_pct']:+.2f}%｜C {r['down_net_ret_pct']:+.2f}%｜Setup Edge {r['setup_edge_pct']:+.2f}%"
        )
        print(
            f"現價距進場 {r['entry_gap_pct']:+.2f}%｜Market Edge {r['market_edge_pct']:+.2f}%｜判定：{r['execution_reason']}"
        )
        print("\n3. 【歷史同條件驗證】")
        print(
            f"樣本 {int(r['trade_n'])}｜勝率 {r['win_rate']:.1f}%｜Wilson下界 {r['wilson_win_lb']:.1f}%｜"
            f"平均淨報酬 {r['avg_net_ret']:+.2f}%｜Profit Factor {r['profit_factor']:.2f}"
        )
        print(
            f"歷史交易序列最大回撤 {r['max_drawdown']:.1f}%｜1/4 Kelly上限 {r['kelly_pct']:.1f}%｜"
            f"回測品質 {r['bt_quality']:.0f}/100"
        )
        print("\n4. 【Truth Guard】")
        print(f"資料旗標：{r['data_flags']}")
        print(
            f"融資追繳示意130% {r['margin_call_proxy_130']:.2f}｜處分示意120% {r['liquidation_proxy_120']:.2f}｜"
            f"{r['margin_proxy_note']}"
        )
        print("-" * 92)
def export_results(df_results: pd.DataFrame, stats: Counter, market_ctx: Dict[str, Any], output_dir: Optional[Path] = None) -> Tuple[str, str, str]:
    output_dir = Path(output_dir or Path.cwd())
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(TAIPEI_TZ).strftime("%Y%m%d_%H%M%S")
    csv_name = f"tino_market_scanner_v156_{stamp}.csv"
    diag_name = f"tino_market_scanner_v156_diagnostics_{stamp}.csv"
    meta_name = f"tino_market_scanner_v156_metadata_{stamp}.json"
    csv_path, diag_path, meta_path = output_dir / csv_name, output_dir / diag_name, output_dir / meta_name
    df_results.to_csv(csv_path, index=False, encoding="utf-8-sig")
    pd.DataFrame([dict(stats)]).to_csv(diag_path, index=False, encoding="utf-8-sig")
    metadata = {
        "scan_version": CFG.scan_version,
        "run_time_taipei": RUN_TS,
        "config": CFG.__dict__,
        "stats": dict(stats),
        "market_context": market_ctx,
        "notes": [
            "先建立 Top10 候選池，再由可執行性、現價 Edge 與歷史回測仲裁推薦五檔。",
            "KNN 情境百分比為模型後驗分布，不是保證命中率。",
            "回測採下一交易日開盤進、收盤出，包含手續費、股票交易稅與滑價假設。",
            "20日量價均價使用 Typical Price × Volume，避免把日K計算誤稱為盤中真實 VWAP。",
        ],
    }
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return str(csv_path), str(diag_path), str(meta_path)
# =========================
# 10. 自我測試
# =========================
def run_self_tests() -> None:
    assert tw_tick_size(9.99) == 0.01
    assert tw_tick_size(10.0) == 0.05
    assert tw_tick_size(50.0) == 0.10
    assert tw_tick_size(100.0) == 0.50
    assert tw_tick_size(1000.0) == 5.00
    up, dn = conservative_tw_price_bounds(87.8)
    assert up <= 87.8 * 1.10 + 1e-9
    assert dn >= 87.8 * 0.90 - 1e-9
    n = 500
    idx = pd.bdate_range("2024-01-01", periods=n)
    rng = np.random.default_rng(154)
    rets = rng.normal(0.0004, 0.018, n)
    close = 50 * np.cumprod(1 + rets)
    open_ = close * (1 + rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * (1 + rng.uniform(0.001, 0.02, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0.001, 0.02, n))
    vol = rng.integers(1_000_000, 8_000_000, n)
    test_df = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol}, index=idx)
    feat = add_core_features(test_df)
    assert feat["atr14"].dropna().shape[0] > 400
    assert feat["fwd_ret_net"].dropna().shape[0] == n - 1
    last = feat.dropna(subset=["vwap20", "atr14", "m_force"]).iloc[-1]
    current = {
        "m_force": float(last["m_force"]),
        "deviation": float(last["deviation"]),
        "vol_ratio": float(last["vol_ratio"]),
        "atr_pct": float(last["atr_pct"]),
        "close_pos": float(last["close_pos"]),
    }
    knn = calc_knn_state(feat, current)
    assert abs(knn["pA"] + knn["pB"] + knn["pC"] - 100.0) < 1e-6
    funnel_df = pd.DataFrame({
        "Close": np.linspace(40, 60, 90),
        "Volume": np.full(90, 2_000_000),
    }, index=pd.bdate_range("2024-01-01", periods=90))
    funnel = _universe_bar_metrics("TEST.TW", funnel_df, "半導體")
    assert funnel["fast_filter_pass"] and funnel["avg_vol_20d"] == 2_000_000
    flow = pd.DataFrame({"code": ["2330"] * 6, "date": pd.date_range("2024-01-01", periods=6), "net": [10, 10, 10, 10, 20, 30]})
    assert _flow_features(flow, "2330", 100)["inst_flow_status"] == "ACCELERATING_BUY"
    previous = pd.DataFrame([{"ticker": "TEST.TW", "data_date": "2024-01-01", "is_recommended5": True,
                              "entry": 10.0, "t1": 11.0, "stop_loss": 9.0}])
    collided = {"TEST.TW": pd.DataFrame([{"Open": 10.0, "High": 11.2, "Low": 8.8, "Close": 10.0}],
                                         index=pd.to_datetime(["2024-01-02"]))}
    audit = validate_previous_recommendations(previous, collided)
    assert audit.iloc[0]["validation_status"] == "AMBIGUOUS_TARGET_STOP_SAME_BAR"
    print("✅ Self tests passed")
# =========================
# 11. 主流程
# =========================
def main(mode: str = "manual", output_dir: Optional[str] = None, download: bool = False) -> pd.DataFrame:
    """Run a full TW market scan. `scheduled` is non-interactive for an external scheduler."""
    if mode not in {"manual", "scheduled"}:
        raise ValueError("mode must be manual or scheduled")
    run_dt = datetime.now(TAIPEI_TZ)
    out = Path(output_dir or Path.cwd()).expanduser().resolve()
    previous_results = _load_previous_results(out)
    print("=" * 92)
    print(f"🌌 {CFG.scan_version} 啟動｜mode={mode}")
    print(f"台北時間：{run_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print("=" * 92)
    if CFG.run_self_tests:
        run_self_tests()
    tickers, name_map, official_snapshot, universe_warnings = get_stock_universe()
    stats: Counter = Counter()
    stats["total"] = len(tickers)
    for msg in universe_warnings:
        print(f"⚠️ {msg}")
    if not tickers:
        raise RuntimeError("Universe 為空；停止，不能發布空快照。")
    print(f"✅ Universe：{len(tickers)} 檔普通股")
    market_ctx = get_market_context()
    print(
        f"📊 大盤：{market_ctx['market_env']}｜市場分 {market_ctx['market_score']:.0f}｜"
        f"VIX {fmt_num(market_ctx['vix'])}｜壓力 {market_ctx['stress_score']:.0f}/100｜"
        f"{market_ctx['tw_night_status']}"
    )
    for msg in market_ctx.get("warnings", []):
        print(f"⚠️ {msg}")
    candidates: list[Dict[str, Any]] = []
    universe_metrics: Dict[str, Dict[str, Any]] = {}
    historical_frames: Dict[str, pd.DataFrame] = {}
    previous_symbols = set(previous_results.get("ticker", pd.Series(dtype=str)).astype(str)) if not previous_results.empty else set()
    deep_frames: Dict[str, pd.DataFrame] = {}
    institutional_flow = fetch_bulk_institutional_flow()
    start = time.time()
    print(f"\n📥 全市場掃描｜批次 {CFG.chunk_size}｜period={CFG.period}｜adjusted OHLC")
    for i in tqdm(range(0, len(tickers), CFG.chunk_size)):
        chunk = tickers[i : i + CFG.chunk_size]
        bulk = download_chunk(chunk)
        for ticker in chunk:
            try:
                df = normalize_yf_df(bulk, ticker, chunk_len=len(chunk))
                if df.empty:
                    fallback = download_single(ticker)
                    df = normalize_yf_df(fallback, ticker, chunk_len=1)
                    if df.empty:
                        stats["yf_fail"] += 1
                        continue
                    stats["yf_fallback_success"] += 1
                sector = official_snapshot.get(ticker, {}).get("sector", "未分類")
                if not df.empty:
                    metric = _universe_bar_metrics(ticker, df, sector)
                    universe_metrics[ticker] = metric
                    if ticker in previous_symbols:
                        historical_frames[ticker] = df
                    if metric["fast_filter_pass"]:
                        deep_frames[ticker] = df
                        if len(deep_frames) > CFG.active_pool_n:
                            worst = min(deep_frames, key=lambda code: universe_metrics[code]["fast_filter_score"])
                            deep_frames.pop(worst, None)
                        stats["fast_filter_pass"] += 1
                    else:
                        stats["fast_filter_reject"] += 1
            except Exception as exc:
                stats["calc_fail"] += 1
                logger.debug("%s failed: %s", ticker, exc)
        time.sleep(random.uniform(CFG.sleep_min, CFG.sleep_max))
    elapsed = time.time() - start
    failure_ratio = stats["yf_fail"] / max(1, len(tickers))
    if failure_ratio > CFG.max_download_failure_ratio:
        raise RuntimeError(f"Yahoo 最終下載失敗率 {failure_ratio:.1%} 超過門檻 {CFG.max_download_failure_ratio:.0%}；不發布不完整快照。")
    active_tickers = sorted(deep_frames, key=lambda code: universe_metrics[code]["fast_filter_score"], reverse=True)
    stats["active_pool"] = len(active_tickers)
    stats["deep_scanned"] = 0
    print(f"\n⚡ Fast Filter：{stats['fast_filter_pass']} 檔通過；Deep Scanner 實際處理 {len(active_tickers)} 檔（上限 {CFG.active_pool_n}），不為補足名額放寬條件。")
    for ticker in tqdm(active_tickers, desc="Deep Scanner"):
        try:
            df = deep_frames[ticker]
            row, status = build_candidate(
                ticker=ticker,
                name=name_map.get(ticker, ticker),
                raw_df=df,
                market_ctx=market_ctx,
                official=official_snapshot.get(ticker),
            )
            stats[status] += 1
            stats["deep_scanned"] += 1
            if status == "success" and row is not None:
                candidates.append(row)
        except Exception as exc:
            stats["calc_fail"] += 1
            logger.debug("%s deep-scan failed: %s", ticker, exc)
    historical_frames.update(deep_frames)
    print("\n" + "=" * 70)
    print("🔍 TINO V156 診斷面板")
    print(f"總標的數：{stats['total']}")
    print(f"Yahoo失敗：{stats['yf_fail']}｜單檔備援成功：{stats['yf_fallback_success']}")
    print(f"歷史不足：{stats['history_fail']}｜流動性不足：{stats['vol_fail']}")
    print(f"資料過舊：{stats['stale_fail']}｜官方價差拒絕：{stats['official_mismatch_fail']}")
    print(f"計算異常：{stats['calc_fail']}｜條件/Edge不足：{stats['edge_fail']}｜Fast Filter 排除：{stats['fast_filter_reject']}")
    print(f"成功候選：{stats['success']}｜耗時：{elapsed/60:.1f} 分鐘")
    print("=" * 70)
    df_results = pd.DataFrame(candidates)
    if df_results.empty:
        raise RuntimeError("沒有符合 Truth Guard 的候選；本次不輸出成功快照，保留舊版結果。")
    enrich_market_discovery(candidates, universe_metrics, market_ctx, institutional_flow)
    add_rotation_labels(candidates, previous_results)
    df_results = pd.DataFrame(candidates)
    df_results = (
        df_results.drop_duplicates(subset=["ticker"])
        .sort_values(
            by=["recommendation_score", "market_edge_pct", "setup_edge_pct", "knn_quality"],
            ascending=[False, False, False, False],
        )
        .reset_index(drop=True)
    )
    df_results["shortlist_rank"] = np.arange(1, len(df_results) + 1)
    df_results["is_top10"] = df_results["shortlist_rank"] <= CFG.shortlist_n
    top10 = df_results.head(CFG.shortlist_n).copy()
    # Never pad the recommendation list with expired or failed-backtest names.
    eligible = top10[top10["execution_status"].isin(["ACTIONABLE", "WAIT_PULLBACK", "WAIT_RESET"])].copy()
    status_order = {"ACTIONABLE": 0, "WAIT_PULLBACK": 1, "WAIT_RESET": 2}
    recommended = (
        eligible.assign(_status_order=eligible["execution_status"].map(status_order))
        .sort_values(["_status_order", "recommendation_score"], ascending=[True, False])
        .head(CFG.recommend_n)
        .drop(columns=["_status_order"])
        .copy()
    )
    recommended_codes = set(recommended["ticker"].tolist())
    df_results["is_recommended5"] = df_results["ticker"].isin(recommended_codes)
    display_cols = [
        "ticker", "name", "data_date", "last_p", "score", "tag", "execution_status",
        "recommendation_score", "setup_edge_pct", "market_edge_pct", "entry_gap_pct",
        "reward_risk", "pA", "pB", "pC", "knn_quality", "trade_n", "win_rate",
        "avg_net_ret", "kelly_pct", "entry", "t1", "t2", "stop_loss", "data_quality",
        "sector", "leader_rank", "leader_label", "sector_5d_pct", "sector_breadth_pct",
        "rs_market_5d_pct", "rs_sector_5d_pct", "inst_net_5d", "inst_flow_status",
        "stock_edge_score", "sector_edge_score", "leader_edge_score",
        "institutional_flow_edge_score", "entry_edge_score", "v157_total_score", "v157_score_coverage_pct",
    ]
    print("\n🏆 第一階段：Top 10 候選池")
    top_table = top10[display_cols].copy()
    try:
        from IPython.display import display
        display(top_table)
    except Exception:
        print(top_table.to_string(index=False))
    print(f"\n⭐ 第二階段：合格推薦（最多 {CFG.recommend_n} 檔；不為湊數降門檻）")
    rec_table = recommended[display_cols].copy()
    try:
        from IPython.display import display
        display(rec_table)
    except Exception:
        print(rec_table.to_string(index=False))
    print_strategy_report(recommended.head(CFG.top_n_full_detail))
    csv_name, diag_name, meta_name = export_results(df_results, stats, market_ctx, out)
    validation = validate_previous_recommendations(previous_results, historical_frames)
    validation_name = f"tino_market_scanner_v156_tplus1_validation_{datetime.now(TAIPEI_TZ).strftime('%Y%m%d_%H%M%S')}.csv"
    validation.to_csv(out / validation_name, index=False, encoding="utf-8-sig")
    summary = {
        "status": "success",
        "run_time_taipei": datetime.now(TAIPEI_TZ).isoformat(),
        "mode": mode,
        "universe_count": len(tickers),
        "candidate_count": len(df_results),
        "top10_count": len(top10),
        "recommendation_count": len(recommended),
        "recommendations": recommended["ticker"].astype(str).tolist(),
        "artifacts": [Path(x).name for x in (csv_name, diag_name, meta_name)] + [validation_name],
        "v157_score_mode": "SHADOW_UNCALIBRATED",
        "sector_coverage_pct": round(100 * sum(m.get("sector") != "未分類" for m in universe_metrics.values()) / max(1, len(universe_metrics)), 1),
        "institutional_flow_status": "available" if not institutional_flow.empty else "unavailable",
        "tplus1_validation_count": len(validation),
        "tplus1_validation_file": validation_name,
    }
    _atomic_json(out / "run_manifest.json", summary)
    print(f"\n✅ 結果：{csv_name}\n✅ 診斷：{diag_name}\n✅ Metadata：{meta_name}")
    print(f"✅ 推薦 {len(recommended)} 檔；清單可少於 5 檔。")
    if download and mode == "manual":
        try:
            from google.colab import files
            files.download(csv_name)
        except Exception:
            print("ℹ️ 下載介面不可用；檔案仍保留於輸出資料夾。")
    return df_results

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="TINO V156 Market Scanner")
    parser.add_argument("--mode", choices=["manual", "scheduled"], default="manual")
    parser.add_argument("--output-dir", default="./scanner_output")
    parser.add_argument("--download", action="store_true", help="Colab manual mode: open CSV download")
    args = parser.parse_args()
    RESULTS = main(mode=args.mode, output_dir=args.output_dir, download=args.download)
