# -*- coding: utf-8 -*-
"""
TINO V12 RC2.4 Memory Persistence Guard
Single source of truth for Watch Center + lightweight persistent memory.

RC2.4.1 additions:
- Auto-init .tino_memory files so Learning Center does not show MISSING on first boot.
- Legacy memory migration from older root/runtime files into .tino_memory.
- Read-back verification for the ledger after every write.

Important limitation:
Local JSON/JSONL keeps memory across reruns and ordinary file updates inside the same
app workspace. For true cross-redeploy permanence on Streamlit Cloud, keep the
.tino_memory folder in your persistent repo/storage, or connect GitHub/DB sync later.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from urllib.parse import quote
from functools import lru_cache
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

TW_TZ = timezone(timedelta(hours=8))
LEDGER_VERSION = "V12_RC24_MEMORY_RECOVERY_GUARD"

try:
    from memory_store import MEMORY_DIR as _TINO_MEMORY_DIR
    from memory_store import PREDICTION_LOG, AUDIT_LOG, TICKER_PROFILE
except Exception:
    _TINO_MEMORY_DIR = Path(os.environ.get("TINO_MEMORY_DIR", ".tino_memory"))
    PREDICTION_LOG = _TINO_MEMORY_DIR / "prediction_log.jsonl"
    AUDIT_LOG = _TINO_MEMORY_DIR / "audit_log.jsonl"
    TICKER_PROFILE = _TINO_MEMORY_DIR / "ticker_profiles.json"

DEFAULT_LEDGER_PATH = Path(os.environ.get("TINO_LEDGER_PATH", str(_TINO_MEMORY_DIR / "tino_memory_ledger.json")))

LEGACY_FILE_NAMES = {
    "prediction_log": "prediction_log.jsonl",
    "audit_log": "audit_log.jsonl",
    "ticker_profiles": "ticker_profiles.json",
    "watch_center_list": "watch_center_list.json",
    "ledger": "tino_memory_ledger.json",
}


# RC2.4.2 Long-term Memory Guard
# ------------------------------------------------------------
# Local .tino_memory is safe across reruns, but not guaranteed across Streamlit
# Cloud redeploy/container recycle.  Therefore this module supports an optional
# GitHub-backed remote mirror.  Configure via environment variables or
# st.secrets:
#   TINO_GITHUB_TOKEN       = GitHub fine-grained token with Contents write access
#   TINO_GITHUB_REPO        = owner/repo
#   TINO_GITHUB_BRANCH      = main (optional)
#   TINO_GITHUB_MEMORY_DIR  = .tino_memory (optional)
# Without these settings the system remains local-only, but still has .bak
# backups and shrink guard inside the current runtime.
_MEMORY_FILES = [
    "prediction_log.jsonl",
    "audit_log.jsonl",
    "ticker_profiles.json",
    "tino_memory_ledger.json",
]
_LAST_REMOTE_REPORT: Dict[str, Any] = {
    "configured": False,
    "backend": "none",
    "status": "LOCAL_ONLY",
    "branch_ready": False,
    "last_restore_at_tw": None,
    "last_sync_at_tw": None,
    "last_verified_at_tw": None,
    "restored_files": [],
    "synced_files": [],
    "shrink_warnings": [],
    "remote_files": {},
    "error": None,
}
_REMOTE_BRANCH_LOCK = threading.RLock()
_REMOTE_BRANCH_READY: set[Tuple[str, str]] = set()
_REMOTE_IO_LOCK = threading.RLock()


@lru_cache(maxsize=1)
def _streamlit_secret_map() -> Dict[str, str]:
    """Read Streamlit secrets once per process.

    The previous implementation touched ``st.secrets`` on every ledger merge,
    which could emit dozens of missing-secrets errors and repeatedly parse the
    secrets backend during a single rerun.  Runtime configuration changes still
    take effect after the normal Streamlit process restart/redeploy.
    """
    try:
        import streamlit as st  # type: ignore
        return {str(k): str(v) for k, v in dict(st.secrets).items()}
    except Exception:
        return {}


@lru_cache(maxsize=32)
def _secret(name: str, default: Optional[str] = None) -> Optional[str]:
    val = os.environ.get(name)
    if val not in (None, ""):
        return val
    secrets_map = _streamlit_secret_map()
    if name in secrets_map:
        return str(secrets_map.get(name) or default)
    aliases = {
        "TINO_GITHUB_TOKEN": ("GITHUB_TOKEN", "github_token"),
        "TINO_GITHUB_REPO": ("GITHUB_REPOSITORY", "github_repo"),
        "TINO_GITHUB_BRANCH": ("GITHUB_BRANCH", "github_branch"),
        "TINO_GITHUB_MEMORY_DIR": ("GITHUB_MEMORY_DIR", "github_memory_dir"),
    }
    for alias in aliases.get(name, ()):
        if alias in secrets_map:
            return str(secrets_map.get(alias) or default)
    return default


@lru_cache(maxsize=1)
def _remote_config() -> Dict[str, Any]:
    token = str(_secret("TINO_GITHUB_TOKEN") or _secret("GITHUB_TOKEN") or "").strip()
    repo = str(_secret("TINO_GITHUB_REPO") or _secret("GITHUB_REPOSITORY") or "").strip().strip("/")
    branch = str(_secret("TINO_GITHUB_BRANCH", "tino-memory") or "tino-memory").strip()
    memory_dir = str(_secret("TINO_GITHUB_MEMORY_DIR", ".tino_memory") or ".tino_memory").strip().strip("/")
    configured = bool(token and repo and "/" in repo and branch)
    return {
        "configured": configured,
        "backend": "github" if configured else "none",
        "token": token,
        "repo": repo,
        "branch": branch,
        "memory_dir": memory_dir,
    }


def _gh_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "TINO-V12-Memory-Guard",
    }


def _gh_request(method: str, url: str, token: str, payload: Optional[Dict[str, Any]] = None) -> Tuple[int, Any]:
    """Small bounded GitHub request wrapper that never raises into Streamlit."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=_gh_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("utf-8")
            return int(resp.status), json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        try:
            parsed = json.loads(body) if body else {}
        except Exception:
            parsed = {"message": body}
        return int(exc.code), parsed
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 599, {"message": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:
        return 598, {"message": f"{type(exc).__name__}: {exc}"}


def _ensure_remote_branch() -> Tuple[bool, Optional[str]]:
    """Ensure the isolated memory branch exists without destabilising the app.

    GitHub's ``git/ref`` endpoint can return a misleading 404 for an otherwise
    visible branch under some fine-grained-token combinations.  Use the normal
    branch endpoint first, then verify through the repository contents endpoint.
    Only if both checks fail do we attempt branch creation from the default
    branch.  Successful checks are cached for the current process.
    """
    cfg = _remote_config()
    if not cfg["configured"]:
        return False, "remote_not_configured"

    repo = str(cfg["repo"]).strip().strip("/")
    branch = str(cfg["branch"]).strip()
    token = str(cfg["token"]).strip()
    key = (repo, branch)

    def _verify_existing_branch() -> Tuple[bool, int, Any]:
        branch_q = quote(branch, safe="")
        branch_url = f"https://api.github.com/repos/{repo}/branches/{branch_q}"
        status, obj = _gh_request("GET", branch_url, token)
        if status == 200:
            return True, status, obj

        # Fallback: asking for the repository root at a branch is sufficient to
        # prove the ref is valid, and is compatible with fine-grained PATs that
        # have Contents read/write permission.
        root_url = f"https://api.github.com/repos/{repo}/contents?ref={branch_q}"
        root_status, root_obj = _gh_request("GET", root_url, token)
        if root_status == 200:
            return True, root_status, root_obj
        return False, status if status != 404 else root_status, obj if status != 404 else root_obj

    with _REMOTE_BRANCH_LOCK:
        if key in _REMOTE_BRANCH_READY:
            return True, None

        exists, verify_status, verify_obj = _verify_existing_branch()
        if exists:
            _REMOTE_BRANCH_READY.add(key)
            _LAST_REMOTE_REPORT["branch_ready"] = True
            return True, None
        if verify_status not in (404,):
            return False, f"github_branch_get_{verify_status}:{verify_obj.get('message', verify_obj)}"

        repo_url = f"https://api.github.com/repos/{repo}"
        repo_status, repo_obj = _gh_request("GET", repo_url, token)
        if repo_status >= 300:
            return False, f"github_repo_get_{repo_status}:{repo_obj.get('message', repo_obj)}"
        base_branch = str(repo_obj.get("default_branch") or "main").strip()
        base_q = quote(base_branch, safe="")
        base_url = f"https://api.github.com/repos/{repo}/git/ref/heads/{base_q}"
        base_status, base_obj = _gh_request("GET", base_url, token)
        if base_status >= 300:
            return False, f"github_base_ref_{base_status}:{base_obj.get('message', base_obj)}"
        base_sha = str((((base_obj or {}).get("object") or {}).get("sha")) or "")
        if not base_sha:
            return False, "github_base_ref_missing_sha"

        create_url = f"https://api.github.com/repos/{repo}/git/refs"
        create_payload = {"ref": f"refs/heads/{branch}", "sha": base_sha}
        create_status, create_obj = _gh_request("POST", create_url, token, create_payload)
        if create_status not in (201, 422):
            return False, f"github_branch_create_{create_status}:{create_obj.get('message', create_obj)}"

        # A 422 commonly means the reference already exists. Verify using the
        # same robust path instead of treating the git/ref endpoint as truth.
        exists, final_status, final_obj = _verify_existing_branch()
        if not exists:
            return False, f"github_branch_verify_{final_status}:{final_obj.get('message', final_obj)}"
        _REMOTE_BRANCH_READY.add(key)
        _LAST_REMOTE_REPORT["branch_ready"] = True
        return True, None

def _remote_path_for(local_path: str | Path) -> str:
    cfg = _remote_config()
    return f"{cfg['memory_dir'].strip('/')}/{Path(local_path).name}"


def _record_remote_file(name: str, blob: bytes, sha: Optional[str], verified: bool) -> None:
    files = dict(_LAST_REMOTE_REPORT.get("remote_files") or {})
    files[str(name)] = {
        "bytes": len(blob or b""),
        "rows": _count_jsonl_bytes(blob) if str(name).endswith(".jsonl") else None,
        "sha": str(sha or "")[:12],
        "verified": bool(verified),
        "checked_at_tw": now_tw_iso(),
    }
    _LAST_REMOTE_REPORT["remote_files"] = files


def _github_read_file(file_name: str) -> Tuple[bool, Optional[bytes], Optional[str], Optional[str]]:
    cfg = _remote_config()
    if not cfg["configured"]:
        return False, None, None, "remote_not_configured"
    branch_ok, branch_err = _ensure_remote_branch()
    if not branch_ok:
        return False, None, None, branch_err

    remote_path = f"{cfg['memory_dir'].strip('/')}/{Path(file_name).name}"
    remote_path_q = quote(remote_path, safe="/")
    url = f"https://api.github.com/repos/{cfg['repo']}/contents/{remote_path_q}?ref={quote(str(cfg['branch']), safe='')}"
    status, obj = _gh_request("GET", url, cfg["token"])
    if status == 404:
        return False, None, None, "remote_missing"
    if status >= 300:
        return False, None, None, f"github_get_{status}:{obj.get('message', obj)}"
    try:
        sha = str(obj.get("sha") or "")
        encoded = str(obj.get("content") or "").replace("\n", "")
        if encoded:
            content = base64.b64decode(encoded.encode("ascii"))
        elif sha:
            # GitHub Contents API omits inline content for larger files.  The
            # Git Blob endpoint still returns base64 content up to GitHub's
            # normal repository object limit.
            blob_url = f"https://api.github.com/repos/{cfg['repo']}/git/blobs/{sha}"
            blob_status, blob_obj = _gh_request("GET", blob_url, cfg["token"])
            if blob_status >= 300:
                return False, None, sha, f"github_blob_{blob_status}:{blob_obj.get('message', blob_obj)}"
            blob_encoded = str(blob_obj.get("content") or "").replace("\n", "")
            content = base64.b64decode(blob_encoded.encode("ascii")) if blob_encoded else b""
        else:
            content = b""
        _record_remote_file(Path(file_name).name, content, sha, verified=False)
        return True, content, sha, None
    except Exception as exc:
        return False, None, None, f"github_decode_failed:{type(exc).__name__}:{exc}"


def _github_write_file(local_path: str | Path, message: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    cfg = _remote_config()
    if not cfg["configured"]:
        return False, "remote_not_configured"
    branch_ok, branch_err = _ensure_remote_branch()
    if not branch_ok:
        return False, branch_err

    p = Path(local_path)
    if not p.exists() or p.is_dir():
        return False, "local_missing"
    ok, _old_bytes, sha, read_err = _github_read_file(p.name)
    if read_err not in (None, "remote_missing"):
        return False, f"remote_prewrite_read_failed:{read_err}"
    remote_path = _remote_path_for(p)
    remote_path_q = quote(remote_path, safe="/")
    url = f"https://api.github.com/repos/{cfg['repo']}/contents/{remote_path_q}"
    payload: Dict[str, Any] = {
        "message": message or f"TINO memory sync: {p.name}",
        "content": base64.b64encode(p.read_bytes()).decode("ascii"),
        "branch": cfg["branch"],
    }
    if ok and sha:
        payload["sha"] = sha
    status, obj = _gh_request("PUT", url, cfg["token"], payload)
    if status not in (200, 201):
        return False, f"github_put_{status}:{obj.get('message', obj)}"
    return True, None

def _count_jsonl_bytes(blob: bytes) -> int:
    if not blob:
        return 0
    return len([x for x in blob.decode("utf-8", "ignore").splitlines() if x.strip()])


def _count_jsonl_file(path: str | Path) -> int:
    p = Path(path)
    if not p.exists() or p.is_dir():
        return 0
    try:
        return len([x for x in p.read_text(encoding="utf-8").splitlines() if x.strip()])
    except Exception:
        return 0



def _atomic_write_bytes(path: str | Path, payload: bytes) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
        os.replace(tmp_name, p)
    finally:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except Exception:
            pass


def _memory_row_identity(row: Dict[str, Any]) -> str:
    if not isinstance(row, dict):
        return ""
    for key in ("audit_id", "id", "prediction_id"):
        value = row.get(key)
        if value not in (None, ""):
            return f"{key}:{value}"
    ticker = str(row.get("ticker") or row.get("symbol") or "")
    stamp = str(
        row.get("run_time_tw")
        or row.get("audit_time_tw")
        or row.get("logged_at_tw")
        or row.get("created_at")
        or ""
    )
    target = str(row.get("target_trade_date") or row.get("target_kind") or row.get("target") or "")
    if ticker and stamp:
        return f"fallback:{ticker}:{target}:{stamp}"
    try:
        return "json:" + json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return ""


def _memory_row_stamp(row: Dict[str, Any]) -> str:
    return str(
        row.get("run_time_tw")
        or row.get("audit_time_tw")
        or row.get("logged_at_tw")
        or row.get("created_at")
        or row.get("run_date_tw")
        or row.get("audit_date_tw")
        or ""
    )


def _decode_jsonl_bytes(blob: bytes) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in (blob or b"").decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict) and obj:
            rows.append(obj)
    return rows


def _merge_jsonl_rows(remote_rows: List[Dict[str, Any]], local_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Lossless ID merge; local/newest occurrence wins for duplicate IDs."""
    combined = [r for r in list(remote_rows or []) + list(local_rows or []) if isinstance(r, dict) and r]
    last_index: Dict[str, int] = {}
    identities: List[str] = []
    for idx, row in enumerate(combined):
        ident = _memory_row_identity(row) or f"row:{idx}"
        identities.append(ident)
        last_index[ident] = idx
    merged = [row for idx, row in enumerate(combined) if last_index.get(identities[idx]) == idx]
    merged.sort(key=_memory_row_stamp)
    return merged


def _encode_jsonl_rows(rows: List[Dict[str, Any]]) -> bytes:
    if not rows:
        return b""
    text = "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows)
    return text.encode("utf-8")


def _json_dict_from_bytes(blob: bytes) -> Dict[str, Any]:
    try:
        value = json.loads((blob or b"").decode("utf-8", "replace"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _merge_recent_memory(remote_rows: Any, local_rows: Any, limit: int = 500) -> List[Dict[str, Any]]:
    rows = _merge_jsonl_rows(
        [r for r in (remote_rows or []) if isinstance(r, dict)],
        [r for r in (local_rows or []) if isinstance(r, dict)],
    )
    rows.sort(key=_memory_row_stamp, reverse=True)
    return rows[: max(1, int(limit or 1))]


def _merge_profile_docs(remote: Dict[str, Any], local: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = copy.deepcopy(remote or {})
    for key, value in (local or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            item = copy.deepcopy(merged.get(key) or {})
            item.update(copy.deepcopy(value))
            merged[key] = item
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _merge_ledger_docs(remote: Dict[str, Any], local: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = copy.deepcopy(remote or {})
    for key, value in (local or {}).items():
        if key not in {"watch_center", "recent_predictions", "recent_audits", "ticker_profiles", "storage_guard"}:
            merged[key] = copy.deepcopy(value)

    remote_watch = remote.get("watch_center", {}) if isinstance(remote.get("watch_center"), dict) else {}
    local_watch = local.get("watch_center", {}) if isinstance(local.get("watch_center"), dict) else {}
    hidden: List[str] = []
    for symbol in list(remote_watch.get("hidden_symbols") or []) + list(local_watch.get("hidden_symbols") or []):
        value = str(symbol or "").strip().upper()
        if value and value not in hidden:
            hidden.append(value)
    symbols: List[str] = []
    for symbol in list(local_watch.get("symbols") or []) + list(remote_watch.get("symbols") or []):
        value = str(symbol or "").strip().upper()
        if value and value not in hidden and value not in symbols:
            symbols.append(value)
    watch = copy.deepcopy(remote_watch)
    watch.update(copy.deepcopy(local_watch))
    watch["symbols"] = symbols
    watch["hidden_symbols"] = hidden
    watch["updated_at_tw"] = max(
        str(remote_watch.get("updated_at_tw") or ""),
        str(local_watch.get("updated_at_tw") or ""),
    ) or None
    merged["watch_center"] = watch
    merged["recent_predictions"] = _merge_recent_memory(
        remote.get("recent_predictions"), local.get("recent_predictions"), 500
    )
    merged["recent_audits"] = _merge_recent_memory(
        remote.get("recent_audits"), local.get("recent_audits"), 500
    )
    merged["ticker_profiles"] = _merge_profile_docs(
        remote.get("ticker_profiles", {}) if isinstance(remote.get("ticker_profiles"), dict) else {},
        local.get("ticker_profiles", {}) if isinstance(local.get("ticker_profiles"), dict) else {},
    )
    storage = copy.deepcopy(remote.get("storage_guard", {}) if isinstance(remote.get("storage_guard"), dict) else {})
    storage.update(copy.deepcopy(local.get("storage_guard", {}) if isinstance(local.get("storage_guard"), dict) else {}))
    merged["storage_guard"] = storage
    return merged


def _reconcile_local_with_remote(path: str | Path, remote_blob: bytes) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """Merge remote and local without allowing either side to erase history."""
    p = Path(path)
    local_blob = p.read_bytes() if p.exists() and p.is_file() else b""
    info: Dict[str, Any] = {"remote_bytes": len(remote_blob or b""), "local_bytes_before": len(local_blob)}
    try:
        if p.suffix == ".jsonl":
            remote_rows = _decode_jsonl_bytes(remote_blob)
            local_rows = _decode_jsonl_bytes(local_blob)
            merged_rows = _merge_jsonl_rows(remote_rows, local_rows)
            payload = _encode_jsonl_rows(merged_rows)
            info.update({
                "remote_rows": len(remote_rows),
                "local_rows_before": len(local_rows),
                "merged_rows": len(merged_rows),
            })
        else:
            remote_doc = _json_dict_from_bytes(remote_blob)
            local_doc = _json_dict_from_bytes(local_blob)
            if p.name == "tino_memory_ledger.json":
                merged_doc = _merge_ledger_docs(remote_doc, local_doc)
            elif p.name == "ticker_profiles.json":
                merged_doc = _merge_profile_docs(remote_doc, local_doc)
            else:
                merged_doc = copy.deepcopy(remote_doc)
                merged_doc.update(copy.deepcopy(local_doc))
            payload = (json.dumps(merged_doc, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")
            info.update({
                "remote_items": len(remote_doc),
                "local_items_before": len(local_doc),
                "merged_items": len(merged_doc),
            })
        changed = payload != local_blob
        if changed:
            _atomic_write_bytes(p, payload)
        info["changed"] = changed
        info["local_bytes_after"] = len(payload)
        return True, info, None
    except Exception as exc:
        return False, info, f"reconcile_failed:{type(exc).__name__}:{exc}"

def _write_local_backup(path: str | Path) -> None:
    p = Path(path)
    if not p.exists() or p.is_dir():
        return
    try:
        bak_dir = p.parent / "_backup"
        bak_dir.mkdir(parents=True, exist_ok=True)
        # latest backup is used by shrink guard after runtime hiccups.
        (bak_dir / f"{p.name}.bak").write_bytes(p.read_bytes())
        # daily rotating backup helps debugging without exploding file count.
        day = datetime.now(TW_TZ).strftime("%Y%m%d")
        (bak_dir / f"{p.name}.{day}.bak").write_bytes(p.read_bytes())
    except Exception:
        return


def _restore_from_local_backup_if_bigger(path: str | Path) -> Tuple[bool, str]:
    p = Path(path)
    bak = p.parent / "_backup" / f"{p.name}.bak"
    if not bak.exists() or bak.is_dir():
        return False, "no_backup"
    try:
        if p.suffix == ".jsonl":
            cur_n = _count_jsonl_file(p)
            bak_n = _count_jsonl_file(bak)
            if bak_n > cur_n:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(bak.read_bytes())
                return True, f"restored_from_backup_lines:{cur_n}->{bak_n}"
        else:
            cur_size = p.stat().st_size if p.exists() else 0
            bak_size = bak.stat().st_size
            if bak_size > cur_size and bak_size > 2:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(bak.read_bytes())
                return True, f"restored_from_backup_bytes:{cur_size}->{bak_size}"
    except Exception as exc:
        return False, f"backup_restore_failed:{type(exc).__name__}:{exc}"
    return False, "backup_not_bigger"


def _local_path_for_memory_file(file_name: str) -> Path:
    if file_name == "tino_memory_ledger.json":
        return Path(DEFAULT_LEDGER_PATH)
    if file_name == "prediction_log.jsonl":
        return Path(PREDICTION_LOG)
    if file_name == "audit_log.jsonl":
        return Path(AUDIT_LOG)
    if file_name == "ticker_profiles.json":
        return Path(TICKER_PROFILE)
    return Path(_TINO_MEMORY_DIR) / file_name


def remote_restore_memory_files(force: bool = False) -> Dict[str, Any]:
    """Restore and reconcile GitHub memory before local initialization.

    Remote and local JSONL are merged by stable row identity.  Equal row counts
    are never treated as proof that the files are identical, which prevents a
    recycled runtime from overwriting a different remote history.
    """
    cfg = _remote_config()
    report: Dict[str, Any] = {
        "configured": cfg["configured"],
        "backend": cfg["backend"],
        "status": "LOCAL_ONLY" if not cfg["configured"] else "PASS",
        "branch_ready": False,
        "restored_files": [],
        "synced_files": [],
        "missing_files": [],
        "reconciled": {},
        "shrink_warnings": [],
        "error": None,
        "last_restore_at_tw": now_tw_iso(),
    }
    if not cfg["configured"]:
        _LAST_REMOTE_REPORT.update(report)
        return report

    branch_ok, branch_err = _ensure_remote_branch()
    report["branch_ready"] = bool(branch_ok)
    if not branch_ok:
        report["status"] = "WARN"
        report["error"] = branch_err
        _LAST_REMOTE_REPORT.update(report)
        return report

    for name in _MEMORY_FILES:
        local = _local_path_for_memory_file(name)
        try:
            exists, blob, sha, err = _github_read_file(name)
            if not exists or blob is None:
                if err == "remote_missing":
                    report["missing_files"].append(name)
                elif err is not None:
                    report["status"] = "WARN"
                    report["shrink_warnings"].append(f"{name}:{err}")
                continue
            before = local.read_bytes() if local.exists() and local.is_file() else b""
            ok, info, reconcile_err = _reconcile_local_with_remote(local, blob)
            report["reconciled"][name] = info
            if not ok:
                report["status"] = "WARN"
                report["shrink_warnings"].append(f"{name}:{reconcile_err}")
                continue
            after = local.read_bytes() if local.exists() and local.is_file() else b""
            if force or after != before:
                report["restored_files"].append(name)
            _write_local_backup(local)
            if after != blob:
                # Local may contain a prediction completed just before a prior
                # outage.  Because reconciliation already includes the full
                # remote history, uploading the merged file cannot shrink it.
                sync_ok, sync_err = _sync_file_to_remote(local, shrink_guard=True)
                if sync_ok:
                    report["synced_files"].append(name)
                else:
                    report["status"] = "WARN"
                    report["shrink_warnings"].append(f"restore_sync_{name}:{sync_err}")
                    _record_remote_file(name, blob, sha, verified=False)
            else:
                _record_remote_file(name, blob, sha, verified=True)
        except Exception as exc:
            report["status"] = "WARN"
            report["error"] = f"restore_{name}:{type(exc).__name__}:{exc}"
    if report["status"] == "PASS" and len(report.get("missing_files") or []) == len(_MEMORY_FILES):
        report["status"] = "EMPTY_REMOTE"
    _LAST_REMOTE_REPORT.update(report)
    _LAST_REMOTE_REPORT["last_verified_at_tw"] = now_tw_iso()
    return report

def _sync_file_to_remote_unlocked(path: str | Path, shrink_guard: bool = True) -> Tuple[bool, Optional[str]]:
    """Reconcile, upload and read-back verify one changed memory file."""
    cfg = _remote_config()
    if not cfg["configured"]:
        return False, "remote_not_configured"
    p = Path(path)
    if not p.exists() or p.is_dir():
        return False, "local_missing"

    exists, blob, _sha, err = _github_read_file(p.name)
    if exists and blob is not None:
        ok, info, reconcile_err = _reconcile_local_with_remote(p, blob)
        if not ok:
            msg = reconcile_err or f"reconcile_failed:{p.name}"
            _LAST_REMOTE_REPORT.setdefault("shrink_warnings", []).append(msg)
            return False, msg
        if info.get("changed"):
            note = f"remote_merged_before_sync:{p.name}"
            _LAST_REMOTE_REPORT.setdefault("shrink_warnings", []).append(note)
    elif err not in (None, "remote_missing"):
        # Never overwrite a remote file when its current state cannot be read.
        msg = f"remote_read_blocked:{p.name}:{err}"
        _LAST_REMOTE_REPORT.setdefault("shrink_warnings", []).append(msg)
        _LAST_REMOTE_REPORT["status"] = "WARN"
        _LAST_REMOTE_REPORT["error"] = msg
        return False, msg

    local_blob = p.read_bytes()
    ok, write_err = _github_write_file(p, message=f"TINO memory sync: {p.name}")
    if not ok:
        _LAST_REMOTE_REPORT["status"] = "WARN"
        _LAST_REMOTE_REPORT["error"] = write_err
        return False, write_err

    verified, verify_blob, verify_sha, verify_err = _github_read_file(p.name)
    if not verified or verify_blob is None:
        msg = f"remote_verify_read_failed:{p.name}:{verify_err}"
        _LAST_REMOTE_REPORT["status"] = "WARN"
        _LAST_REMOTE_REPORT["error"] = msg
        return False, msg
    if hashlib.sha256(verify_blob).digest() != hashlib.sha256(local_blob).digest():
        msg = f"remote_verify_mismatch:{p.name}"
        _LAST_REMOTE_REPORT["status"] = "WARN"
        _LAST_REMOTE_REPORT["error"] = msg
        return False, msg

    _record_remote_file(p.name, verify_blob, verify_sha, verified=True)
    _LAST_REMOTE_REPORT["configured"] = True
    _LAST_REMOTE_REPORT["backend"] = "github"
    _LAST_REMOTE_REPORT["branch_ready"] = True
    _LAST_REMOTE_REPORT["status"] = "PASS"
    _LAST_REMOTE_REPORT["last_sync_at_tw"] = now_tw_iso()
    _LAST_REMOTE_REPORT["last_verified_at_tw"] = now_tw_iso()
    _LAST_REMOTE_REPORT["error"] = None
    synced = list(_LAST_REMOTE_REPORT.get("synced_files", []))
    if p.name not in synced:
        synced.append(p.name)
    _LAST_REMOTE_REPORT["synced_files"] = synced[-20:]
    return True, None

def _sync_file_to_remote(path: str | Path, shrink_guard: bool = True) -> Tuple[bool, Optional[str]]:
    """Serialize one reconcile/write/verify transaction across app sessions."""
    with _REMOTE_IO_LOCK:
        return _sync_file_to_remote_unlocked(path, shrink_guard=shrink_guard)

def sync_all_memory_files_to_remote() -> Dict[str, Any]:
    cfg = _remote_config()
    report: Dict[str, Any] = {
        "configured": cfg["configured"],
        "backend": cfg["backend"],
        "status": "LOCAL_ONLY" if not cfg["configured"] else "PASS",
        "branch_ready": False,
        "synced_files": [],
        "shrink_warnings": [],
        "error": None,
        "last_sync_at_tw": now_tw_iso(),
    }
    if not cfg["configured"]:
        _LAST_REMOTE_REPORT.update(report)
        return report
    branch_ok, branch_err = _ensure_remote_branch()
    report["branch_ready"] = bool(branch_ok)
    if not branch_ok:
        report["status"] = "WARN"
        report["error"] = branch_err
        _LAST_REMOTE_REPORT.update(report)
        return report
    for name in _MEMORY_FILES:
        p = _local_path_for_memory_file(name)
        if not p.exists():
            continue
        ok, err = _sync_file_to_remote(p, shrink_guard=True)
        if ok:
            report["synced_files"].append(name)
        elif err != "remote_not_configured":
            report["status"] = "WARN"
            report["error"] = err
            report["shrink_warnings"].append(str(err))
    report["remote_files"] = dict(_LAST_REMOTE_REPORT.get("remote_files") or {})
    report["last_verified_at_tw"] = _LAST_REMOTE_REPORT.get("last_verified_at_tw")
    _LAST_REMOTE_REPORT.update(report)
    return report


def remote_status() -> Dict[str, Any]:
    cfg = _remote_config()
    out = dict(_LAST_REMOTE_REPORT)
    out.update({
        "configured": cfg["configured"],
        "backend": cfg["backend"],
        "repo": cfg.get("repo") if cfg["configured"] else None,
        "branch": cfg.get("branch") if cfg["configured"] else None,
        "memory_dir": cfg.get("memory_dir"),
    })
    return out


def now_tw_iso() -> str:
    return datetime.now(TW_TZ).isoformat(timespec="seconds")


def normalize_symbol(symbol: str) -> str:
    s = str(symbol or "").strip().upper().replace(" ", "")
    if not s:
        return ""
    try:
        from ticker_resolver import resolve_ticker
        return resolve_ticker(s).resolved_symbol.upper()
    except Exception:
        return s


def _unique_symbols(symbols: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in symbols or []:
        s = normalize_symbol(raw)
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def empty_ledger() -> Dict[str, Any]:
    ts = now_tw_iso()
    return {
        "version": LEDGER_VERSION,
        "created_at_tw": ts,
        "last_saved_tw": ts,
        "watch_center": {
            "symbols": [],
            "hidden_symbols": [],
            "updated_at_tw": ts,
            "source_policy": "ledger_only_no_auto_backfill",
        },
        "ticker_profiles": {},
        "recent_predictions": [],
        "recent_audits": [],
        "storage_guard": {
            "storage_mode": "github_remote_if_configured_else_local_runtime",
            "last_write_ok": False,
            "last_verify_ok": False,
            "last_error": "not_saved_yet",
            "last_write_at_tw": None,
            "last_verify_at_tw": None,
            "migration": {
                "last_migration_at_tw": None,
                "status": "not_run",
                "imported_predictions": 0,
                "imported_audits": 0,
                "imported_profiles": 0,
                "imported_watch_symbols": 0,
                "notes": [],
            },
        },
    }


def _merge_schema(data: Dict[str, Any]) -> Dict[str, Any]:
    base = empty_ledger()
    if not isinstance(data, dict):
        return base

    base.update(data)
    base.setdefault("version", LEDGER_VERSION)
    base.setdefault("created_at_tw", now_tw_iso())
    base.setdefault("last_saved_tw", None)

    wc = base.setdefault("watch_center", {})
    wc.setdefault("symbols", [])
    wc.setdefault("hidden_symbols", [])
    wc.setdefault("updated_at_tw", None)
    wc.setdefault("source_policy", "ledger_only_no_auto_backfill")
    wc["symbols"] = _unique_symbols(wc.get("symbols", []))
    wc["hidden_symbols"] = _unique_symbols(wc.get("hidden_symbols", []))

    hidden = set(wc["hidden_symbols"])
    wc["symbols"] = [s for s in wc["symbols"] if s not in hidden]

    if not isinstance(base.get("ticker_profiles"), dict):
        base["ticker_profiles"] = {}
    if not isinstance(base.get("recent_predictions"), list):
        base["recent_predictions"] = []
    if not isinstance(base.get("recent_audits"), list):
        base["recent_audits"] = []

    sg = base.setdefault("storage_guard", {})
    sg.setdefault("storage_mode", "local_runtime_json")
    sg.setdefault("last_write_ok", False)
    sg.setdefault("last_verify_ok", False)
    sg.setdefault("last_error", None)
    sg.setdefault("last_write_at_tw", None)
    sg.setdefault("last_verify_at_tw", None)
    sg.setdefault("remote", remote_status())
    sg["remote"] = remote_status()
    mig = sg.setdefault("migration", {})
    mig.setdefault("last_migration_at_tw", None)
    mig.setdefault("status", "not_run")
    mig.setdefault("imported_predictions", 0)
    mig.setdefault("imported_audits", 0)
    mig.setdefault("imported_profiles", 0)
    mig.setdefault("imported_watch_symbols", 0)
    mig.setdefault("notes", [])
    return base


def ledger_exists(path: str | Path = DEFAULT_LEDGER_PATH) -> bool:
    return Path(path).exists()


def inline_remote_sync_enabled() -> bool:
    """Return True only for configured, explicitly enabled GitHub write-through.

    The app defaults this flag on for completed memory writes.  No network call
    is attempted when the GitHub token/repository is absent, and callers still
    catch every remote failure after the canonical local write succeeds.
    """
    enabled = os.environ.get("TINO_INLINE_REMOTE_SYNC", "1").strip() == "1"
    return bool(enabled and _remote_config().get("configured"))


def load_ledger(
    path: str | Path = DEFAULT_LEDGER_PATH,
    default_symbols: Optional[Iterable[str]] = None,
    initialize_if_missing: bool = True,
    *,
    sync_remote_on_create: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Load ledger. Default symbols are used ONLY when the file does not exist.
    If the file exists and symbols is empty, do NOT backfill defaults.
    """
    p = Path(path)
    if not p.exists():
        data = empty_ledger()
        if default_symbols:
            data["watch_center"]["symbols"] = _unique_symbols(default_symbols)
        data["watch_center"]["updated_at_tw"] = now_tw_iso()
        if initialize_if_missing:
            save_ledger(data, p, sync_remote=sync_remote_on_create)
        return data

    try:
        with p.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        data = empty_ledger()
        data["storage_guard"].update({
            "last_write_ok": False,
            "last_verify_ok": False,
            "last_error": f"load_failed:{type(e).__name__}:{e}",
        })
        return data

    return _merge_schema(raw)


def save_ledger(
    data: Dict[str, Any],
    path: str | Path = DEFAULT_LEDGER_PATH,
    *,
    sync_remote: Optional[bool] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """Atomic write + read-back verification.

    Synchronous remote sync is OFF by default.  This prevents GitHub GET/PUT
    traffic from running inside Streamlit boot, rerun, analysis, Watch Center,
    and Learning writes.  External maintenance jobs can call the explicit
    remote sync functions or opt in with ``TINO_INLINE_REMOTE_SYNC=1``.
    """
    if sync_remote is None:
        sync_remote = inline_remote_sync_enabled()
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ledger = _merge_schema(copy.deepcopy(data))
    ts = now_tw_iso()
    ledger["last_saved_tw"] = ts
    ledger["storage_guard"].update({
        "last_write_at_tw": ts,
        "last_write_ok": False,
        "last_verify_ok": False,
        "last_error": None,
    })

    try:
        fd, tmp_name = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent or Path(".")))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(ledger, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp_name, p)
        ledger["storage_guard"]["last_write_ok"] = True

        with p.open("r", encoding="utf-8") as f:
            check = json.load(f)
        check = _merge_schema(check)

        a = copy.deepcopy(ledger)
        b = copy.deepcopy(check)
        a_sg = a.get("storage_guard", {})
        b_sg = b.get("storage_guard", {})
        for k in ("last_write_ok", "last_verify_ok", "last_error", "last_write_at_tw", "last_verify_at_tw"):
            a_sg.pop(k, None)
            b_sg.pop(k, None)

        ok = a == b
        verify_ts = now_tw_iso()
        check["storage_guard"].update({
            "last_write_ok": True,
            "last_verify_ok": bool(ok),
            "last_verify_at_tw": verify_ts,
            "last_error": None if ok else "read_back_compare_failed",
        })

        fd, tmp_name = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent or Path(".")))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(check, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp_name, p)
        _write_local_backup(p)
        if sync_remote:
            try:
                _sync_file_to_remote(p, shrink_guard=True)
                check.setdefault("storage_guard", {})["remote"] = remote_status()
            except Exception:
                pass
        else:
            check.setdefault("storage_guard", {})["remote"] = remote_status()
            check["storage_guard"]["boot_remote_sync"] = "skipped"
        return bool(ok), check
    except Exception as e:
        ledger["storage_guard"].update({
            "last_write_ok": False,
            "last_verify_ok": False,
            "last_error": f"save_failed:{type(e).__name__}:{e}",
        })
        return False, ledger


def _jsonl_identity(row: Dict[str, Any]) -> str:
    if not isinstance(row, dict):
        return ""
    for k in ("id", "audit_id"):
        if row.get(k):
            return f"{k}:{row.get(k)}"
    return json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)


def _read_jsonl_file(path: Path) -> List[Dict[str, Any]]:
    if not path.exists() or path.is_dir():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
            except Exception:
                continue
    except Exception:
        return []
    return rows


def _append_jsonl_unique(target: Path, rows: List[Dict[str, Any]]) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = {_jsonl_identity(r) for r in _read_jsonl_file(target)}
    imported = 0
    with target.open("a", encoding="utf-8") as f:
        for r in rows:
            ident = _jsonl_identity(r)
            if not ident or ident in existing:
                continue
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
            existing.add(ident)
            imported += 1
    return imported


def _read_json_dict(path: Path) -> Dict[str, Any]:
    if not path.exists() or path.is_dir():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json_dict(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _merge_profiles_file(target: Path, incoming: Dict[str, Any]) -> int:
    if not incoming:
        return 0
    current = _read_json_dict(target)
    count = 0
    for k, v in incoming.items():
        key = normalize_symbol(k)
        if not key:
            continue
        old = current.get(key, {}) if isinstance(current.get(key), dict) else {}
        if isinstance(v, dict):
            old.update(v)
        else:
            old["value"] = v
        current[key] = old
        count += 1
    _write_json_dict(target, current)
    return count




def _recent_identity(row: Dict[str, Any]) -> str:
    """Stable identity for ledger recovery rows."""
    if not isinstance(row, dict):
        return ""
    for k in ("id", "audit_id"):
        if row.get(k):
            return f"{k}:{row.get(k)}"
    ticker = str(row.get("ticker") or "")
    ts = str(row.get("run_time_tw") or row.get("audit_time_tw") or row.get("logged_at_tw") or "")
    target = str(row.get("target") or row.get("target_kind") or row.get("target_trade_date") or "")
    if ticker and ts:
        return f"fallback:{ticker}:{target}:{ts}"
    return json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)


def _merge_recent_rows(existing: List[Dict[str, Any]], incoming: List[Dict[str, Any]], limit: int = 500) -> List[Dict[str, Any]]:
    """Merge newest-first recent rows without duplicating ids."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for row in list(incoming or []) + list(existing or []):
        if not isinstance(row, dict):
            continue
        ident = _recent_identity(row)
        if not ident or ident in seen:
            continue
        seen.add(ident)
        out.append(row)
        if len(out) >= limit:
            break
    return out


def mirror_prediction_to_ledger(
    row: Dict[str, Any],
    path: str | Path = DEFAULT_LEDGER_PATH,
    limit: int = 500,
    *,
    sync_remote: Optional[bool] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """Mirror a prediction row into the compact ledger recovery index."""
    if not isinstance(row, dict) or not row.get("id"):
        return False, {}
    ledger = load_ledger(path, initialize_if_missing=True, sync_remote_on_create=False)
    r = dict(row)
    r.setdefault("logged_at_tw", now_tw_iso())
    ledger["recent_predictions"] = _merge_recent_rows(list(ledger.get("recent_predictions", [])), [r], limit)
    return save_ledger(ledger, path, sync_remote=sync_remote)


def mirror_audit_to_ledger(
    row: Dict[str, Any],
    path: str | Path = DEFAULT_LEDGER_PATH,
    limit: int = 500,
    *,
    sync_remote: Optional[bool] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """Mirror an audit row into the compact ledger recovery index."""
    if not isinstance(row, dict) or not row.get("audit_id"):
        return False, {}
    ledger = load_ledger(path, initialize_if_missing=True, sync_remote_on_create=False)
    r = dict(row)
    r.setdefault("logged_at_tw", now_tw_iso())
    ledger["recent_audits"] = _merge_recent_rows(list(ledger.get("recent_audits", [])), [r], limit)
    return save_ledger(ledger, path, sync_remote=sync_remote)


def mirror_profiles_to_ledger(
    profile_path: str | Path = TICKER_PROFILE,
    path: str | Path = DEFAULT_LEDGER_PATH,
    *,
    sync_remote: Optional[bool] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """Mirror ticker_profiles.json into the compact ledger recovery index."""
    profiles = _read_json_dict(Path(profile_path))
    if not profiles:
        return False, {}
    ledger = load_ledger(path, initialize_if_missing=True, sync_remote_on_create=False)
    ledger.setdefault("ticker_profiles", {}).update(profiles)
    return save_ledger(ledger, path, sync_remote=sync_remote)


def _hydrate_ledger_from_jsonl(ledger: Dict[str, Any], limit: int = 500) -> Dict[str, Any]:
    """Backfill ledger recent_* arrays from existing JSONL logs."""
    preds = list(reversed(_read_jsonl_file(Path(PREDICTION_LOG))[-limit:]))
    audits = list(reversed(_read_jsonl_file(Path(AUDIT_LOG))[-limit:]))
    if preds:
        ledger["recent_predictions"] = _merge_recent_rows(list(ledger.get("recent_predictions", [])), preds, limit)
    if audits:
        ledger["recent_audits"] = _merge_recent_rows(list(ledger.get("recent_audits", [])), audits, limit)
    profiles = _read_json_dict(Path(TICKER_PROFILE))
    if profiles:
        ledger.setdefault("ticker_profiles", {}).update(profiles)
    return ledger


def restore_memory_files_from_ledger(
    ledger: Optional[Dict[str, Any]] = None,
    only_if_empty: bool = True,
    path: str | Path = DEFAULT_LEDGER_PATH,
    limit: int = 500,
) -> Dict[str, Any]:
    """Recover JSONL memory from ledger recent_* arrays after restart/relogin.

    JSONL is still the raw log, but the ledger is the compact survival index. If
    reconnect/relogin creates empty JSONL files, this restores recent rows before
    the Learning Center renders.
    """
    ledger = _merge_schema(ledger or load_ledger(path, initialize_if_missing=False))
    report = {"restored_predictions": 0, "restored_audits": 0, "restored_profiles": 0, "status": "PASS"}
    try:
        pred_target = Path(PREDICTION_LOG)
        audit_target = Path(AUDIT_LOG)
        prof_target = Path(TICKER_PROFILE)
        pred_rows = list(reversed([r for r in ledger.get("recent_predictions", []) if isinstance(r, dict)]))[-limit:]
        audit_rows = list(reversed([r for r in ledger.get("recent_audits", []) if isinstance(r, dict)]))[-limit:]
        if pred_rows and ((not pred_target.exists()) or (not only_if_empty) or _count_jsonl_file(pred_target) == 0):
            report["restored_predictions"] = _append_jsonl_unique(pred_target, pred_rows)
        if audit_rows and ((not audit_target.exists()) or (not only_if_empty) or _count_jsonl_file(audit_target) == 0):
            report["restored_audits"] = _append_jsonl_unique(audit_target, audit_rows)
        profiles = ledger.get("ticker_profiles", {}) if isinstance(ledger.get("ticker_profiles"), dict) else {}
        if profiles and ((not prof_target.exists()) or (not only_if_empty) or prof_target.stat().st_size <= 4):
            report["restored_profiles"] = _merge_profiles_file(prof_target, profiles)
        for fp in (pred_target, audit_target, prof_target):
            _write_local_backup(fp)
    except Exception as exc:
        report["status"] = "WARN"
        report["error"] = f"{type(exc).__name__}: {exc}"
    return report

def _candidate_roots() -> List[Path]:
    roots: List[Path] = []
    for r in [Path.cwd(), Path(__file__).resolve().parent, Path.cwd().parent, Path.home() / ".tino_stock_engine_memory", Path("/tmp/tino_memory")]:
        try:
            rp = r.resolve()
        except Exception:
            rp = r
        if rp not in roots:
            roots.append(rp)
    return roots


def _legacy_candidates(file_name: str, target: Path) -> List[Path]:
    out: List[Path] = []
    target_resolved = target.resolve() if target.exists() else target.resolve().parent / target.name
    for root in _candidate_roots():
        for p in [root / file_name, root / ".tino_memory" / file_name, root / "tino_memory" / file_name]:
            try:
                pr = p.resolve()
            except Exception:
                pr = p
            if pr == target_resolved:
                continue
            if p.exists() and p.is_file() and p not in out:
                out.append(p)
    return out


def _coerce_watch_symbols(data: Any) -> List[str]:
    if isinstance(data, list):
        return _unique_symbols([str(x) for x in data])
    if isinstance(data, dict):
        for key in ("symbols", "watchlist", "watch_center", "items"):
            val = data.get(key)
            if isinstance(val, list):
                return _unique_symbols([str(x) for x in val])
            if isinstance(val, dict) and isinstance(val.get("symbols"), list):
                return _unique_symbols([str(x) for x in val.get("symbols")])
    return []


def ensure_memory_initialized(
    default_symbols: Optional[Iterable[str]] = None,
    migrate: bool = True,
    path: str | Path = DEFAULT_LEDGER_PATH,
    *,
    allow_remote: bool = True,
) -> Dict[str, Any]:
    """Create memory files and migrate older local memory if found.

    This is idempotent and safe to call on every app boot. It never backfills
    Watch Center from holdings/core/default unless default_symbols are supplied
    and the ledger file does not exist yet.
    """
    p = Path(path)
    report: Dict[str, Any] = {
        "memory_dir": str(_TINO_MEMORY_DIR),
        "ledger_path": str(p),
        "created_files": [],
        "imported_predictions": 0,
        "imported_audits": 0,
        "imported_profiles": 0,
        "imported_watch_symbols": 0,
        "notes": [],
        "status": "PASS",
    }

    try:
        # RC2.4.2: restore remote memory first.  Never create empty files before
        # checking the long-term mirror, otherwise a recycled runtime can look PASS
        # while losing older predictions.
        if allow_remote:
            restore_report = remote_restore_memory_files(force=False)
        else:
            restore_report = {
                "configured": bool(_remote_config().get("configured")),
                "backend": _remote_config().get("backend"),
                "status": "SKIPPED_BOOTSAFE",
                "reason": "remote restore disabled in Streamlit boot/rerun path",
            }
        report["remote_restore"] = restore_report
        _TINO_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        # Local backup shrink guard: if a hiccup produced a smaller file, restore .bak.
        for _fp in [Path(PREDICTION_LOG), Path(AUDIT_LOG), Path(TICKER_PROFILE), p]:
            restored, note = _restore_from_local_backup_if_bigger(_fp)
            if restored:
                report["notes"].append(f"{Path(_fp).name}:{note}")
        # Create log/profile files first so Storage Guard does not show MISSING on first boot.
        for fp, kind in [(PREDICTION_LOG, "jsonl"), (AUDIT_LOG, "jsonl"), (TICKER_PROFILE, "json")]:
            fp = Path(fp)
            if not fp.exists():
                fp.parent.mkdir(parents=True, exist_ok=True)
                if kind == "jsonl":
                    fp.write_text("", encoding="utf-8")
                else:
                    fp.write_text("{}\n", encoding="utf-8")
                report["created_files"].append(str(fp))

        ledger = load_ledger(
            p,
            default_symbols=default_symbols,
            initialize_if_missing=True,
            sync_remote_on_create=allow_remote,
        )

        # Restore JSONL logs from ledger compact recovery arrays before the UI
        # calculates Recent T1 audits / formal samples.  This fixes reconnect or
        # login restarts where prediction_log/audit_log reappear empty.
        ledger_restore = restore_memory_files_from_ledger(ledger=ledger, only_if_empty=True, path=p)
        report["ledger_restored_predictions"] = ledger_restore.get("restored_predictions", 0)
        report["ledger_restored_audits"] = ledger_restore.get("restored_audits", 0)
        report["ledger_restored_profiles"] = ledger_restore.get("restored_profiles", 0)
        if any(int(ledger_restore.get(k, 0) or 0) for k in ("restored_predictions", "restored_audits", "restored_profiles")):
            report["notes"].append(f"restored from ledger recovery index: {ledger_restore}")

        if migrate:
            for src in _legacy_candidates(LEGACY_FILE_NAMES["prediction_log"], Path(PREDICTION_LOG)):
                n = _append_jsonl_unique(Path(PREDICTION_LOG), _read_jsonl_file(src))
                report["imported_predictions"] += n
                if n:
                    report["notes"].append(f"migrated prediction_log from {src}: {n}")

            for src in _legacy_candidates(LEGACY_FILE_NAMES["audit_log"], Path(AUDIT_LOG)):
                n = _append_jsonl_unique(Path(AUDIT_LOG), _read_jsonl_file(src))
                report["imported_audits"] += n
                if n:
                    report["notes"].append(f"migrated audit_log from {src}: {n}")

            for src in _legacy_candidates(LEGACY_FILE_NAMES["ticker_profiles"], Path(TICKER_PROFILE)):
                n = _merge_profiles_file(Path(TICKER_PROFILE), _read_json_dict(src))
                report["imported_profiles"] += n
                if n:
                    report["notes"].append(f"migrated ticker_profiles from {src}: {n}")

            # Mirror ticker profile into ledger too, so future UI can read one place.
            profiles = _read_json_dict(Path(TICKER_PROFILE))
            if profiles:
                ledger.setdefault("ticker_profiles", {}).update(profiles)

            # Watchlist migration: import only when current ledger list is empty.
            wc = ledger.setdefault("watch_center", {})
            current_symbols = _unique_symbols(wc.get("symbols", []))
            hidden = set(_unique_symbols(wc.get("hidden_symbols", [])))
            if not current_symbols:
                imported_symbols: List[str] = []
                for src in _legacy_candidates(LEGACY_FILE_NAMES["watch_center_list"], p):
                    try:
                        raw = json.loads(src.read_text(encoding="utf-8"))
                    except Exception:
                        raw = None
                    for s in _coerce_watch_symbols(raw):
                        if s not in imported_symbols and s not in hidden:
                            imported_symbols.append(s)
                if imported_symbols:
                    wc["symbols"] = imported_symbols[:60]
                    wc["updated_at_tw"] = now_tw_iso()
                    report["imported_watch_symbols"] = len(imported_symbols[:60])
                    report["notes"].append(f"migrated watch_center_list symbols: {len(imported_symbols[:60])}")

        ledger = _hydrate_ledger_from_jsonl(_merge_schema(ledger), limit=500)
        mig = ledger.setdefault("storage_guard", {}).setdefault("migration", {})
        mig.update({
            "last_migration_at_tw": now_tw_iso(),
            "status": report["status"],
            "imported_predictions": int(report["imported_predictions"]),
            "imported_audits": int(report["imported_audits"]),
            "imported_profiles": int(report["imported_profiles"]),
            "imported_watch_symbols": int(report["imported_watch_symbols"]),
            "notes": report["notes"][-20:],
        })
        ok, saved = save_ledger(ledger, p, sync_remote=allow_remote)
        if not ok:
            report["status"] = "VERIFY_FAIL"
        report["ledger_saved"] = bool(ok)
        report["ledger_exists"] = p.exists()
        for _fp in [Path(PREDICTION_LOG), Path(AUDIT_LOG), Path(TICKER_PROFILE), p]:
            _write_local_backup(_fp)
        if allow_remote:
            report["remote_sync"] = sync_all_memory_files_to_remote()
        else:
            report["remote_sync"] = {
                "configured": bool(_remote_config().get("configured")),
                "backend": _remote_config().get("backend"),
                "status": "SKIPPED_BOOTSAFE",
                "reason": "remote sync disabled in Streamlit boot/rerun path",
            }
        return report
    except Exception as exc:
        report["status"] = "FAIL"
        report["error"] = f"{type(exc).__name__}: {exc}"
        return report


_BOOTSAFE_INIT_LOCK = threading.RLock()
_BOOTSAFE_INIT_CACHE: Dict[Tuple[str, Tuple[str, ...], bool], Dict[str, Any]] = {}


def ensure_memory_initialized_bootsafe(
    default_symbols: Optional[Iterable[str]] = None,
    migrate: bool = True,
    path: str | Path = DEFAULT_LEDGER_PATH,
) -> Dict[str, Any]:
    """One-time process bootstrap with fail-safe GitHub restore.

    Remote restore is attempted only once per Python process and before local
    migration/empty-file creation.  GitHub errors are returned as diagnostics;
    they cannot stop Streamlit or the main analysis engine.
    """
    normalized_defaults = tuple(_unique_symbols(default_symbols or []))
    key = (str(Path(path).resolve()), normalized_defaults, bool(migrate))
    with _BOOTSAFE_INIT_LOCK:
        cached = _BOOTSAFE_INIT_CACHE.get(key)
        if cached is not None:
            return copy.deepcopy(cached)

        remote_report: Dict[str, Any]
        try:
            remote_report = remote_restore_memory_files(force=False)
        except Exception as exc:
            remote_report = {
                "configured": bool(_remote_config().get("configured")),
                "status": "WARN",
                "error": f"{type(exc).__name__}: {exc}",
            }

        report = ensure_memory_initialized(
            default_symbols=normalized_defaults,
            migrate=migrate,
            path=path,
            allow_remote=False,
        )
        report["remote_restore"] = remote_report
        seed_report: Dict[str, Any] = {"status": "SKIPPED", "synced_files": [], "errors": []}
        missing_remote = list(remote_report.get("missing_files") or [])
        if remote_report.get("configured") and remote_report.get("status") in {"PASS", "EMPTY_REMOTE"} and missing_remote:
            seed_report["status"] = "PASS"
            for name in missing_remote:
                local_path = _local_path_for_memory_file(name)
                if not local_path.exists() or local_path.is_dir():
                    continue
                ok, err = _sync_file_to_remote(local_path, shrink_guard=True)
                if ok:
                    seed_report["synced_files"].append(name)
                else:
                    seed_report["status"] = "WARN"
                    seed_report["errors"].append(f"{name}:{err}")
        report["remote_seed"] = seed_report
        report["boot_mode"] = (
            "github_restore_once_per_process"
            if remote_report.get("configured")
            else "local_only_once_per_process"
        )
        if remote_report.get("configured") and remote_report.get("status") not in {"PASS", "EMPTY_REMOTE"}:
            report.setdefault("notes", []).append(
                f"remote restore degraded: {remote_report.get('error') or remote_report.get('status')}"
            )
        _BOOTSAFE_INIT_CACHE[key] = copy.deepcopy(report)
        return copy.deepcopy(report)

def get_watch_symbols(ledger: Dict[str, Any]) -> List[str]:
    wc = _merge_schema(ledger)["watch_center"]
    hidden = set(wc.get("hidden_symbols", []))
    return [s for s in _unique_symbols(wc.get("symbols", [])) if s not in hidden]


def set_watch_symbols(
    symbols: Iterable[str],
    path: str | Path = DEFAULT_LEDGER_PATH,
    default_symbols: Optional[Iterable[str]] = None,
) -> Tuple[bool, Dict[str, Any]]:
    ledger = load_ledger(path, default_symbols=default_symbols, initialize_if_missing=True)
    hidden = set(ledger["watch_center"].get("hidden_symbols", []))
    ledger["watch_center"]["symbols"] = [s for s in _unique_symbols(symbols) if s not in hidden]
    ledger["watch_center"]["updated_at_tw"] = now_tw_iso()
    return save_ledger(ledger, path)


def add_watch_symbol(
    symbol: str,
    path: str | Path = DEFAULT_LEDGER_PATH,
    default_symbols: Optional[Iterable[str]] = None,
    unhide: bool = True,
) -> Tuple[bool, Dict[str, Any]]:
    s = normalize_symbol(symbol)
    ledger = load_ledger(path, default_symbols=default_symbols, initialize_if_missing=True)
    wc = ledger["watch_center"]
    if unhide:
        wc["hidden_symbols"] = [x for x in _unique_symbols(wc.get("hidden_symbols", [])) if x != s]
    symbols = get_watch_symbols(ledger)
    if s and s not in symbols:
        symbols.append(s)
    wc["symbols"] = symbols
    wc["updated_at_tw"] = now_tw_iso()
    return save_ledger(ledger, path)


def remove_watch_symbol(
    symbol: str,
    path: str | Path = DEFAULT_LEDGER_PATH,
    default_symbols: Optional[Iterable[str]] = None,
    remember_hidden: bool = True,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Remove from Watch Center and optionally mark hidden so holdings/core/default cannot auto-backfill it.
    """
    s = normalize_symbol(symbol)
    ledger = load_ledger(path, default_symbols=default_symbols, initialize_if_missing=True)
    wc = ledger["watch_center"]
    wc["symbols"] = [x for x in _unique_symbols(wc.get("symbols", [])) if x != s]
    if remember_hidden and s:
        hidden = _unique_symbols(wc.get("hidden_symbols", []))
        if s not in hidden:
            hidden.append(s)
        wc["hidden_symbols"] = hidden
    wc["updated_at_tw"] = now_tw_iso()
    return save_ledger(ledger, path)


def import_symbols_explicitly(
    symbols: Iterable[str],
    path: str | Path = DEFAULT_LEDGER_PATH,
    default_symbols: Optional[Iterable[str]] = None,
    clear_hidden_for_imported: bool = True,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Use only behind an explicit button like '匯入持股'. Never call from auto-refresh.
    """
    ledger = load_ledger(path, default_symbols=default_symbols, initialize_if_missing=True)
    wc = ledger["watch_center"]
    incoming = _unique_symbols(symbols)
    if clear_hidden_for_imported:
        incoming_set = set(incoming)
        wc["hidden_symbols"] = [x for x in _unique_symbols(wc.get("hidden_symbols", [])) if x not in incoming_set]
    merged = get_watch_symbols(ledger)
    for s in incoming:
        if s not in merged:
            merged.append(s)
    hidden = set(wc.get("hidden_symbols", []))
    wc["symbols"] = [s for s in merged if s not in hidden]
    wc["updated_at_tw"] = now_tw_iso()
    return save_ledger(ledger, path)


def append_recent_prediction(pred: Dict[str, Any], path: str | Path = DEFAULT_LEDGER_PATH, limit: int = 200) -> Tuple[bool, Dict[str, Any]]:
    return mirror_prediction_to_ledger(pred, path=path, limit=limit)


def append_recent_audit(audit: Dict[str, Any], path: str | Path = DEFAULT_LEDGER_PATH, limit: int = 200) -> Tuple[bool, Dict[str, Any]]:
    return mirror_audit_to_ledger(audit, path=path, limit=limit)


def upsert_ticker_profile(symbol: str, profile: Dict[str, Any], path: str | Path = DEFAULT_LEDGER_PATH) -> Tuple[bool, Dict[str, Any]]:
    s = normalize_symbol(symbol)
    ledger = load_ledger(path, initialize_if_missing=True)
    old = dict(ledger.get("ticker_profiles", {}).get(s, {}))
    old.update(profile or {})
    old["updated_at_tw"] = now_tw_iso()
    ledger.setdefault("ticker_profiles", {})[s] = old
    return save_ledger(ledger, path)


def storage_status(path: str | Path = DEFAULT_LEDGER_PATH) -> Dict[str, Any]:
    ledger = load_ledger(path, initialize_if_missing=False)
    sg = ledger.get("storage_guard", {})
    mig = sg.get("migration", {}) if isinstance(sg.get("migration", {}), dict) else {}
    remote = remote_status()
    return {
        "path": str(Path(path).resolve()),
        "exists": Path(path).exists(),
        "storage_mode": sg.get("storage_mode", "local_runtime_json"),
        "last_write_ok": sg.get("last_write_ok"),
        "last_verify_ok": sg.get("last_verify_ok"),
        "last_error": sg.get("last_error"),
        "last_write_at_tw": sg.get("last_write_at_tw"),
        "last_verify_at_tw": sg.get("last_verify_at_tw"),
        "migration_status": mig.get("status"),
        "migration_at_tw": mig.get("last_migration_at_tw"),
        "imported_predictions": mig.get("imported_predictions"),
        "imported_audits": mig.get("imported_audits"),
        "imported_profiles": mig.get("imported_profiles"),
        "imported_watch_symbols": mig.get("imported_watch_symbols"),
        "migration_notes": mig.get("notes", []),
        "remote_configured": remote.get("configured"),
        "remote_backend": remote.get("backend"),
        "remote_status": remote.get("status"),
        "remote_repo": remote.get("repo"),
        "remote_branch": remote.get("branch"),
        "remote_memory_dir": remote.get("memory_dir"),
        "remote_branch_ready": remote.get("branch_ready"),
        "remote_last_restore": remote.get("last_restore_at_tw"),
        "remote_last_sync": remote.get("last_sync_at_tw"),
        "remote_last_verified": remote.get("last_verified_at_tw"),
        "remote_restored_files": remote.get("restored_files"),
        "remote_synced_files": remote.get("synced_files"),
        "remote_shrink_warnings": remote.get("shrink_warnings"),
        "remote_files": remote.get("remote_files") or {},
        "remote_error": remote.get("error"),
        "prediction_log_rows": _count_jsonl_file(PREDICTION_LOG),
        "audit_log_rows": _count_jsonl_file(AUDIT_LOG),
        "ledger_recent_predictions": len((ledger.get("recent_predictions") or []) if isinstance(ledger, dict) else []),
        "ledger_recent_audits": len((ledger.get("recent_audits") or []) if isinstance(ledger, dict) else []),
    }
