# -*- coding: utf-8 -*-
"""Observed cross-market context for TINO V12.2.

Design contracts
----------------
* No fabricated live values. Missing fields stay ``None`` and contribute zero.
* Taiwan index night-session data is accepted only from TAIFEX.
* Correlated market proxies are fetched once and timestamped per field.
* A historical price frame never receives today's proxy values (look-ahead guard).
"""
from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from io import StringIO
import math
import os
import re
import time
from typing import Any, Dict, Iterable, Mapping, Tuple
import copy
from concurrent.futures import ThreadPoolExecutor
from market_proxy_observation import observation_from_history


TAIFEX_NIGHT_URL = "https://www.taifex.com.tw/cht/3/futDailyMarketExcel?marketCode=1"


def _finite(value: Any) -> float | None:
    try:
        text = str(value).replace(",", "").replace("%", "").strip()
        x = float(text)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _pct_change(closes: Iterable[Any]) -> float | None:
    vals = [_finite(x) for x in closes]
    vals = [x for x in vals if x is not None and x > 0]
    if len(vals) < 2:
        return None
    return round((vals[-1] - vals[-2]) / vals[-2] * 100.0, 3)


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()[:10].replace("/", "-")
    try:
        return date.fromisoformat(text)
    except Exception:
        return None


def _flatten_columns(columns: Any) -> list[str]:
    out: list[str] = []
    for col in list(columns):
        if isinstance(col, tuple):
            out.append(" ".join(str(x) for x in col if str(x).lower() != "nan").strip())
        else:
            out.append(str(col).strip())
    return out


def _pick_col(columns: list[str], *needles: str) -> str | None:
    for col in columns:
        norm = re.sub(r"\s+", "", col)
        if all(n in norm for n in needles):
            return col
    return None


def _fetch_taifex_night() -> Dict[str, object]:
    """Fetch the nearest TX contract night-session change from TAIFEX.

    The parser deliberately fails closed.  It never substitutes a Yahoo symbol
    or an index future from another exchange when the official row is absent.
    """
    out: Dict[str, object] = {
        "accepted": False,
        "source": "TAIFEX_TX_NIGHT",
        "change_pct": None,
        "last": None,
        "contract_month": "",
        "session_date": "",
    }
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        out.update({
            "accepted": True,
            "source": "OFFLINE_TEST_TAIFEX_TX_NIGHT",
            "change_pct": -0.55,
            "last": 22120.0,
            "contract_month": "TEST",
            "session_date": date.today().isoformat(),
        })
        return out

    try:
        import requests
        import pandas as pd

        response = requests.get(
            TAIFEX_NIGHT_URL,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html,application/xhtml+xml,application/vnd.ms-excel,*/*",
                "Referer": "https://www.taifex.com.tw/cht/3/futDailyMarket",
            },
            timeout=8,
        )
        response.raise_for_status()
        response.encoding = response.apparent_encoding or response.encoding
        text = response.text

        session_match = re.search(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})[^\n]{0,80}(?:15:00|夜盤)", text)
        if session_match:
            out["session_date"] = f"{int(session_match.group(1)):04d}-{int(session_match.group(2)):02d}-{int(session_match.group(3)):02d}"

        tables = pd.read_html(StringIO(text))
        candidates: list[tuple[str, float, float | None, str]] = []
        for df in tables:
            if df is None or df.empty:
                continue
            df = df.copy()
            df.columns = _flatten_columns(df.columns)
            cols = list(df.columns)
            contract_col = _pick_col(cols, "契約") or _pick_col(cols, "商品") or (cols[0] if cols else None)
            month_col = _pick_col(cols, "到期月份") or _pick_col(cols, "契約月份")
            pct_col = _pick_col(cols, "漲跌%") or _pick_col(cols, "漲跌幅")
            last_col = _pick_col(cols, "最後成交價") or _pick_col(cols, "收盤價")
            if not contract_col or not pct_col:
                continue
            for _, row in df.iterrows():
                contract_text = str(row.get(contract_col, "")).strip().upper()
                # Accept exact TX/TXF/Taiwan Stock Index Futures rows only.
                if not (
                    contract_text in {"TX", "TXF"}
                    or "臺股期貨" in contract_text
                    or "台股期貨" in contract_text
                    or contract_text.startswith("TX ")
                ):
                    continue
                pct = _finite(row.get(pct_col))
                if pct is None or abs(pct) > 15.0:
                    continue
                last = _finite(row.get(last_col)) if last_col else None
                month = str(row.get(month_col, "")).strip() if month_col else ""
                candidates.append((month, pct, last, contract_text))

        if not candidates:
            out["reason"] = "official TX night row not found"
            return out

        # TAIFEX usually lists nearest month first.  Prefer a numeric YYYYMM
        # contract and then its natural order; never select a far contract by price.
        candidates.sort(key=lambda x: (0 if re.fullmatch(r"20\d{4}", x[0].replace("/", "")) else 1, x[0]))
        month, pct, last, _ = candidates[0]
        out.update({
            "accepted": True,
            "change_pct": round(float(pct), 3),
            "last": round(float(last), 3) if last is not None else None,
            "contract_month": month,
        })
        if not out["session_date"]:
            out.update(accepted=False, reason="official night-session date unavailable")
        return out
    except Exception as exc:
        out["reason"] = type(exc).__name__
        return out


@lru_cache(maxsize=8)
def _fetch_cached(fifteen_minute_bucket: int) -> Dict[str, object]:
    del fifteen_minute_bucket
    out: Dict[str, object] = {
        "accepted": False,
        "source": "YahooFinance market proxies + TAIFEX",
        "sox": None,
        "nq": None,
        "qqq": None,
        "vix": None,
        "vix_change": None,
        "smh": None,
        "mu": None,
        "tsm_adr": None,
        "wti": None,
        "brent": None,
        "tx_night": None,
        "as_of": {},
        "symbols": {},
        "observations": {},
        "sessions": {},
        "trends": {},
    }
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        today = date.today().isoformat()
        out.update({
            "accepted": True,
            "source": "OFFLINE_TEST_MARKET_PROXIES",
            "sox": -1.20,
            "nq": -0.45,
            "qqq": -0.40,
            "vix": 20.0,
            "vix_change": 2.1,
            "smh": -1.05,
            "mu": -1.80,
            "tsm_adr": -0.70,
            "wti": 0.0,
            "brent": 0.0,
            "tx_night": -0.55,
            "as_of": {k: today for k in ("sox", "nq", "qqq", "vix", "vix_change", "smh", "mu", "tsm_adr", "wti", "brent", "tx_night")},
            "symbols": {"tx_night": "TAIFEX:TX night"},
        })
        return out

    errors: list[str] = []
    try:
        import yfinance as yf

        maps: Tuple[Tuple[str, str], ...] = (
            ("sox", "^SOX"),
            ("nq", "NQ=F"),
            ("qqq", "QQQ"),
            ("vix", "^VIX"),
            ("smh", "SMH"),
            ("mu", "MU"),
            ("tsm_adr", "TSM"),
            ("wti", "CL=F"),
            ("brent", "BZ=F"),
            ("taiex", "^TWII"),
            ("tpex", "^TWOII"),
        )
        def load_proxy(pair):
            key, symbol = pair
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(
                    period="3mo", interval="1d", auto_adjust=False, timeout=5
                )
                try:
                    metadata = ticker.history_metadata
                except Exception:
                    metadata = {}
                return key, observation_from_history(key, symbol, hist, metadata), None
            except Exception as exc:
                return key, None, type(exc).__name__
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(load_proxy, maps))
        for key, observation, error in results:
            if error:
                errors.append(f"{key}:{error}")
            if observation is None:
                continue
            out["observations"][key] = observation
            if observation["stale"]:
                errors.append(f"{key}:stale")
                continue
            change = observation["change_pct"]
            try:
                if key == "vix":
                    out["vix"] = observation["price"]
                    out["vix_change"] = change
                    out["as_of"]["vix_change"] = observation["quote_at"] or observation["data_date"]
                    out["sessions"]["vix_change"] = observation["session"]
                else:
                    out[key] = change
                out["symbols"][key] = observation["symbol"]
                out["as_of"][key] = observation["quote_at"] or observation["data_date"]
                out["sessions"][key] = observation["session"]
                out["trends"][key] = {field: observation[field] for field in (
                    "consecutive_down", "return_3d_pct", "return_5d_pct", "below_ma20",
                    "drawdown_20d_pct", "completed_as_of", "completed_sessions")}
            except Exception as exc:
                errors.append(f"{key}:{type(exc).__name__}")
    except Exception as exc:
        errors.append(f"yfinance:{type(exc).__name__}")

    night = _fetch_taifex_night()
    night_date = _parse_date(night.get("session_date"))
    night_fresh = night_date is not None and 0 <= (date.today() - night_date).days <= 4
    if night.get("accepted") and night_fresh:
        out["tx_night"] = night.get("change_pct")
        out["tx_night_last"] = night.get("last")
        out["tx_night_contract"] = night.get("contract_month")
        out["as_of"]["tx_night"] = night.get("session_date")
        out["symbols"]["tx_night"] = "TAIFEX:TX night"
        out["sessions"]["tx_night"] = "overnight"
    else:
        out["tx_night_reason"] = night.get("reason") or "official night-session date is stale or missing"

    out["accepted"] = any(
        out.get(k) is not None
        for k in ("sox", "nq", "qqq", "smh", "mu", "tsm_adr", "tx_night", "taiex", "tpex")
    )
    if errors and not out["accepted"]:
        out["reason"] = ";".join(errors[-4:])
    return out


def fetch_market_proxy_context(reference_date: str = "") -> Dict[str, object]:
    """Return a protected copy of the current observed proxy snapshot.

    ``reference_date`` is the price frame's date.  When it is clearly
    historical, the current snapshot is rejected to prevent look-ahead bias.
    """
    ref = _parse_date(reference_date)
    today = date.today()
    if ref is not None and (today - ref).days > 4:
        return {
            "accepted": False,
            "source": "MARKET_PROXY_LOOKAHEAD_GUARD",
            "reason": "historical frame requires historical proxy dataset",
            "reference_date": ref.isoformat(),
            "sox": None, "nq": None, "qqq": None, "vix": None,
            "vix_change": None, "smh": None, "mu": None,
            "tsm_adr": None, "wti": None, "brent": None,
            "tx_night": None, "as_of": {}, "symbols": {},
        }

    raw = _fetch_cached(int(time.time() // 300))
    out = copy.deepcopy(raw)
    out["as_of"] = dict(raw.get("as_of") or {})
    out["symbols"] = dict(raw.get("symbols") or {})
    out["reference_date"] = reference_date or today.isoformat()
    return out

