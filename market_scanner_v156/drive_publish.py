"""Publish one completed scanner run to Google Drive; update latest pointer last."""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload
import io

SCOPES = ["https://www.googleapis.com/auth/drive"]
MANIFEST_NAME = "TINO_V156_LATEST.json"


def _escape_q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _find_file(service: Any, name: str, parent_id: str) -> dict[str, Any] | None:
    query = f"name = '{_escape_q(name)}' and '{_escape_q(parent_id)}' in parents and trashed = false"
    response = service.files().list(q=query, fields="files(id,name,mimeType,modifiedTime)", pageSize=10).execute()
    files = response.get("files", [])
    return files[0] if files else None


def _create_folder(service: Any, name: str, parent_id: str) -> str:
    item = service.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
        fields="id",
    ).execute()
    return item["id"]


def publish_run(run_dir: Path) -> dict[str, Any]:
    parent_id = os.environ["GOOGLE_DRIVE_FOLDER_ID"].strip()
    sa_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    credentials = service_account.Credentials.from_service_account_info(sa_info, scopes=SCOPES)
    service = build("drive", "v3", credentials=credentials, cache_discovery=False)

    local_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    if local_manifest.get("status") != "success":
        raise RuntimeError("Refusing to publish: local run_manifest is not successful")
    csv_files = list(run_dir.glob("*.csv"))
    if not csv_files:
        raise RuntimeError("Refusing to publish: no CSV result exists")

    taipei_stamp = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m%d_%H%M%S")
    snapshot_name = f"TINO_V156_{taipei_stamp}"
    snapshot_id = _create_folder(service, snapshot_name, parent_id)
    uploaded = []
    for path in sorted(run_dir.iterdir()):
        if not path.is_file():
            continue
        media = MediaFileUpload(str(path), resumable=True)
        item = service.files().create(
            body={"name": path.name, "parents": [snapshot_id]}, media_body=media,
            fields="id,name,mimeType,size,webViewLink",
        ).execute()
        uploaded.append(item)
    if not uploaded:
        raise RuntimeError("No files uploaded; latest pointer was not changed")

    previous = _find_file(service, MANIFEST_NAME, parent_id)
    previous_summary = None
    if previous:
        try:
            old_bytes = service.files().get_media(fileId=previous["id"]).execute()
            old = json.loads(old_bytes.decode("utf-8"))
            previous_summary = {"snapshot_id": old.get("snapshot_id"), "published_at": old.get("published_at")}
        except Exception:
            # Do not block the current successful snapshot if the old pointer is unreadable.
            previous_summary = {"manifest_file_id": previous["id"], "status": "unreadable"}

    pointer = {
        "status": "success",
        "schema_version": 1,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "market_timezone": "Asia/Taipei",
        "snapshot_id": snapshot_id,
        "snapshot_name": snapshot_name,
        "scan": local_manifest,
        "files": uploaded,
        "previous_last_known_good": previous_summary,
    }
    payload = json.dumps(pointer, ensure_ascii=False, indent=2).encode("utf-8")
    media = MediaIoBaseUpload(io.BytesIO(payload), mimetype="application/json", resumable=False)
    if previous:
        service.files().update(fileId=previous["id"], media_body=media).execute()
        pointer["manifest_file_id"] = previous["id"]
    else:
        created = service.files().create(
            body={"name": MANIFEST_NAME, "mimeType": "application/json", "parents": [parent_id]},
            media_body=media, fields="id,name",
        ).execute()
        pointer["manifest_file_id"] = created["id"]
    return pointer


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    result = publish_run(args.run_dir)
    print(json.dumps({"status": result["status"], "snapshot_id": result["snapshot_id"], "manifest_file_id": result["manifest_file_id"]}, ensure_ascii=False))
