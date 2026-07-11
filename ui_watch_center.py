# -*- coding: utf-8 -*-
from __future__ import annotations

import html
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from memory_store import MEMORY_DIR, read_prediction_log
from ticker_resolver import resolve_ticker
from tino_persistent_store import (
    load_ledger,
    get_watch_symbols,
    set_watch_symbols,
    add_watch_symbol,
    import_symbols_explicitly,
    remove_watch_symbol,
    storage_status,
    ensure_memory_initialized_bootsafe,
)

WATCH_LEDGER_PATH = MEMORY_DIR / "tino_memory_ledger.json"
# RC2.4: Watch Center must not auto-import Tino holdings/Core Watchlist.
# Defaults are intentionally empty; users add/import explicitly.
DEFAULT_WATCH: List[str] = []


def _esc(x: Any) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def _normalize_symbol(raw: str) -> str:
    s = str(raw or "").strip().upper()
    if not s:
        return ""
    try:
        return resolve_ticker(s).resolved_symbol.upper()
    except Exception:
        return s.replace(" ", "")


def _load_watchlist() -> List[str]:
    """Load Watch Center list from the single Memory Ledger source.

    RC2.4 rule: ledger only.  No holdings/core/default backfill.
    If the ledger exists and symbols is empty, it means the user intentionally
    removed everything.
    """
    try:
        ensure_memory_initialized_bootsafe(default_symbols=DEFAULT_WATCH, migrate=True, path=WATCH_LEDGER_PATH)
        ledger = load_ledger(WATCH_LEDGER_PATH, default_symbols=DEFAULT_WATCH, initialize_if_missing=True)
        return get_watch_symbols(ledger)[:60]
    except Exception:
        return []


def _save_watchlist(items: List[str]) -> bool:
    try:
        ok, _ledger = set_watch_symbols(items, path=WATCH_LEDGER_PATH, default_symbols=DEFAULT_WATCH)
        return bool(ok)
    except Exception:
        return False


def _ledger_status_label() -> str:
    try:
        ss = storage_status(WATCH_LEDGER_PATH)
        if ss.get("last_write_ok") and ss.get("last_verify_ok"):
            return "Memory Ledger：PASS"
        if ss.get("exists"):
            return "Memory Ledger：待驗證"
        return "Memory Ledger：尚未建立"
    except Exception:
        return "Memory Ledger：讀取失敗"


def _get_query_param(st, key: str) -> str:
    try:
        value = st.query_params.get(key, "")
        if isinstance(value, list):
            value = value[0] if value else ""
        return str(value or "").strip()
    except Exception:
        try:
            data = st.experimental_get_query_params()
            value = data.get(key, [""])
            return str(value[0] if isinstance(value, list) else value).strip()
        except Exception:
            return ""


def _clear_query_param(st, key: str) -> None:
    try:
        if key in st.query_params:
            del st.query_params[key]
        return
    except Exception:
        pass
    try:
        params = st.experimental_get_query_params()
        params.pop(key, None)
        st.experimental_set_query_params(**params)
    except Exception:
        pass


def _apply_card_remove_request(st) -> None:
    target = _normalize_symbol(_get_query_param(st, "watch_remove"))
    if not target:
        return
    # Lock navigation before rerun.  Removing a card must stay on Watch Center
    # and must never trigger full TINO analysis.
    st.session_state["main_view"] = "watch"
    st.session_state["watch_center_active"] = True
    st.session_state["watch_autorun_symbol"] = ""
    st.session_state["suppress_auto_once"] = True
    try:
        remove_watch_symbol(target, path=WATCH_LEDGER_PATH, default_symbols=DEFAULT_WATCH, remember_hidden=True)
        st.session_state["watch_center_items"] = _load_watchlist()
    except Exception:
        current = list(st.session_state.get("watch_center_items", _load_watchlist()))
        st.session_state["watch_center_items"] = [x for x in current if _normalize_symbol(x) != target]
        _save_watchlist(st.session_state["watch_center_items"])
    _clear_query_param(st, "watch_remove")
    st.rerun()


def _recent_symbols(limit: int = 20) -> List[str]:
    out: List[str] = []
    for row in reversed(read_prediction_log(300)):
        s = _normalize_symbol(str(row.get("ticker") or row.get("symbol") or ""))
        if s and s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def _latest_prediction_map(limit: int = 800) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for row in reversed(read_prediction_log(limit)):
        s = _normalize_symbol(str(row.get("ticker") or row.get("symbol") or ""))
        if s and s not in latest:
            latest[s] = row
    return latest


def _fmt_price(v: Any) -> str:
    try:
        x = float(v)
        if abs(x) >= 1000:
            return f"{x:,.0f}"
        if abs(x) >= 100:
            return f"{x:,.1f}"
        return f"{x:,.2f}"
    except Exception:
        return "--"


def _fmt_pct(v: Any) -> str:
    try:
        return f"{float(v):+.2f}%"
    except Exception:
        return "--"


def _fmt_time(v: Any) -> str:
    txt = str(v or "").strip()
    if not txt:
        return "--"
    try:
        dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
        return dt.strftime("%m/%d %H:%M")
    except Exception:
        return txt.replace("T", " ")[:16]


def _symbol_title(symbol: str) -> tuple[str, str, str, str]:
    try:
        t = resolve_ticker(symbol)
        code = t.resolved_symbol.split(".")[0] if t.market == "TW" else t.resolved_symbol
        return code, t.resolved_symbol, t.name, t.market
    except Exception:
        return symbol, symbol, symbol, "US"


def _quick_quote(symbol: str) -> Dict[str, Any]:
    """Fetch light quote for Watch Center.

    This is intentionally not a full TINO analysis. It avoids news/fundamental/
    institutional parsing so a 10-30 name watchlist stays responsive.
    """
    normalized = _normalize_symbol(symbol)
    t = resolve_ticker(normalized)
    row: Dict[str, Any] = {
        "input": normalized,
        "symbol": t.resolved_symbol,
        "name": t.name,
        "market": t.market,
        "currency": t.currency,
        "accepted": False,
        "source": "待同步",
    }
    if t.market == "TW":
        try:
            from data_sources_tw_live_price import fetch_twse_mis_live_price, fetch_google_finance_reference
            q = fetch_twse_mis_live_price(t.resolved_symbol)
            if not q.get("accepted"):
                g = fetch_google_finance_reference(t.resolved_symbol)
                if g.get("accepted"):
                    q = g
            if q.get("accepted"):
                last = float(q.get("last"))
                prev = float(q.get("previous_close") or last)
                chg_pct = (last - prev) / prev * 100.0 if prev else 0.0
                vwap = float(q.get("vwap") or last)
                row.update({
                    "accepted": True,
                    "last": last,
                    "change_pct": chg_pct,
                    "vwap_state": "VWAP上" if last >= vwap else "VWAP下",
                    "time": str(q.get("raw_time") or q.get("price_date") or ""),
                    "source": str(q.get("source") or "TW_QUOTE"),
                })
                return row
        except Exception as exc:
            row["source"] = f"TW_QUOTE_ERROR:{type(exc).__name__}"
    else:
        try:
            import yfinance as yf
            hist = yf.Ticker(t.resolved_symbol).history(period="5d", interval="1d", auto_adjust=False, timeout=5)
            if hist is not None and not hist.empty:
                hist = hist.dropna(subset=["Close"])
                if len(hist) >= 1:
                    last = float(hist["Close"].iloc[-1])
                    prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else last
                    row.update({
                        "accepted": True,
                        "last": last,
                        "change_pct": (last - prev) / prev * 100.0 if prev else 0.0,
                        "vwap_state": "日K",
                        "time": str(getattr(hist.index[-1], "date", lambda: hist.index[-1])()),
                        "source": "YahooFinance_Light",
                    })
                    return row
        except Exception as exc:
            row["source"] = f"US_QUOTE_ERROR:{type(exc).__name__}"
    return row


def _cached_watch_quotes(st, symbols: List[str], refresh_bucket: int) -> List[Dict[str, Any]]:
    @st.cache_data(show_spinner=False, ttl=30)
    def _inner(items: tuple[str, ...], bucket: int) -> List[Dict[str, Any]]:
        return [_quick_quote(x) for x in items]
    return _inner(tuple(symbols), refresh_bucket)


def _watch_css(st) -> None:
    st.markdown(
        """
        <style>
        .watch-shell{margin-top:10px;color:#f8fbff;}
        .watch-shell h2{font-weight:1000;letter-spacing:.2px;color:#f8fbff;margin-bottom:6px;}
        .watch-help{color:#c8d8ea;font-size:13px;margin:2px 0 14px;font-weight:750;}

        /* Streamlit controls: keep native widgets readable in dark theme. */
        .watch-controls label,.watch-controls p,.watch-controls span{color:#f1f5f9!important;font-weight:800;}
        .watch-controls input{background:#061827!important;color:#ffffff!important;border-color:rgba(80,220,255,.55)!important;}
        .watch-controls input::placeholder{color:#90a4b8!important;}
        .watch-controls [data-baseweb="select"]{background:#f8fafc!important;color:#111827!important;border-radius:10px!important;}
        .watch-controls [data-baseweb="select"] *{color:#111827!important;}
        .watch-controls [data-baseweb="tag"]{background:#ff4d5f!important;color:#ffffff!important;border-radius:8px!important;}
        .watch-controls [data-baseweb="tag"] *{color:#ffffff!important;}
        .watch-controls button{border:1px solid rgba(255,217,106,.58)!important;background:#111821!important;color:#fff4b8!important;font-weight:950!important;border-radius:14px!important;}

        .watch-card{
            position:relative;min-height:235px;border:1px solid rgba(54,230,255,.42);border-radius:20px;
            padding:16px 17px 14px;margin:0 0 10px;
            background:linear-gradient(180deg,rgba(7,23,39,.99),rgba(1,9,18,.99));
            box-shadow:0 16px 36px rgba(0,0,0,.36), inset 0 0 0 1px rgba(255,255,255,.03);
            overflow:hidden;
        }
        .watch-card:before{content:"";position:absolute;left:0;top:0;right:0;height:3px;background:rgba(80,220,255,.45);}
        .watch-card.up{border-color:rgba(47,255,171,.58);box-shadow:0 0 0 1px rgba(47,255,171,.10) inset,0 16px 36px rgba(0,0,0,.36)}
        .watch-card.up:before{background:rgba(52,255,195,.85);}
        .watch-card.down{border-color:rgba(255,95,132,.62);box-shadow:0 0 0 1px rgba(255,95,132,.10) inset,0 16px 36px rgba(0,0,0,.36)}
        .watch-card.down:before{background:rgba(255,111,142,.85);}
        .watch-card.flat{border-color:rgba(255,217,106,.50)}
        .watch-card.flat:before{background:rgba(255,217,106,.72);}

        .watch-top{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:12px;}
        .watch-code{font-size:20px;font-weight:1000;color:#ffffff;letter-spacing:.2px;line-height:1.12;text-shadow:0 0 8px rgba(255,255,255,.10);}
        .watch-name{font-size:12px;color:#bfe6ff;font-weight:900;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:170px;}
        .watch-badge{font-size:12px;font-weight:1000;border-radius:999px;padding:5px 10px;background:rgba(255,217,106,.16);color:#fff1a8;border:1px solid rgba(255,217,106,.52);white-space:nowrap;margin-right:28px;}
        .watch-x{position:absolute;right:13px;top:12px;width:26px;height:26px;border-radius:999px;border:1px solid rgba(255,255,255,.18);background:rgba(255,77,95,.18);color:#ffffff!important;text-decoration:none!important;font-size:18px;font-weight:1000;line-height:23px;text-align:center;z-index:5;box-shadow:0 0 12px rgba(0,0,0,.25);}
        .watch-x:hover{background:rgba(255,77,95,.82);border-color:rgba(255,255,255,.52);color:#ffffff!important;}
        .watch-price{font-size:34px;font-weight:1000;color:#ffffff;line-height:1.04;margin-top:4px;letter-spacing:.2px;}
        .watch-curr{font-size:12px;color:#b6c9dd;font-weight:950;margin-left:4px;}
        .watch-move{font-size:16px;font-weight:1000;margin:6px 0 12px;}
        .watch-move.up{color:#38ffc7}.watch-move.down{color:#ff6f91}.watch-move.flat{color:#e5e7eb}

        .watch-grid{display:grid;grid-template-columns:1fr 1.12fr;gap:7px 12px;margin-top:10px;font-size:13px;font-weight:900;}
        .watch-k{color:#95ddff;}
        .watch-v{color:#ffffff;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
        .watch-note{margin-top:10px;padding:7px 9px;border-radius:10px;background:rgba(255,217,106,.08);color:#ffe59a;font-weight:900;font-size:12px;border:1px solid rgba(255,217,106,.18);}
        .watch-footer{display:flex;justify-content:space-between;gap:8px;margin-top:10px;color:#bed1e5;font-size:11px;font-weight:850;}
        .watch-empty{border:1px dashed rgba(255,217,106,.50);border-radius:16px;padding:20px;color:#fff0a6;background:rgba(255,217,106,.08);font-weight:900;}

        /* Button below each card */
        div[data-testid="column"] div.stButton > button{
            width:100%;min-height:42px;border-radius:14px!important;border:1px solid rgba(255,217,106,.55)!important;
            background:linear-gradient(180deg,rgba(28,31,36,.98),rgba(13,18,28,.98))!important;
            color:#fff1a8!important;font-weight:1000!important;font-size:16px!important;margin:0 0 18px!important;
            box-shadow:0 6px 18px rgba(0,0,0,.22)!important;
        }
        div[data-testid="column"] div.stButton > button:hover{border-color:rgba(255,238,153,.9)!important;color:#ffffff!important;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _prediction_values(pred: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not pred:
        return {"close": "待完整分析", "hi": "", "lo": "", "conf": "--", "ready": "0"}
    close = pred.get("today_close_est")
    if close in (None, ""):
        close = pred.get("next_close_est")
    hi = pred.get("next_high_est")
    lo = pred.get("next_low_est")
    ready = "1" if close not in (None, "") else "0"
    return {
        "close": _fmt_price(close) if ready == "1" else "待完整分析",
        "hi": _fmt_price(hi) if hi not in (None, "") else "",
        "lo": _fmt_price(lo) if lo not in (None, "") else "",
        "conf": f"{float(pred.get('confidence')):.0f}%" if pred.get("confidence") not in (None, "") else "--",
        "ready": ready,
    }


def _status(row: Dict[str, Any], pred: Optional[Dict[str, Any]]) -> str:
    if not row.get("accepted"):
        return "待同步"
    pct = float(row.get("change_pct") or 0.0)
    vwap = str(row.get("vwap_state") or "")
    if pct > 1.0 and "上" in vwap:
        return "轉強觀察"
    if pct < -1.0 and "下" in vwap:
        return "偏弱防守"
    if "下" in vwap:
        return "觀察不追"
    if "上" in vwap:
        return "可觀察"
    return "持平觀察"


def _card_html(row: Dict[str, Any], pred: Optional[Dict[str, Any]]) -> str:
    code, symbol, name, market = _symbol_title(str(row.get("symbol") or row.get("input") or ""))
    remove_href = f"?watch_remove={html.escape(_normalize_symbol(symbol), quote=True)}"
    pct = float(row.get("change_pct") or 0.0) if row.get("accepted") else 0.0
    cls = "up" if pct > 0 else "down" if pct < 0 else "flat"
    predv = _prediction_values(pred)
    price = _fmt_price(row.get("last"))
    curr = str(row.get("currency") or ("TWD" if market == "TW" else "USD"))
    status = _status(row, pred)
    source = str(row.get("source") or "")
    if len(source) > 22:
        source = source[:22] + "…"
    range_text = f"{predv['lo']}～{predv['hi']}" if predv.get("lo") and predv.get("hi") else "點分析建立"
    conf_text = predv.get("conf") or "--"
    note = "" if predv.get("ready") == "1" else '<div class="watch-note">尚未有完整 TINO 預測，點下方「分析」建立。</div>'
    return f"""
    <div class="watch-card {cls}">
      <a class="watch-x" href="{remove_href}" title="移除觀察">×</a>
      <div class="watch-top">
        <div><div class="watch-code">{_esc(code)}｜{_esc(name)}</div><div class="watch-name">{_esc(symbol)}</div></div>
        <div class="watch-badge">{_esc(status)}</div>
      </div>
      <div class="watch-price">{_esc(price)} <span class="watch-curr">{_esc(curr)}</span></div>
      <div class="watch-move {cls}">{_esc(_fmt_pct(row.get('change_pct')))}</div>
      <div class="watch-grid">
        <div class="watch-k">TINO預估</div><div class="watch-v">{_esc(predv['close'])}</div>
        <div class="watch-k">區間</div><div class="watch-v">{_esc(range_text)}</div>
        <div class="watch-k">位置</div><div class="watch-v">{_esc(row.get('vwap_state') or '觀察')}</div>
        <div class="watch-k">信心</div><div class="watch-v">{_esc(conf_text)}</div>
      </div>
      {note}
      <div class="watch-footer"><span>{_esc(_fmt_time(row.get('time')))}</span><span>{_esc(source)}</span></div>
    </div>
    """


def _render_cards(st, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        st.markdown("<div class='watch-empty'>尚未加入觀察股票。</div>", unsafe_allow_html=True)
        return
    pred_map = _latest_prediction_map()
    cols_per_row = 4
    for i in range(0, len(rows), cols_per_row):
        cols = st.columns(cols_per_row, gap="small")
        for j, r in enumerate(rows[i:i + cols_per_row]):
            with cols[j]:
                pred = pred_map.get(_normalize_symbol(str(r.get("symbol") or r.get("input") or "")))
                st.markdown(_card_html(r, pred), unsafe_allow_html=True)
                if st.button("分析", key=f"watch_analyze_{_normalize_symbol(str(r.get('symbol') or r.get('input')))}", use_container_width=True):
                    target = _normalize_symbol(str(r.get("symbol") or r.get("input") or ""))
                    st.session_state["symbol"] = target
                    st.session_state["typed_symbol"] = target
                    st.session_state["ticker_text_input"] = target
                    st.session_state["typing_changed"] = False
                    st.session_state["input_was_cleared"] = False
                    st.session_state["suppress_auto_once"] = False
                    st.session_state["watch_autorun_symbol"] = target
                    st.session_state["main_view"] = "analysis"
                    st.rerun()


def _quote_panel(st, items: List[str], auto: bool) -> None:
    bucket = int(time.time() // 30) if auto else 0
    rows = _cached_watch_quotes(st, items, bucket)
    _render_cards(st, rows)


def render_watch_center(st) -> None:
    try:
        st.session_state["memory_init_report"] = ensure_memory_initialized_bootsafe(default_symbols=DEFAULT_WATCH, migrate=True, path=WATCH_LEDGER_PATH)
    except Exception:
        pass
    _watch_css(st)
    st.markdown("<div class='watch-shell'><h2>📊 TINO Watch Center</h2><div class='watch-help'>戰情中心：30秒輕量更新即時價 / 漲跌 / VWAP / TINO預估；完整 AI 分析仍回主頁手動啟動。每張卡右上角 × 可直接移除。</div></div>", unsafe_allow_html=True)

    # Fragment reruns may execute before this page-level state is materialized.
    # Always initialize with dict-style access; attribute access raises AttributeError
    # on Streamlit Cloud when the key is missing inside a fragment context.
    raw_items = st.session_state.get("watch_center_items", None)
    if raw_items is None:
        st.session_state["watch_center_items"] = _load_watchlist()
    else:
        normalized_items = []
        for x in raw_items:
            sx = _normalize_symbol(x)
            if sx and sx not in normalized_items:
                normalized_items.append(sx)
        st.session_state["watch_center_items"] = normalized_items[:60]

    _apply_card_remove_request(st)

    st.markdown("<div class='watch-controls'>", unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns([0.36, 0.20, 0.20, 0.24], gap="small")
    with c1:
        new_symbol = st.text_input("新增觀察", placeholder="例如 2337 / 6586 / 2454 / AAPL / MRVL", key="watch_new_symbol")
    with c2:
        if st.button("＋ 加入", use_container_width=True, key="watch_add"):
            s = _normalize_symbol(new_symbol)
            if s:
                try:
                    add_watch_symbol(s, path=WATCH_LEDGER_PATH, default_symbols=DEFAULT_WATCH, unhide=True)
                    st.session_state["watch_center_items"] = _load_watchlist()
                except Exception:
                    if s not in st.session_state["watch_center_items"]:
                        st.session_state["watch_center_items"].insert(0, s)
                        st.session_state["watch_center_items"] = st.session_state["watch_center_items"][:60]
                        _save_watchlist(st.session_state["watch_center_items"])
                st.session_state["main_view"] = "watch"
                st.rerun()
    with c3:
        if st.button("同步最近查詢", use_container_width=True, key="watch_sync_recent"):
            # Explicit import only. This is allowed to unhide imported symbols.
            try:
                import_symbols_explicitly(_recent_symbols(20), path=WATCH_LEDGER_PATH, default_symbols=DEFAULT_WATCH, clear_hidden_for_imported=True)
                st.session_state["watch_center_items"] = _load_watchlist()
            except Exception:
                for s in _recent_symbols(20):
                    if s not in st.session_state["watch_center_items"]:
                        st.session_state["watch_center_items"].append(s)
                st.session_state["watch_center_items"] = st.session_state["watch_center_items"][:60]
                _save_watchlist(st.session_state["watch_center_items"])
            st.session_state["main_view"] = "watch"
            st.rerun()
    with c4:
        auto = st.toggle("30秒刷新", value=False, key="watch_auto_refresh")

    items = list(st.session_state["watch_center_items"])
    if items:
        st.caption("移除：點每張股票卡右上角 ×；清單寫入 Memory Ledger，並記錄 hidden_symbols，避免自動長回。")
        if st.button("清空全部觀察", use_container_width=True, key="watch_clear_all"):
            for s in list(st.session_state.get("watch_center_items", [])):
                try:
                    remove_watch_symbol(s, path=WATCH_LEDGER_PATH, default_symbols=DEFAULT_WATCH, remember_hidden=True)
                except Exception:
                    pass
            st.session_state["watch_center_items"] = _load_watchlist()
            st.session_state["main_view"] = "watch"
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    # RC24.1 Stable Observation: automatic network fragments are disabled by
    # default because a 10-60 symbol list can overlap quote cycles and exhaust the
    # Streamlit worker.  Re-enable explicitly only after observation by setting
    # TINO_WATCH_FRAGMENT=1.  Manual/page rerun rendering remains available.
    fragment_enabled = os.environ.get("TINO_WATCH_FRAGMENT", "0") == "1"
    if auto and fragment_enabled and hasattr(st, "fragment"):
        @st.fragment(run_every="30s")
        def _live_fragment():
            _quote_panel(st, list(st.session_state.get("watch_center_items", _load_watchlist())), True)
        _live_fragment()
    else:
        if auto and not fragment_enabled:
            st.caption("RC24.1 穩定觀察：Watch Center 自動背景刷新暫停；切換頁面或手動重整時更新。")
        _quote_panel(st, list(st.session_state.get("watch_center_items", _load_watchlist())), False)

    st.caption(f"{_ledger_status_label()}｜Watch Center 不跑新聞、法人、營收與完整 AI 仲裁，所以速度比完整 Analyze 快；點卡片下方『分析』再回主頁跑完整 TINO。")
