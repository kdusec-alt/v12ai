# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


DEFAULT_VISIBLE_LOG_ROWS = 900


def _default_memory_dir() -> Path:
    """Return the first writable TINO memory directory.

    The module directory is kept as a second candidate because Streamlit Cloud
    can relaunch with a different working directory after login/reconnect.
    """
    env = os.environ.get("TINO_MEMORY_DIR")
    if env:
        return Path(env)
    app_dir = Path(__file__).resolve().parent
    candidates = [
        Path.cwd() / ".tino_memory",
        app_dir / ".tino_memory",
        Path.home() / ".tino_stock_engine_memory",
        Path("/tmp/tino_memory"),
    ]
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return candidate
        except Exception:
            continue
    return Path("/tmp/tino_memory")


MEMORY_DIR = _default_memory_dir()
PREDICTION_LOG = MEMORY_DIR / "prediction_log.jsonl"
AUDIT_LOG = MEMORY_DIR / "audit_log.jsonl"
TICKER_PROFILE = MEMORY_DIR / "ticker_profiles.json"


def _post_memory_write(path: Path, row: Optional[Dict[str, Any]] = None) -> None:
    """Persist one completed memory write without endangering analysis.

    JSONL/JSON remains the canonical local source.  When GitHub persistence is
    configured, only the file that just changed is reconciled and verified on
    the remote memory branch.  The optional ledger mirror stays disabled by
    default because duplicating full Prediction DNA rows into the ledger can
    create unnecessary Streamlit memory pressure.
    """
    mirror_enabled = os.environ.get("TINO_INLINE_MEMORY_MIRROR", "0").strip() == "1"
    try:
        from tino_persistent_store import (  # type: ignore
            _write_local_backup,
            _sync_file_to_remote,
            inline_remote_sync_enabled,
            mirror_prediction_to_ledger,
            mirror_audit_to_ledger,
            mirror_profiles_to_ledger,
        )

        remote_enabled = inline_remote_sync_enabled()
        if not mirror_enabled and not remote_enabled:
            return

        p = Path(path)
        _write_local_backup(p)
        if mirror_enabled:
            if row and p.name == "prediction_log.jsonl":
                mirror_prediction_to_ledger(row, sync_remote=False)
            elif row and p.name == "audit_log.jsonl":
                mirror_audit_to_ledger(row, sync_remote=False)
            elif p.name == "ticker_profiles.json":
                mirror_profiles_to_ledger(p, sync_remote=False)
        if remote_enabled:
            _sync_file_to_remote(p, shrink_guard=True)
    except Exception:
        # Learning persistence is fail-safe: the formal local write already
        # succeeded, so a remote outage must never invalidate the forecast.
        return


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    """Append one JSON object without loading the existing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(row, ensure_ascii=False, default=str)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(payload + "\n")
        handle.flush()
    _post_memory_write(path, row)


def read_jsonl(path: Path, limit: int = 200) -> List[Dict[str, Any]]:
    """Read a bounded JSONL tail without ``read_text().splitlines()``.

    The previous implementation loaded the complete log into memory before
    slicing.  Prediction DNA rows can be large, so that pattern could create a
    native Pandas/PyArrow memory spike in Streamlit.  ``deque`` keeps only the
    requested tail and has stable memory use.
    """
    try:
        max_rows = max(0, int(limit))
    except Exception:
        max_rows = 200
    if max_rows <= 0 or not Path(path).exists():
        return []

    lines: deque[str] = deque(maxlen=max_rows)
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.strip():
                    lines.append(line)
    except Exception:
        return []

    rows: List[Dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
            if isinstance(value, dict) and value:
                rows.append(value)
        except Exception:
            continue
    return rows


def read_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not Path(path).exists():
        return dict(default or {})
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else dict(default or {})
    except Exception:
        return dict(default or {})


def write_json(path: Path, data: Dict[str, Any]) -> None:
    """Atomically replace a compact JSON object."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
    _post_memory_write(path)


def _candidate_paths(primary: Path) -> List[Path]:
    """Return safe local recovery candidates in priority order."""
    primary = Path(primary)
    app_dir = Path(__file__).resolve().parent
    cwd = Path.cwd()
    name = primary.name
    candidates = [
        primary,
        app_dir / ".tino_memory" / name,
        cwd / ".tino_memory" / name,
        app_dir / name,
        cwd / name,
        primary.parent / "_backup" / f"{name}.bak",
        app_dir / f"{name}.bak",
        cwd / f"{name}.bak",
    ]
    unique: List[Path] = []
    seen = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve())
        except Exception:
            key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _row_identity(row: Dict[str, Any], kind: str) -> str:
    preferred = ("audit_id", "prediction_id", "id") if kind == "audit" else ("id", "prediction_id")
    for key in preferred:
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
    target = str(row.get("target_trade_date") or row.get("target") or row.get("target_kind") or "")
    if ticker and stamp:
        return f"fallback:{kind}:{ticker}:{target}:{stamp}"
    try:
        return "json:" + json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return f"object:{id(row)}"


def _dedupe_rows_keep_latest(
    rows: Iterable[Dict[str, Any]],
    *,
    kind: str,
    limit: int,
) -> List[Dict[str, Any]]:
    """Return one canonical row per identity, keeping the newest occurrence.

    JSONL is append-only, so a repeated ``id`` can appear after reconnect,
    recovery or an interrupted mirror.  Every Prediction Log consumer must see
    the same de-duplicated tail; otherwise Storage Guard and Learning Center can
    report different counts for the same file.
    """
    try:
        max_rows = max(1, int(limit or 1))
    except Exception:
        max_rows = DEFAULT_VISIBLE_LOG_ROWS

    clean = [row for row in rows if isinstance(row, dict) and row]
    output_reversed: List[Dict[str, Any]] = []
    seen = set()
    for row in reversed(clean):
        ident = _row_identity(row, kind)
        if ident and ident in seen:
            continue
        if ident:
            seen.add(ident)
        output_reversed.append(row)
        if len(output_reversed) >= max_rows:
            break
    output_reversed.reverse()
    return output_reversed


def _merge_jsonl_candidates(primary: Path, limit: int, kind: str) -> List[Dict[str, Any]]:
    """Read the active tail and use legacy files only as empty-file recovery.

    The common path touches one small JSONL file.  Historical root/backup files
    are scanned only when the active ``.tino_memory`` file has no valid rows,
    preventing doubled I/O on every Streamlit rerun.
    """
    max_rows = max(1, int(limit or 1))
    active = _dedupe_rows_keep_latest(
        read_jsonl(primary, max_rows),
        kind=kind,
        limit=max_rows,
    )
    if active and os.environ.get("TINO_MEMORY_MERGE_LEGACY", "0").strip() != "1":
        return active[-max_rows:]

    collected: List[Dict[str, Any]] = list(active)
    seen = {_row_identity(row, kind) for row in collected}
    seen.discard("")
    candidates = _candidate_paths(primary)
    for path in candidates[1:]:
        if not path.exists() or path.is_dir():
            continue
        for row in read_jsonl(path, max_rows):
            ident = _row_identity(row, kind)
            if ident and ident in seen:
                continue
            if ident:
                seen.add(ident)
            collected.append(row)

    def _stamp(row: Dict[str, Any]) -> str:
        return str(
            row.get("run_time_tw")
            or row.get("audit_time_tw")
            or row.get("logged_at_tw")
            or row.get("created_at")
            or row.get("run_date_tw")
            or row.get("audit_date_tw")
            or ""
        )

    collected.sort(key=_stamp)
    return _dedupe_rows_keep_latest(collected, kind=kind, limit=max_rows)


def read_prediction_log(limit: int = 100) -> List[Dict[str, Any]]:
    return _merge_jsonl_candidates(PREDICTION_LOG, limit, "prediction")


def read_audit_log(limit: int = 100) -> List[Dict[str, Any]]:
    return _merge_jsonl_candidates(AUDIT_LOG, limit, "audit")


def load_profiles() -> Dict[str, Any]:
    """Load active profiles, falling back to legacy copies only when empty."""
    active = read_json(TICKER_PROFILE, {})
    if active:
        return active
    merged: Dict[str, Any] = {}
    for path in reversed(_candidate_paths(TICKER_PROFILE)[1:]):
        if not path.exists() or path.is_dir():
            continue
        data = read_json(path, {})
        if isinstance(data, dict):
            merged.update(data)
    return merged


def save_profiles(profiles: Dict[str, Any]) -> None:
    write_json(TICKER_PROFILE, profiles)


def _line_count(path: Path, cap: int = 200_000) -> int:
    if not path.exists() or path.is_dir():
        return 0
    count = 0
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for count, _ in enumerate(handle, start=1):
                if count >= cap:
                    break
    except Exception:
        return 0
    return count


def memory_diagnostics(limit: int = DEFAULT_VISIBLE_LOG_ROWS) -> Dict[str, Any]:
    """Small scalar-only status payload for the Admin Learning Center."""
    try:
        visible_limit = max(1, int(limit or 1))
    except Exception:
        visible_limit = DEFAULT_VISIBLE_LOG_ROWS

    prediction_rows = read_prediction_log(visible_limit)
    audit_rows = read_audit_log(visible_limit)
    files = []
    for path in (PREDICTION_LOG, AUDIT_LOG, TICKER_PROFILE):
        try:
            physical_rows = _line_count(path) if path.suffix == ".jsonl" else None
            if path == PREDICTION_LOG:
                visible_rows = len(prediction_rows)
            elif path == AUDIT_LOG:
                visible_rows = len(audit_rows)
            else:
                visible_rows = None
            files.append({
                "file": path.name,
                "path": str(path),
                "exists": path.exists(),
                "size_bytes": path.stat().st_size if path.exists() else 0,
                "rows": visible_rows,
                "physical_rows": physical_rows,
            })
        except Exception as exc:
            files.append({
                "file": path.name,
                "path": str(path),
                "exists": False,
                "size_bytes": 0,
                "rows": None,
                "error": f"{type(exc).__name__}: {exc}",
            })
    return {
        "memory_dir": str(MEMORY_DIR),
        "files": files,
        "visible_limit": visible_limit,
        "prediction_rows_visible": len(prediction_rows),
        "audit_rows_visible": len(audit_rows),
        "profile_count": len(load_profiles()),
    }
