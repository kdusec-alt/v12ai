"""Read the latest completed V156 snapshot from private Google Drive for Streamlit."""
from __future__ import annotations

import io
import json
import html
import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any

import pandas as pd
import requests
import streamlit as st

_DRIVE_API = "https://www.googleapis.com/drive/v3"
_READ_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
_MANIFEST_NAME = "TINO_V156_LATEST.json"
_COLAB_FOLDER_NAME = "manual"
_COLAB_RUN_MANIFEST_NAME = "run_manifest.json"


def _drive_session(service_account_json: str):
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    info = json.loads(service_account_json)
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=[_READ_SCOPE]
    )
    return AuthorizedSession(credentials)


def _drive_request(session: Any, url: str, *, params: dict | None = None) -> requests.Response:
    response = session.get(url, params=params, timeout=(10, 30))
    response.raise_for_status()
    return response


def _escape_q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _choose_result_csv(files: list[dict]) -> dict | None:
    """Select the full scanner table, never diagnostics CSV."""
    candidates = [
        item for item in files
        if str(item.get("name", "")).startswith("tino_market_scanner_v156_")
        and str(item.get("name", "")).lower().endswith(".csv")
        and "_diagnostics_" not in str(item.get("name", "")).lower()
        and "_tplus1_validation_" not in str(item.get("name", "")).lower()
    ]
    return candidates[0] if candidates else None


def _choose_validation_csv(files: list[dict]) -> dict | None:
    return next((item for item in files if str(item.get("name", "")).startswith(
        "tino_market_scanner_v156_tplus1_validation_")), None)


def _list_drive_children(session: Any, parent_id: str) -> list[dict]:
    """List all files in a Drive folder, including shared-drive items."""
    items: list[dict] = []
    page_token = None
    while True:
        params = {
            "q": f"'{_escape_q(parent_id)}' in parents and trashed = false",
            "pageSize": 1000,
            "orderBy": "modifiedTime desc",
            "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,size)",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if page_token:
            params["pageToken"] = page_token
        data = _drive_request(session, f"{_DRIVE_API}/files", params=params).json()
        items.extend(data.get("files") or [])
        page_token = data.get("nextPageToken")
        if not page_token:
            return items


def _load_colab_manual_snapshot(session: Any, folder_id: str):
    """Read the commit manifest written last by the Colab manual scanner."""
    root_items = _list_drive_children(session, folder_id)
    manual = next(
        (
            item for item in root_items
            if item.get("name") == _COLAB_FOLDER_NAME
            and item.get("mimeType") == "application/vnd.google-apps.folder"
        ),
        None,
    )
    if not manual:
        raise FileNotFoundError(
            f"Drive has neither {_MANIFEST_NAME} nor Colab folder {_COLAB_FOLDER_NAME}"
        )

    files = _list_drive_children(session, manual["id"])
    run_manifest_file = next(
        (item for item in files if item.get("name") == _COLAB_RUN_MANIFEST_NAME),
        None,
    )
    if not run_manifest_file:
        raise FileNotFoundError("Colab folder has no completed run_manifest.json")
    run_manifest = _drive_request(
        session,
        f"{_DRIVE_API}/files/{run_manifest_file['id']}",
        params={"alt": "media"},
    ).json()
    if run_manifest.get("status") != "success":
        raise ValueError("Colab run_manifest is not a successful scan")

    artifact_names = set(run_manifest.get("artifacts") or [])
    result_file = _choose_result_csv(
        [item for item in files if item.get("name") in artifact_names]
    )
    if not result_file or not result_file.get("id"):
        raise FileNotFoundError("Colab run_manifest does not reference an available result CSV")
    response = _drive_request(
        session, f"{_DRIVE_API}/files/{result_file['id']}", params={"alt": "media"}
    )
    frame = pd.read_csv(io.BytesIO(response.content), encoding="utf-8-sig")
    validation = pd.DataFrame()
    validation_file = _choose_validation_csv([item for item in files if item.get("name") in artifact_names])
    if validation_file and validation_file.get("id"):
        try:
            validation_response = _drive_request(session, f"{_DRIVE_API}/files/{validation_file['id']}", params={"alt": "media"})
            validation = pd.read_csv(io.BytesIO(validation_response.content), encoding="utf-8-sig")
        except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError):
            # An empty T+1 validation CSV is expected when there were no prior
            # recommendations. It must not prevent the valid result CSV loading.
            validation = pd.DataFrame()
        except Exception:
            validation = pd.DataFrame()

    # Normalize Colab's run manifest to the fields rendered by the V1116 page.
    run_time = str(run_manifest.get("run_time_taipei", ""))
    manifest = {
        **run_manifest,
        "published_at": run_time,
        "snapshot_id": manual["id"],
        "snapshot_name": f"Colab manual｜{run_time or '時間未知'}",
        "files": [result_file] + ([validation_file] if validation_file else []),
        "source": "Colab Drive manual snapshot",
    }
    return frame, manifest, result_file.get("name", ""), validation


@st.cache_data(ttl=300, show_spinner=False)
def _load_snapshot(folder_id: str, service_account_json: str, refresh_nonce: int = 0):
    """Download only the small latest pointer and its result CSV; never scan stocks."""
    del refresh_nonce  # participates in Streamlit's cache key for manual refresh
    session = _drive_session(service_account_json)
    q = (
        f"name = '{_escape_q(_MANIFEST_NAME)}' and "
        f"'{_escape_q(folder_id)}' in parents and trashed = false"
    )
    listing = _drive_request(
        session,
        f"{_DRIVE_API}/files",
        params={
            "q": q,
            "pageSize": 10,
            "orderBy": "modifiedTime desc",
            "fields": "files(id,name,mimeType,modifiedTime)",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        },
    ).json()
    manifests = listing.get("files") or []
    if not manifests:
        return _load_colab_manual_snapshot(session, folder_id)

    manifest_file = manifests[0]
    manifest = _drive_request(
        session, f"{_DRIVE_API}/files/{manifest_file['id']}", params={"alt": "media"}
    ).json()
    if manifest.get("status") != "success" or not manifest.get("snapshot_id"):
        raise ValueError("Latest manifest is not a successful scanner snapshot")

    result_file = _choose_result_csv(manifest.get("files") or [])
    if not result_file or not result_file.get("id"):
        raise FileNotFoundError("Latest manifest does not reference the scanner result CSV")
    response = _drive_request(
        session, f"{_DRIVE_API}/files/{result_file['id']}", params={"alt": "media"}
    )
    frame = pd.read_csv(io.BytesIO(response.content), encoding="utf-8-sig")
    validation = pd.DataFrame()
    validation_file = _choose_validation_csv(manifest.get("files") or [])
    if validation_file and validation_file.get("id"):
        try:
            validation_response = _drive_request(session, f"{_DRIVE_API}/files/{validation_file['id']}", params={"alt": "media"})
            validation = pd.read_csv(io.BytesIO(validation_response.content), encoding="utf-8-sig")
        except Exception:
            validation = pd.DataFrame()
    return frame, manifest, result_file.get("name", ""), validation


def _get_secret(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, default)
        if value not in (None, ""):
            return str(value).strip()
    except Exception:
        pass
    # Also support deployment platforms that expose secrets as environment variables.
    value = os.environ.get(name, default)
    return str(value).strip() if value not in (None, "") else default


def _row_value(row: pd.Series, key: str, default: str = "—") -> str:
    """Return safe display text for one snapshot cell."""
    value = row.get(key)
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    value = str(value).strip()
    return value if value and value.lower() not in {"nan", "none", "null"} else default


def _row_number(row: pd.Series, key: str, digits: int = 1, suffix: str = "") -> str:
    """Format a numeric cell without showing NaN or inventing a value."""
    try:
        value = float(row.get(key))
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(value):
        return "—"
    return f"{value:,.{digits}f}{suffix}"


def _scanner_card(row: pd.Series, rank: int, *, recommended: bool = False, quote: dict | None = None) -> str:
    """Build an escaped, responsive candidate card from a V156 result row."""
    statuses = {
        "ACTIONABLE": ("可執行", "actionable"),
        "WAIT_PULLBACK": ("等待回檔", "wait"),
        "WAIT_RESET": ("等待條件重置", "wait"),
        "REJECTED_BACKTEST": ("回測未通過", "rejected"),
    }
    raw_status = _row_value(row, "execution_status", "狀態未知")
    status_label, status_class = statuses.get(raw_status.upper(), (raw_status, "neutral"))
    title = html.escape(_row_value(row, "name", _row_value(row, "ticker")))
    ticker = html.escape(_row_value(row, "ticker"))
    tag = html.escape(_row_value(row, "tag", "Scanner 候選"))
    recommended_badge = '<span class="v156-rec">✓ 合格推薦</span>' if recommended else ""

    edge_items = [
        ("型態 Edge", _row_number(row, "setup_edge_pct", 2, "%")),
        ("市場 Edge", _row_number(row, "market_edge_pct", 2, "%")),
        ("距 Entry", _row_number(row, "entry_gap_pct", 2, "%")),
    ]
    edge_html = "".join(
        f'<div class="v156-mini"><span>{label}</span><b>{value}</b></div>'
        for label, value in edge_items
    )
    discovery_items = [
        ("Stock", _row_number(row, "stock_edge_score", 0)),
        ("Sector", _row_number(row, "sector_edge_score", 0)),
        ("Leader", _row_number(row, "leader_edge_score", 0)),
        ("Flow", _row_number(row, "institutional_flow_edge_score", 0)),
        ("Entry", _row_number(row, "entry_edge_score", 0)),
    ]
    discovery_html = "".join(
        f'<div class="v156-mini"><span>{label}</span><b>{value}</b></div>'
        for label, value in discovery_items
    )
    discovery_score = html.escape(_row_number(row, "v157_total_score", 1))
    sector_line = html.escape(
        f"族群 {_row_value(row, 'sector')}｜龍頭 {_row_value(row, 'leader_label')} #{_row_value(row, 'leader_rank')}｜"
        f"RS族群 {_row_number(row, 'rs_sector_5d_pct', 2, '%')}｜法人 {_row_value(row, 'inst_flow_status')}"
    )
    prob_items = [
        ("A", _row_number(row, "pA", 1, "%")),
        ("B", _row_number(row, "pB", 1, "%")),
        ("C", _row_number(row, "pC", 1, "%")),
    ]
    prob_html = "".join(
        f'<span class="v156-prob"><i>{label}</i><b>{value}</b></span>'
        for label, value in prob_items
    )
    levels = [
        ("Entry", _row_number(row, "entry", 2)),
        ("T1", _row_number(row, "t1", 2)),
        ("T2", _row_number(row, "t2", 2)),
        ("Stop", _row_number(row, "stop_loss", 2)),
    ]
    levels_html = "".join(
        f'<div class="v156-level"><span>{label}</span><b>{value}</b></div>'
        for label, value in levels
    )
    score = html.escape(_row_number(row, "recommendation_score", 1))
    price = html.escape(_row_number(row, "last_p", 2))
    quote = quote or {}
    live_price = quote.get("last")
    live_time = html.escape(str(quote.get("raw_time") or ""))
    live_label_raw = str(quote.get("label") or "尚未更新行情")
    try:
        quote_time = datetime.fromisoformat(str(quote.get("fetched_at")))
        if (datetime.now(timezone.utc) - quote_time.astimezone(timezone.utc)).total_seconds() > 900:
            live_label_raw += "｜報價超過 15 分鐘"
    except Exception:
        if live_price is not None:
            live_label_raw += "｜更新時間未知"
    live_label = html.escape(live_label_raw)
    live_text = f"最新 {float(live_price):,.2f}｜{live_label} {live_time}" if isinstance(live_price, (int, float)) else live_label
    live_text = html.escape(live_text)
    delta = ""
    snapshot_change = ""
    if isinstance(live_price, (int, float)) and math.isfinite(float(live_price)):
        try:
            snapshot_price = float(row.get("last_p"))
            if math.isfinite(snapshot_price) and snapshot_price > 0:
                change_pct = (float(live_price) / snapshot_price - 1) * 100
                snapshot_change = f"｜相對掃描價 {change_pct:+.2f}%"
        except (TypeError, ValueError):
            pass
    if isinstance(live_price, (int, float)) and float(row.get("entry") or 0) > 0:
        gap = (float(live_price) / float(row.get("entry")) - 1) * 100
        delta = f"｜距 Entry {gap:+.2f}%"
    trades = html.escape(_row_number(row, "trade_n", 0))
    win_rate = html.escape(_row_number(row, "win_rate", 1, "%"))
    data_date = html.escape(_row_value(row, "data_date"))
    return f"""<article class="v156-card">
      <div class="v156-card-head">
        <div><div class="v156-eyebrow">#{rank:02d}　{ticker}　·　{data_date}</div>
          <div class="v156-title">{title}</div>
          <div class="v156-subtitle">{tag}　·　掃描快照價 {price}</div>
          <div class="v156-subtitle">{sector_line}</div>
          <div class="v156-subtitle">{live_text}{html.escape(snapshot_change)}{html.escape(delta)}</div></div>
        <div class="v156-score"><b>{score}</b><span>評分</span></div>
      </div>
      <div class="v156-badges"><span class="v156-status {status_class}">{html.escape(status_label)}</span>{recommended_badge}</div>
      <div class="v156-section-label">訊號 Edge</div><div class="v156-mini-grid">{edge_html}</div>
      <div class="v156-section-label">V157 可解釋總分 {discovery_score}（未校準，不改推薦排序）</div><div class="v156-mini-grid">{discovery_html}</div>
      <div class="v156-data-row"><span>預測機率</span><div class="v156-probs">{prob_html}</div></div>
      <div class="v156-data-row v156-backtest"><span>歷史交易 {trades} 筆</span><b>勝率 {win_rate}</b></div>
      <div class="v156-section-label">價格規劃</div><div class="v156-level-grid">{levels_html}</div>
    </article>"""


def _render_card_section(st_module, title: str, frame: pd.DataFrame, *, recommended: bool = False, quotes: dict | None = None) -> None:
    """Render two-column cards while keeping the full raw table out of the way."""
    st_module.markdown(title)
    if frame.empty:
        st_module.info("本次沒有通過推薦條件的股票，不以低品質候選補足名額。")
        return
    st_module.markdown(
        """<style>
        .v156-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:16px;margin:8px 0 20px}
        .v156-card{background:linear-gradient(145deg,rgba(19,28,48,.98),rgba(14,20,35,.98));border:1px solid rgba(137,164,207,.22);border-radius:18px;padding:18px 19px;color:#edf3ff;box-shadow:0 9px 24px rgba(2,8,23,.16)}
        .v156-card-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}
        .v156-eyebrow{font-size:.72rem;letter-spacing:.06em;color:#91a6c8;font-weight:700}
        .v156-title{font-size:1.2rem;font-weight:800;margin-top:5px;line-height:1.3}
        .v156-subtitle{font-size:.82rem;color:#aebbd0;margin-top:4px}
        .v156-score{min-width:62px;text-align:center;border-radius:14px;padding:8px 10px;background:rgba(88,134,255,.14);border:1px solid rgba(111,153,255,.28)}
        .v156-score b{display:block;font-size:1.2rem;color:#c6d8ff}.v156-score span{font-size:.68rem;color:#9bb0d4}
        .v156-badges{display:flex;gap:7px;margin:12px 0 14px;flex-wrap:wrap}
        .v156-status,.v156-rec{display:inline-block;border-radius:999px;padding:4px 10px;font-size:.75rem;font-weight:750}
        .v156-status.actionable{background:rgba(31,190,130,.16);color:#78e3b7}.v156-status.wait{background:rgba(244,180,55,.15);color:#ffd276}.v156-status.rejected{background:rgba(244,92,105,.15);color:#ff9aa4}.v156-status.neutral{background:rgba(153,170,195,.15);color:#c1cede}
        .v156-rec{background:rgba(91,136,255,.18);color:#a9c2ff}
        .v156-section-label{font-size:.72rem;color:#91a6c8;font-weight:750;letter-spacing:.05em;margin:11px 0 7px}
        .v156-mini-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px}
        .v156-mini,.v156-level{background:rgba(255,255,255,.045);border:1px solid rgba(255,255,255,.055);border-radius:10px;padding:8px 9px;min-width:0}
        .v156-mini span,.v156-level span{display:block;color:#91a0b8;font-size:.68rem;margin-bottom:3px}.v156-mini b,.v156-level b{font-size:.84rem;overflow-wrap:anywhere}
        .v156-data-row{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:9px 1px;border-bottom:1px solid rgba(255,255,255,.07);font-size:.77rem;color:#aebbd0}
        .v156-probs{display:flex;gap:8px}.v156-prob{display:flex;gap:4px;align-items:center;color:#eef3fc}.v156-prob i{font-style:normal;color:#91a6c8;font-size:.7rem}.v156-prob b{font-size:.76rem}
        .v156-backtest b{color:#e5edfa}.v156-level-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:7px}
        @media(max-width:480px){.v156-card{padding:15px}.v156-mini-grid{gap:5px}.v156-level-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.v156-probs{gap:5px}}
        </style>""",
        unsafe_allow_html=True,
    )
    cards = [
        _scanner_card(row, index + 1, recommended=recommended, quote=(quotes or {}).get(str(row.get("ticker", ""))))
        for index, (_, row) in enumerate(frame.iterrows())
    ]
    st_module.markdown('<div class="v156-cards">' + "".join(cards) + "</div>", unsafe_allow_html=True)


def render_market_scanner(st_module=st) -> None:
    """Render an isolated, read-only V156 page. Errors stay inside this page."""
    st_module.markdown("## 🌌 AI Market Scanner")
    st_module.caption("讀取每日已完成的 V156 快照；此頁不啟動掃描，也不呼叫個股分析模型。")

    folder_id = _get_secret("GOOGLE_DRIVE_FOLDER_ID")
    service_account_json = _get_secret("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not folder_id or not service_account_json:
        st_module.info(
            "尚未設定 Drive 讀取權限。請在網站部署平台的 Streamlit Secrets 設定 "
            "GOOGLE_SERVICE_ACCOUNT_JSON 與 GOOGLE_DRIVE_FOLDER_ID。"
        )
        return

    refresh_nonce = int(st_module.session_state.get("v156_drive_refresh_nonce", 0))
    if st_module.button("🔄 重新讀取最新快照", key="v156_drive_refresh"):
        refresh_nonce += 1
        st_module.session_state["v156_drive_refresh_nonce"] = refresh_nonce

    try:
        with st_module.spinner("讀取 Google Drive 最新快照…"):
            frame, manifest, result_name, validation = _load_snapshot(
                folder_id, service_account_json, refresh_nonce
            )
    except Exception as exc:
        st_module.warning(
            "目前無法讀取最新 Scanner 快照；個股分析功能不受影響。"
        )
        if bool(st_module.session_state.get("admin_authenticated", False)):
            st_module.caption(f"Scanner 讀取狀態：{type(exc).__name__}: {exc}")
        return

    published = str(manifest.get("published_at", ""))
    stale = False
    age_hours = None
    try:
        published_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        age_hours = max(0.0, (datetime.now(timezone.utc) - published_at).total_seconds() / 3600)
        stale = age_hours > 72
    except Exception:
        stale = True

    snapshot_label = str(manifest.get("snapshot_name") or result_name or "V156")
    quote_snapshot_key = f"{manifest.get('snapshot_id', '')}|{manifest.get('published_at', '')}"
    if st_module.session_state.get("v156_quote_snapshot_key") != quote_snapshot_key:
        st_module.session_state["v156_live_quotes"] = {}
        st_module.session_state["v156_quote_snapshot_key"] = quote_snapshot_key
    st_module.caption(
        f"快照：{snapshot_label}｜發布時間：{published or '未知'}｜"
        f"來源檔：{result_name or '未知'}｜Drive 快取 5 分鐘"
    )
    if stale:
        st_module.warning("快照超過 72 小時或時間格式無法確認，請視為舊資料；保留顯示供查閱。")

    if frame.empty:
        st_module.info("本次掃描沒有候選資料。")
        return

    if "is_recommended5" in frame.columns:
        recommended = frame[frame["is_recommended5"].astype(str).str.lower().isin({"true", "1"})].copy()
    else:
        recommended = frame.iloc[0:0].copy()
    if "recommendation_score" in recommended.columns:
        recommended = recommended.sort_values("recommendation_score", ascending=False)
    recommended = recommended.head(5)

    top10 = frame.copy()
    if "recommendation_score" in top10.columns:
        top10 = top10.sort_values("recommendation_score", ascending=False)
    top10 = top10.head(10)

    quote_nonce = int(st_module.session_state.get("v156_quote_refresh_nonce", 0))
    if st_module.button("💹 一鍵更新 Top10 候選行情", key="v156_quote_refresh"):
        quote_nonce += 1
        st_module.session_state["v156_quote_refresh_nonce"] = quote_nonce
        symbols = list(dict.fromkeys(top10.get("ticker", pd.Series(dtype=str)).astype(str).tolist()))[:10]
        quotes: dict[str, dict] = {}
        try:
            from data_sources_tw_live_price import fetch_twse_mis_live_price
            with st_module.spinner(f"向證交所／櫃買行情端更新 {len(symbols)} 檔候選…"):
                # Bounded concurrency and bounded universe: this never runs the market scan.
                with ThreadPoolExecutor(max_workers=3) as pool:
                    futures = {pool.submit(fetch_twse_mis_live_price, symbol): symbol for symbol in symbols}
                    for future in as_completed(futures):
                        symbol = futures[future]
                        try:
                            result = future.result()
                            if result.get("accepted") and result.get("last"):
                                price_date = str(result.get("price_date") or "")
                                is_today = price_date == datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
                                quality = {
                                    "last": "成交價",
                                    "bid_ask_mid": "買賣中價參考",
                                    "ask_proxy": "賣價參考",
                                    "bid_proxy": "買價參考",
                                }.get(str(result.get("mis_last_source") or ""), "報價參考")
                                label = f"{('今日' if is_today else '最近')}官方{quality}" if is_today else f"最近官方{quality}（日期較舊）"
                                quotes[symbol] = {"last": float(result["last"]), "raw_time": result.get("raw_time"), "label": label,
                                                 "source": result.get("source"), "fetched_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat()}
                            else:
                                quotes[symbol] = {"label": "官方行情未取得"}
                        except Exception:
                            quotes[symbol] = {"label": "官方行情更新失敗"}
        except Exception:
            quotes = {symbol: {"label": "官方行情模組不可用"} for symbol in symbols}
        st_module.session_state["v156_live_quotes"] = quotes

    quotes = st_module.session_state.get("v156_live_quotes", {})

    summary_cols = st_module.columns(3)
    summary_cols[0].metric("合格推薦", f"{len(recommended)} 檔", "最多 5 檔")
    summary_cols[1].metric("觀察候選", f"{len(top10)} 檔", "依 Scanner 評分排序")
    actionable_count = 0
    if "execution_status" in top10.columns:
        actionable_count = int(top10["execution_status"].astype(str).str.upper().eq("ACTIONABLE").sum())
    summary_cols[2].metric("目前可執行", f"{actionable_count} 檔", "依執行狀態")

    st_module.caption("行情更新只查詢目前 Top10，僅更新卡牌顯示；Scanner 排名、Entry/T1/T2/Stop 與快照不會因此改寫。行情取得失敗時保留掃描快照價並標示狀態。")
    _render_card_section(st_module, "### ✅ 合格推薦", recommended, recommended=True, quotes=quotes)
    _render_card_section(st_module, "### 🏁 Top 10 觀察卡", top10, quotes=quotes)

    if not validation.empty:
        st_module.markdown("### 🔁 前次推薦隔日驗證")
        st_module.caption("依前次推薦的 Entry、T1、Stop 與下一交易日 OHLC 逐檔核對。同一根日K同時碰到 T1/Stop 時標示歧義，不推定先後順序。")
        st_module.dataframe(validation, use_container_width=True, hide_index=True)
    else:
        st_module.caption("隔日驗證將在下一次成功掃描後建立；首日或無前次推薦時會是空白。")

    with st_module.expander("查看完整 Scanner 結果"):
        st_module.dataframe(frame, use_container_width=True, hide_index=True)
