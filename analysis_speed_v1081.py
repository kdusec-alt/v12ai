# -*- coding: utf-8 -*-
"""Bounded performance layer for TINO V1081.

Goals
-----
- Reduce first-query Taiwan latency by loading independent official blocks with
  at most three workers.
- Reuse market-wide/calendar/news results for a short, explicit TTL.
- Never cache the full FinalForecast, never duplicate Streamlit serialization,
  and never start background workers.
- Preserve exact data-source fallbacks and fail back to the original function
  whenever the speed layer cannot prove compatibility.

The module is process-local and bounded.  It deliberately avoids unbounded
``lru_cache`` objects for large payloads because previous Cloud runs reached
high RSS/thread counts.
"""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading
import time
from typing import Any, Callable, Dict, Hashable, Mapping


SCHEMA = "TINO_ANALYSIS_SPEED_V1081"
_INSTALLED = False


class _TTLCache:
    def __init__(self, max_items: int = 48):
        self.max_items = max(4, int(max_items))
        self._rows: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0

    def get(self, key: Hashable, ttl: float) -> Any | None:
        now = time.monotonic()
        with self._lock:
            row = self._rows.get(key)
            if row is None:
                self.misses += 1
                return None
            stamp, value = row
            if now - stamp > max(0.0, float(ttl)):
                self._rows.pop(key, None)
                self.misses += 1
                return None
            self._rows.move_to_end(key)
            self.hits += 1
            try:
                return deepcopy(value)
            except Exception:
                return value

    def put(self, key: Hashable, value: Any) -> None:
        with self._lock:
            try:
                stored = deepcopy(value)
            except Exception:
                stored = value
            self._rows[key] = (time.monotonic(), stored)
            self._rows.move_to_end(key)
            while len(self._rows) > self.max_items:
                self._rows.popitem(last=False)

    def get_or_load(self, key: Hashable, ttl: float, loader: Callable[[], Any]) -> Any:
        cached = self.get(key, ttl)
        if cached is not None:
            return cached
        value = loader()
        self.put(key, value)
        return value

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "size": len(self._rows),
                "max_items": self.max_items,
                "hits": self.hits,
                "misses": self.misses,
            }


_NEWS_CACHE = _TTLCache(32)
_MARKET_CACHE = _TTLCache(16)
_OFFICIAL_CACHE = _TTLCache(64)
_CALENDAR_CACHE = _TTLCache(4)


def _symbol_key(value: Any) -> str:
    return str(value or "").strip().upper().replace(" ", "")


def fetch_news_cached(
    fetcher: Callable[..., Any],
    symbol: str,
    *,
    force_refresh: bool = False,
    ttl_seconds: float = 75.0,
) -> list[Any]:
    """Short-lived final-news cache; force-refresh always bypasses it."""
    key = ("news", _symbol_key(symbol))
    if force_refresh:
        rows = list(fetcher(symbol, force_refresh=True) or [])
        _NEWS_CACHE.put(key, rows)
        return rows
    cached = _NEWS_CACHE.get(key, ttl_seconds)
    if cached is not None:
        return list(cached or [])
    rows = list(fetcher(symbol) or [])
    _NEWS_CACHE.put(key, rows)
    return rows


def seed_news_cache(symbol: str, rows: Any) -> Dict[str, Any]:
    """Publish a force-refreshed watcher result to the next full analysis.

    The event watcher already paid the network cost and passed V1079 timestamp
    guards.  Reusing that exact bounded list makes the immediate reassessment
    both faster and immune to a stale 75-second cache entry.
    """
    values = list(rows or [])
    _NEWS_CACHE.put(("news", _symbol_key(symbol)), values)
    return {"schema": SCHEMA, "symbol": _symbol_key(symbol), "rows": len(values)}


def cached_market_proxy_context(
    fetcher: Callable[..., Any],
    price_date: str = "",
    *,
    market: str = "",
    ttl_seconds: float = 90.0,
) -> Dict[str, Any]:
    """Avoid refetching the same market-wide context during render fragments."""
    key = ("market_proxy", str(market or "").upper(), str(price_date or "")[:10])
    value = _MARKET_CACHE.get_or_load(
        key,
        ttl_seconds,
        lambda: dict(fetcher(str(price_date or "")) or {}),
    )
    return dict(value or {})


def _accepted_ttl(value: Any, accepted_ttl: float, failed_ttl: float = 30.0) -> float:
    if isinstance(value, Mapping) and value.get("accepted") is True:
        return accepted_ttl
    return failed_ttl


def _cached_loader(
    key: Hashable,
    loader: Callable[[], Any],
    *,
    accepted_ttl: float,
    failed_ttl: float = 30.0,
) -> Any:
    cached = _OFFICIAL_CACHE.get(key, accepted_ttl)
    if cached is not None:
        return cached
    value = loader()
    ttl = _accepted_ttl(value, accepted_ttl, failed_ttl)
    # Failed rows are stored with their own timestamp; ``get`` cannot carry a
    # per-row TTL, so encode the shorter expiry in the key bucket.
    # Cache only accepted official rows.  A failed provider response must
    # remain retryable and must not become the apparent official truth.
    if ttl >= accepted_ttl:
        _OFFICIAL_CACHE.put(key, value)
    return value


def _install_calendar_guard() -> None:
    try:
        import data_sources as ds
    except Exception:
        return
    if getattr(ds, "_v1081_calendar_guard_installed", False):
        return
    original = getattr(ds, "ensure_global_macro_calendar", None)
    if not callable(original):
        return

    def ensure_global_macro_calendar_v1081():
        return _CALENDAR_CACHE.get_or_load(
            ("global_macro_calendar",),
            300.0,
            original,
        )

    ds.ensure_global_macro_calendar = ensure_global_macro_calendar_v1081
    ds._v1081_calendar_guard_installed = True


def _install_tw_official_parallelism() -> None:
    """Patch only the independent official-block stage, with a safe fallback."""
    try:
        import data_sources_tw as tw
    except Exception:
        return
    if getattr(tw, "_v1081_official_parallel_installed", False):
        return
    original = getattr(tw, "_merge_official_context", None)
    required = (
        "fetch_yahoo_institutional", "fetch_yahoo_margin",
        "_fetch_finmind_inst", "_fetch_finmind_margin",
        "_empty_inst", "_empty_margin", "validate_official_block",
        "fetch_tw_fundamental_crosscheck", "_fetch_taifex_foreign_futures",
        "_macro_forward_context", "_tv_pressure_context",
        "_tv_pressure_wait", "_apply_v9_verified_contract",
        "_market_foreign_flow_v2_snapshot",
    )
    if not callable(original) or any(not hasattr(tw, name) for name in required):
        return

    def merge_official_context_v1081(
        ticker,
        context,
        price_date,
        *,
        closes=None,
        last=None,
        vwap=None,
        previous_close=None,
    ):
        if getattr(tw, "os", None) is not None and tw.os.environ.get("TINO_OFFLINE_TEST") == "1":
            return original(
                ticker, context, price_date,
                closes=closes, last=last, vwap=vwap, previous_close=previous_close,
            )
        started = time.perf_counter()
        symbol = str(getattr(ticker, "resolved_symbol", "") or "")
        date_key = str(price_date or "")[:10]
        output = dict(context or {})

        def load_inst():
            def work():
                try:
                    return tw.validate_official_block(
                        tw.fetch_yahoo_institutional(symbol, price_date), price_date, "三大法人"
                    )
                except Exception as yahoo_exc:
                    try:
                        return tw.validate_official_block(
                            tw._fetch_finmind_inst(symbol, price_date), price_date, "三大法人"
                        )
                    except Exception as finmind_exc:
                        return tw._empty_inst(
                            price_date,
                            f"法人抓取失敗：Yahoo {type(yahoo_exc).__name__} / FinMind {type(finmind_exc).__name__}",
                        )
            return _cached_loader(("inst", symbol, date_key), work, accepted_ttl=900.0)

        def load_margin():
            def work():
                try:
                    return tw.validate_official_block(
                        tw.fetch_yahoo_margin(symbol, price_date), price_date, "資券"
                    )
                except Exception as yahoo_exc:
                    try:
                        return tw.validate_official_block(
                            tw._fetch_finmind_margin(symbol, price_date), price_date, "資券"
                        )
                    except Exception as finmind_exc:
                        return tw._empty_margin(
                            price_date,
                            f"資券抓取失敗：Yahoo {type(yahoo_exc).__name__} / FinMind {type(finmind_exc).__name__}",
                        )
            return _cached_loader(("margin", symbol, date_key), work, accepted_ttl=900.0)

        def load_fundamental():
            def work():
                try:
                    return tw.fetch_tw_fundamental_crosscheck(symbol, price_date)
                except Exception as exc:
                    return {
                        "accepted": False,
                        "source": "TW_FUNDAMENTAL_FETCH_ERROR",
                        "reason": f"fundamental error:{type(exc).__name__}",
                    }
            return _cached_loader(("fundamental", symbol, date_key), work, accepted_ttl=1800.0)

        def load_futures():
            def work():
                try:
                    return tw.validate_official_block(
                        tw._fetch_taifex_foreign_futures(price_date), price_date, "外資期貨"
                    )
                except Exception as exc:
                    return {
                        "accepted": False,
                        "source": "TAIFEX_FUTURES_FETCH_FAILED",
                        "date": "",
                        "reason": f"外資期貨抓取失敗：{type(exc).__name__}",
                    }
            return _cached_loader(("futures", date_key), work, accepted_ttl=300.0)

        def load_macro():
            def work():
                try:
                    return tw._macro_forward_context(price_date)
                except Exception:
                    return None
            return _cached_loader(("macro", date_key), work, accepted_ttl=300.0)

        try:
            # Three workers are intentional: lower latency without recreating
            # the high thread/RSS pressure seen in earlier Cloud runs.
            with ThreadPoolExecutor(max_workers=3, thread_name_prefix="tino-official") as pool:
                futures = {
                    "inst": pool.submit(load_inst),
                    "margin": pool.submit(load_margin),
                    "fundamental": pool.submit(load_fundamental),
                    "futures": pool.submit(load_futures),
                    "macro": pool.submit(load_macro),
                }
                results = {name: future.result() for name, future in futures.items()}

            output["inst"] = results["inst"]
            output["margin"] = results["margin"]
            output["fundamental"] = results["fundamental"]
            output["futures"] = results["futures"]
            if isinstance(results.get("macro"), Mapping):
                output["macro"] = dict(results["macro"])

            try:
                output["tv_pressure"] = tw._tv_pressure_context(
                    symbol,
                    price_date,
                    closes or [],
                    float(last if last is not None else 0.0),
                    float(vwap if vwap is not None else (last if last is not None else 0.0)),
                    output.get("chip_proxy", {}),
                    previous_close=previous_close,
                    inst=output.get("inst", {}),
                )
            except Exception:
                output["tv_pressure"] = tw._tv_pressure_wait(
                    price_date, "匯率差公式資料待同步｜不顯示假數字"
                )

            output = tw._apply_v9_verified_contract(ticker, output, price_date)
            try:
                if "price_snapshot" not in output and last is not None:
                    current = float(last)
                    scoped_vwap = float(vwap if vwap is not None else current)
                    output["price_snapshot"] = {
                        "last": current,
                        "vwap": scoped_vwap,
                        "vwap_state": "VWAP 上方" if current >= scoped_vwap else "VWAP 下方",
                    }
                flow_v2 = tw._market_foreign_flow_v2_snapshot(price_date, output)
                output["foreign_flow_v2"] = flow_v2
                if isinstance(flow_v2, Mapping) and isinstance(flow_v2.get("tv_pressure"), Mapping):
                    output["tv_pressure"] = dict(flow_v2["tv_pressure"])
            except Exception:
                pass

            output["_analysis_speed_v1081"] = {
                "schema": SCHEMA,
                "official_context_parallel": True,
                "worker_limit": 3,
                "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 2),
                "cache": _OFFICIAL_CACHE.stats(),
                "decision_influence": False,
            }
            return output
        except Exception as exc:
            fallback = original(
                ticker, context, price_date,
                closes=closes, last=last, vwap=vwap, previous_close=previous_close,
            )
            try:
                fallback = dict(fallback or {})
                fallback["_analysis_speed_v1081"] = {
                    "schema": SCHEMA,
                    "official_context_parallel": False,
                    "fallback": type(exc).__name__,
                    "decision_influence": False,
                }
            except Exception:
                pass
            return fallback

    tw._merge_official_context = merge_official_context_v1081
    tw._v1081_official_parallel_installed = True


def install_analysis_speed_guards() -> Dict[str, Any]:
    global _INSTALLED
    if not _INSTALLED:
        _install_calendar_guard()
        _install_tw_official_parallelism()
        _INSTALLED = True
    return {
        "schema": SCHEMA,
        "installed": _INSTALLED,
        "news_cache": _NEWS_CACHE.stats(),
        "market_cache": _MARKET_CACHE.stats(),
        "official_cache": _OFFICIAL_CACHE.stats(),
        "calendar_cache": _CALENDAR_CACHE.stats(),
    }


def run_analysis_pipeline(
    symbol: str,
    macro: str,
    live_data: bool,
    *,
    fetch_price: Callable[[str], Any],
    fetch_news: Callable[..., Any],
    build_learning_signals: Callable[[str], Any],
    orchestrate: Callable[..., Any],
    mark_runtime_stage: Callable[..., Any] | None = None,
) -> Any:
    """One foreground analysis with bounded overlap and trace-only timings."""
    install_analysis_speed_guards()
    stage = mark_runtime_stage if callable(mark_runtime_stage) else (lambda *args, **kwargs: None)
    timings: Dict[str, float] = {}
    total_started = time.perf_counter()

    started = time.perf_counter()
    stage("analysis_fetch_price_start", symbol=symbol)
    price = fetch_price(symbol)
    stage("analysis_fetch_price_done", symbol=symbol)
    timings["price_ms"] = (time.perf_counter() - started) * 1000.0

    # News retrieval is the long I/O path; profile learning is a bounded local
    # read.  Running only these two in parallel saves wall-clock time while the
    # official price block has already completed and the ticker route is known.
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="tino-analysis") as pool:
        news_future = pool.submit(fetch_news_cached, fetch_news, symbol)
        learning_future = pool.submit(build_learning_signals, symbol)
        news = list(news_future.result() or [])
        extra_signals = list(learning_future.result() or [])
    stage("analysis_fetch_news_done", symbol=symbol)
    timings["news_learning_ms"] = (time.perf_counter() - started) * 1000.0

    started = time.perf_counter()
    forecast = orchestrate(price, macro, news_items=news, extra_signals=extra_signals)
    stage("analysis_orchestrate_done", symbol=symbol)
    timings["orchestrate_ms"] = (time.perf_counter() - started) * 1000.0
    timings["total_ms"] = (time.perf_counter() - total_started) * 1000.0

    try:
        card = dict(getattr(forecast, "decision_card", {}) or {})
        card["_analysis_performance_v1081"] = {
            "schema": SCHEMA,
            "price_ms": round(timings["price_ms"], 2),
            "news_learning_ms": round(timings["news_learning_ms"], 2),
            "orchestrate_ms": round(timings["orchestrate_ms"], 2),
            "total_ms": round(timings["total_ms"], 2),
            "worker_limits": {"official": 3, "foreground": 2},
            "cache": {
                "news": _NEWS_CACHE.stats(),
                "market": _MARKET_CACHE.stats(),
                "official": _OFFICIAL_CACHE.stats(),
                "calendar": _CALENDAR_CACHE.stats(),
            },
            "decision_influence": False,
            "full_forecast_cached": False,
        }
        forecast.decision_card = card
    except Exception:
        pass
    return forecast


def performance_cache_health() -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "installed": _INSTALLED,
        "news": _NEWS_CACHE.stats(),
        "market": _MARKET_CACHE.stats(),
        "official": _OFFICIAL_CACHE.stats(),
        "calendar": _CALENDAR_CACHE.stats(),
        "background_workers": 0,
    }
