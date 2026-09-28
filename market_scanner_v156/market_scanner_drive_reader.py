"""Read the latest completed V156 snapshot from private Google Drive for Streamlit."""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests
import streamlit as st

_DRIVE_API = "https://www.googleapis.com/drive/v3"
_READ_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
_MANIFEST_NAME = "TINO_V156_LATEST.json"


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
    ]
    return candidates[0] if candidates else None


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
        raise FileNotFoundError(f"Drive folder has no {_MANIFEST_NAME}")

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
    return frame, manifest, result_file.get("name", "")


def _get_secret(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, default)
        return str(value).strip() if value not in (None, "") else default
    except Exception:
        return default


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
            frame, manifest, result_name = _load_snapshot(
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

    preferred = [
        "ticker", "name", "data_date", "last_p", "tag", "execution_status",
        "recommendation_score", "setup_edge_pct", "market_edge_pct", "entry_gap_pct",
        "pA", "pB", "pC", "trade_n", "win_rate", "entry", "t1", "t2",
        "stop_loss", "data_quality",
    ]
    result_cols = [col for col in preferred if col in frame.columns]
    if recommended.empty:
        st_module.info("本次沒有通過推薦條件的股票，不以低品質候選補足名額。")
    else:
        st_module.markdown("### ✅ 合格推薦（0–5 檔）")
        st_module.dataframe(recommended[[c for c in result_cols if c in recommended.columns]], use_container_width=True, hide_index=True)

    top10 = frame.copy()
    if "recommendation_score" in top10.columns:
        top10 = top10.sort_values("recommendation_score", ascending=False)
    st_module.markdown("### 🏁 Top 10 候選")
    st_module.dataframe(top10[[c for c in result_cols if c in top10.columns]].head(10), use_container_width=True, hide_index=True)

    with st_module.expander("查看完整 Scanner 結果"):
        st_module.dataframe(frame, use_container_width=True, hide_index=True)
