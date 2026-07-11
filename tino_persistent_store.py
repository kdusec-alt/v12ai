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
import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
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
    "last_restore_at_tw": None,
    "last_sync_at_tw": None,
    "restored_files": [],
    "synced_files": [],
    "shrink_warnings": [],
    "error": None,
}


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
    token = _secret("TINO_GITHUB_TOKEN") or _secret("GITHUB_TOKEN")
    repo = _secret("TINO_GITHUB_REPO") or _secret("GITHUB_REPOSITORY")
    branch = _secret("TINO_GITHUB_BRANCH", "main") or "main"
    memory_dir = (_secret("TINO_GITHUB_MEMORY_DIR", ".tino_memory") or ".tino_memory").strip("/")
    configured = bool(token and repo and "/" in str(repo))
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
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=_gh_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = resp.read().decode("utf-8")
            return int(resp.status), json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        try:
            parsed = json.loads(body) if body else {}
        except Exception:
            parsed = {"message": body}
        return int(e.code), parsed


def _remote_path_for(local_path: str | Path) -> str:
    cfg = _remote_config()
    return f"{cfg['memory_dir'].strip('/')}/{Path(local_path).name}"


def _github_read_file(file_name: str) -> Tuple[bool, Optional[bytes], Optional[str], Optional[str]]:
    cfg = _remote_config()
    if not cfg["configured"]:
        return False, None, None, "remote_not_configured"
    remote_path = f"{cfg['memory_dir'].strip('/')}/{Path(file_name).name}"
    url = f"https://api.github.com/repos/{cfg['repo']}/contents/{remote_path}?ref={cfg['branch']}"
    status, obj = _gh_request("GET", url, cfg["token"])
    if status == 404:
        return False, None, None, "remote_missing"
    if status >= 300:
        return False, None, None, f"github_get_{status}:{obj.get('message', obj)}"
    try:
        content = base64.b64decode(str(obj.get("content") or "").encode("utf-8"))
        sha = str(obj.get("sha") or "")
        return True, content, sha, None
    except Exception as exc:
        return False, None, None, f"github_decode_failed:{type(exc).__name__}:{exc}"


def _github_write_file(local_path: str | Path, message: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    cfg = _remote_config()
    if not cfg["configured"]:
        return False, "remote_not_configured"
    p = Path(local_path)
    if not p.exists() or p.is_dir():
        return False, "local_missing"
    ok, _old_bytes, sha, _err = _github_read_file(p.name)
    remote_path = _remote_path_for(p)
    url = f"https://api.github.com/repos/{cfg['repo']}/contents/{remote_path}"
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
    """Restore local memory from GitHub remote before local init.

    Shrink rule: remote wins when local is missing/empty or remote has more
    jsonl rows.  This prevents a recycled Streamlit runtime from starting with
    empty files and silently losing older learning history.
    """
    cfg = _remote_config()
    report: Dict[str, Any] = {
        "configured": cfg["configured"],
        "backend": cfg["backend"],
        "status": "LOCAL_ONLY" if not cfg["configured"] else "PASS",
        "restored_files": [],
        "shrink_warnings": [],
        "error": None,
        "last_restore_at_tw": now_tw_iso(),
    }
    if not cfg["configured"]:
        _LAST_REMOTE_REPORT.update(report)
        return report

    for name in _MEMORY_FILES:
        local = _local_path_for_memory_file(name)
        try:
            exists, blob, _sha, err = _github_read_file(name)
            if not exists or blob is None:
                if err not in (None, "remote_missing"):
                    report["shrink_warnings"].append(f"{name}:{err}")
                continue
            local.parent.mkdir(parents=True, exist_ok=True)
            should_restore = force or (not local.exists()) or (local.stat().st_size == 0)
            if name.endswith(".jsonl") and local.exists():
                remote_n = _count_jsonl_bytes(blob)
                local_n = _count_jsonl_file(local)
                if remote_n > local_n:
                    should_restore = True
                    report["shrink_warnings"].append(f"remote_has_more_{name}:{local_n}->{remote_n}")
            elif local.exists() and name.endswith(".json"):
                if len(blob) > max(2, local.stat().st_size) and local.stat().st_size <= 4:
                    should_restore = True
            if should_restore:
                local.write_bytes(blob)
                _write_local_backup(local)
                report["restored_files"].append(name)
        except Exception as exc:
            report["status"] = "WARN"
            report["error"] = f"restore_{name}:{type(exc).__name__}:{exc}"
    _LAST_REMOTE_REPORT.update(report)
    return report


def _sync_file_to_remote(path: str | Path, shrink_guard: bool = True) -> Tuple[bool, Optional[str]]:
    cfg = _remote_config()
    if not cfg["configured"]:
        return False, "remote_not_configured"
    p = Path(path)
    if not p.exists() or p.is_dir():
        return False, "local_missing"
    if shrink_guard:
        exists, blob, _sha, err = _github_read_file(p.name)
        if exists and blob is not None and p.name.endswith(".jsonl"):
            remote_n = _count_jsonl_bytes(blob)
            local_n = _count_jsonl_file(p)
            if local_n < remote_n:
                msg = f"shrink_guard_blocked:{p.name}:local {local_n} < remote {remote_n}"
                _LAST_REMOTE_REPORT.setdefault("shrink_warnings", []).append(msg)
                return False, msg
        elif err not in (None, "remote_missing"):
            # If we cannot read the remote, never risk overwriting a larger remote
            # memory file with an empty/restarted local file.  This is the key
            # reconnect/offline protection.
            msg = f"remote_read_blocked:{p.name}:{err}"
            _LAST_REMOTE_REPORT.setdefault("shrink_warnings", []).append(msg)
            return False, msg
    ok, err = _github_write_file(p, message=f"TINO memory sync: {p.name}")
    if ok:
        _LAST_REMOTE_REPORT["configured"] = True
        _LAST_REMOTE_REPORT["backend"] = "github"
        _LAST_REMOTE_REPORT["status"] = "PASS"
        _LAST_REMOTE_REPORT["last_sync_at_tw"] = now_tw_iso()
        synced = list(_LAST_REMOTE_REPORT.get("synced_files", []))
        if p.name not in synced:
            synced.append(p.name)
        _LAST_REMOTE_REPORT["synced_files"] = synced[-20:]
    else:
        _LAST_REMOTE_REPORT["status"] = "WARN"
        _LAST_REMOTE_REPORT["error"] = err
    return ok, err


def sync_all_memory_files_to_remote() -> Dict[str, Any]:
    cfg = _remote_config()
    report: Dict[str, Any] = {
        "configured": cfg["configured"],
        "backend": cfg["backend"],
        "status": "LOCAL_ONLY" if not cfg["configured"] else "PASS",
        "synced_files": [],
        "shrink_warnings": [],
        "error": None,
        "last_sync_at_tw": now_tw_iso(),
    }
    if not cfg["configured"]:
        _LAST_REMOTE_REPORT.update(report)
        return report
    for name in _MEMORY_FILES:
        p = _local_path_for_memory_file(name)
        if not p.exists():
            continue
        ok, err = _sync_file_to_remote(p, shrink_guard=True)
        if ok:
            report["synced_files"].append(name)
        elif err and str(err).startswith("shrink_guard_blocked"):
            report["status"] = "SHRINK_BLOCKED"
            report["shrink_warnings"].append(str(err))
        elif err != "remote_not_configured":
            report["status"] = "WARN"
            report["error"] = err
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
    """Whether normal app writes may perform synchronous GitHub network I/O.

    Default is OFF.  Streamlit render/analysis paths must remain local-only;
    external maintenance jobs can call ``sync_all_memory_files_to_remote``
    directly or set ``TINO_INLINE_REMOTE_SYNC=1`` deliberately.
    """
    return os.environ.get("TINO_INLINE_REMOTE_SYNC", "0").strip() == "1"


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


def mirror_prediction_to_ledger(row: Dict[str, Any], path: str | Path = DEFAULT_LEDGER_PATH, limit: int = 500) -> Tuple[bool, Dict[str, Any]]:
    """Mirror a prediction row into the ledger so UI can recover after reconnect."""
    if not isinstance(row, dict) or not row.get("id"):
        return False, {}
    ledger = load_ledger(path, initialize_if_missing=True)
    r = dict(row)
    r.setdefault("logged_at_tw", now_tw_iso())
    ledger["recent_predictions"] = _merge_recent_rows(list(ledger.get("recent_predictions", [])), [r], limit)
    return save_ledger(ledger, path)


def mirror_audit_to_ledger(row: Dict[str, Any], path: str | Path = DEFAULT_LEDGER_PATH, limit: int = 500) -> Tuple[bool, Dict[str, Any]]:
    """Mirror an audit row into the ledger so Recent T1 audits do not disappear."""
    if not isinstance(row, dict) or not row.get("audit_id"):
        return False, {}
    ledger = load_ledger(path, initialize_if_missing=True)
    r = dict(row)
    r.setdefault("logged_at_tw", now_tw_iso())
    ledger["recent_audits"] = _merge_recent_rows(list(ledger.get("recent_audits", [])), [r], limit)
    return save_ledger(ledger, path)


def mirror_profiles_to_ledger(profile_path: str | Path = TICKER_PROFILE, path: str | Path = DEFAULT_LEDGER_PATH) -> Tuple[bool, Dict[str, Any]]:
    """Mirror ticker_profiles.json into the ledger recovery index."""
    profiles = _read_json_dict(Path(profile_path))
    if not profiles:
        return False, {}
    ledger = load_ledger(path, initialize_if_missing=True)
    ledger.setdefault("ticker_profiles", {}).update(profiles)
    return save_ledger(ledger, path)


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
    """Initialize local memory once per process without any remote network I/O.

    This is the only initializer that the Streamlit render path should call.
    Remote restore/sync remains available through explicit admin/external jobs by
    calling :func:`ensure_memory_initialized` with its default ``allow_remote``.
    """
    normalized_defaults = tuple(_unique_symbols(default_symbols or []))
    key = (str(Path(path).resolve()), normalized_defaults, bool(migrate))
    with _BOOTSAFE_INIT_LOCK:
        cached = _BOOTSAFE_INIT_CACHE.get(key)
        if cached is not None:
            return copy.deepcopy(cached)
        report = ensure_memory_initialized(
            default_symbols=normalized_defaults,
            migrate=migrate,
            path=path,
            allow_remote=False,
        )
        report["boot_mode"] = "local_only_once_per_process"
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
        "remote_last_restore": remote.get("last_restore_at_tw"),
        "remote_last_sync": remote.get("last_sync_at_tw"),
        "remote_restored_files": remote.get("restored_files"),
        "remote_synced_files": remote.get("synced_files"),
        "remote_shrink_warnings": remote.get("shrink_warnings"),
        "remote_error": remote.get("error"),
        "prediction_log_rows": _count_jsonl_file(PREDICTION_LOG),
        "audit_log_rows": _count_jsonl_file(AUDIT_LOG),
        "ledger_recent_predictions": len((ledger.get("recent_predictions") or []) if isinstance(ledger, dict) else []),
        "ledger_recent_audits": len((ledger.get("recent_audits") or []) if isinstance(ledger, dict) else []),
    }
