# -*- coding: utf-8 -*-
"""Bounded, append-only storage for V13 Research records.

Hot-path guarantees
-------------------
* No full-history scan.
* No pandas / DataFrame creation.
* Duplicate checks use file-signature-aware memory caches.
* UI reads are bounded tails and execute only inside AI Research Lab.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Tuple

from memory_store import MEMORY_DIR

RESEARCH_DIR = Path(MEMORY_DIR) / "v13_research"
RESEARCH_SEED_LOG = RESEARCH_DIR / "research_seed.jsonl"
GENOME_SNAPSHOT_LOG = RESEARCH_DIR / "genome_snapshot.jsonl"
DETECTION_EVENT_LOG = RESEARCH_DIR / "detection_event.jsonl"
CLOSE_RECHECK_LOG = RESEARCH_DIR / "close_recheck.jsonl"
CLOSE_RECHECK_STATE = RESEARCH_DIR / "close_recheck_state.json"
MACRO_EVENT_LOG = RESEARCH_DIR / "macro_event.jsonl"

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_ID_CACHE: Dict[Tuple[str, str], Tuple[int, int, set[str]]] = {}
_ROWS_CACHE: Dict[Tuple[str, int], Tuple[int, int, List[Dict[str, Any]]]] = {}
_RECENT_BY_TICKER: Dict[str, List[Dict[str, Any]]] = {}
_RECENT_CACHE_SIGNATURE: Tuple[int, int] | None = None
_REMOTE_SYNC_LOCK = threading.RLock()
_REMOTE_SYNC_PENDING: set[str] = set()
_REMOTE_SYNC_THREAD: threading.Thread | None = None


def _research_remote_sync_worker() -> None:
    """Flush changed V13 files without blocking the formal analysis path."""
    global _REMOTE_SYNC_THREAD
    # Small debounce lets Genome + Detection + Macro writes collapse into one
    # bounded queue after a completed prediction.
    time.sleep(0.35)
    while True:
        with _REMOTE_SYNC_LOCK:
            if not _REMOTE_SYNC_PENDING:
                _REMOTE_SYNC_THREAD = None
                return
            path_text = _REMOTE_SYNC_PENDING.pop()
        try:
            from tino_persistent_store import _sync_file_to_remote  # type: ignore
            _sync_file_to_remote(Path(path_text), shrink_guard=True)
        except Exception:
            # The canonical local write already succeeded.  A later boot/sync
            # will reconcile the file, so remote failure never affects V12.
            continue


def _notify_persistent_write(path: Path) -> None:
    """Create a local backup and queue optional GitHub persistence."""
    global _REMOTE_SYNC_THREAD
    try:
        from tino_persistent_store import _write_local_backup, inline_remote_sync_enabled  # type: ignore
        _write_local_backup(path)
        if not inline_remote_sync_enabled():
            return
    except Exception:
        return
    with _REMOTE_SYNC_LOCK:
        _REMOTE_SYNC_PENDING.add(str(path))
        if _REMOTE_SYNC_THREAD is None or not _REMOTE_SYNC_THREAD.is_alive():
            _REMOTE_SYNC_THREAD = threading.Thread(
                target=_research_remote_sync_worker,
                name="tino-v13-memory-sync",
                daemon=True,
            )
            _REMOTE_SYNC_THREAD.start()


def _durable_write_enabled() -> bool:
    return str(os.environ.get("TINO_V13_DURABLE_WRITE", "0") or "0").strip().lower() in _TRUE_VALUES


def _tail_lines(path: Path, limit: int, max_bytes: int = 4 * 1024 * 1024) -> List[str]:
    wanted = max(1, int(limit))
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            chunks: List[bytes] = []
            line_count = 0
            byte_count = 0
            while position > 0 and line_count <= wanted and byte_count < max_bytes:
                size = min(65536, position, max_bytes - byte_count)
                if size <= 0:
                    break
                position -= size
                handle.seek(position)
                chunk = handle.read(size)
                chunks.append(chunk)
                byte_count += len(chunk)
                line_count += chunk.count(b"\n")
            data = b"".join(reversed(chunks))
    except Exception:
        return []
    return [line for line in data.decode("utf-8", errors="replace").splitlines() if line.strip()][-wanted:]


def _signature(path: Path) -> Tuple[int, int]:
    try:
        stat = path.stat()
        return int(stat.st_mtime_ns), int(stat.st_size)
    except FileNotFoundError:
        return 0, 0
    except Exception:
        return -1, -1


def _read_recent_rows(path: Path, limit: int = 500) -> List[Dict[str, Any]]:
    wanted = max(1, int(limit))
    cache_key = (str(path), wanted)
    signature = _signature(path)
    cached = _ROWS_CACHE.get(cache_key)
    if cached is not None and (cached[0], cached[1]) == signature:
        return [dict(row) for row in cached[2]]

    rows: List[Dict[str, Any]] = []
    for line in _tail_lines(path, wanted):
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
        except Exception:
            continue
    _ROWS_CACHE[cache_key] = (signature[0], signature[1], rows)
    return [dict(row) for row in rows]


def _recent_ids(path: Path, id_key: str, limit: int = 3000) -> set[str]:
    cache_key = (str(path), id_key)
    signature = _signature(path)
    cached = _ID_CACHE.get(cache_key)
    if cached is not None and (cached[0], cached[1]) == signature:
        return cached[2]

    ids: set[str] = set()
    for row in _read_recent_rows(path, limit):
        value = row.get(id_key)
        if value:
            ids.add(str(value))
    _ID_CACHE[cache_key] = (signature[0], signature[1], ids)
    return ids


def record_exists(path: Path, id_key: str, record_id: str) -> bool:
    value = str(record_id or "").strip()
    return bool(value and value in _recent_ids(path, id_key))


def research_seed_exists(seed_id: str) -> bool:
    return record_exists(RESEARCH_SEED_LOG, "seed_id", seed_id)


def genome_prediction_exists(prediction_id: str) -> bool:
    return record_exists(GENOME_SNAPSHOT_LOG, "prediction_id", prediction_id)


def _append_once(path: Path, row: Dict[str, Any], id_key: str) -> Dict[str, Any]:
    record_id = str((row or {}).get(id_key) or "").strip()
    if not record_id:
        raise ValueError(f"{id_key} is required")

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    if record_exists(path, id_key, record_id):
        return {"status": "duplicate", id_key: record_id, "path": str(path)}

    payload = json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
    fd = os.open(str(path), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
        if _durable_write_enabled():
            os.fsync(fd)
    finally:
        os.close(fd)

    signature = _signature(path)
    cache_key = (str(path), id_key)
    ids = set(_ID_CACHE.get(cache_key, (0, 0, set()))[2])
    ids.add(record_id)
    if len(ids) > 4000:
        ids = set(list(ids)[-3000:])
    _ID_CACHE[cache_key] = (signature[0], signature[1], ids)

    for key in [key for key in _ROWS_CACHE if key[0] == str(path)]:
        _ROWS_CACHE.pop(key, None)
    _notify_persistent_write(path)
    return {"status": "written", id_key: record_id, "path": str(path)}


def append_research_seed(row: Dict[str, Any]) -> Dict[str, Any]:
    return _append_once(RESEARCH_SEED_LOG, row, "seed_id")


def append_genome_snapshot(row: Dict[str, Any]) -> Dict[str, Any]:
    result = _append_once(GENOME_SNAPSHOT_LOG, row, "snapshot_id")
    if str(result.get("status")) == "written":
        _update_recent_ticker_cache(row)
    return result


def append_detection_event(row: Dict[str, Any]) -> Dict[str, Any]:
    return _append_once(DETECTION_EVENT_LOG, row, "event_id")


def append_close_recheck_event(row: Dict[str, Any]) -> Dict[str, Any]:
    return _append_once(CLOSE_RECHECK_LOG, row, "event_id")


def append_macro_event(row: Dict[str, Any]) -> Dict[str, Any]:
    return _append_once(MACRO_EVENT_LOG, row, "event_id")


def load_recent_macro_events(limit: int = 200) -> List[Dict[str, Any]]:
    return _read_recent_rows(MACRO_EVENT_LOG, limit)


def load_recent_close_rechecks(limit: int = 200) -> List[Dict[str, Any]]:
    return _read_recent_rows(CLOSE_RECHECK_LOG, limit)


def load_close_recheck_state() -> Dict[str, Any]:
    try:
        value = json.loads(CLOSE_RECHECK_STATE.read_text(encoding="utf-8"))
        return dict(value) if isinstance(value, dict) else {}
    except Exception:
        return {}


def save_close_recheck_state(state: Dict[str, Any]) -> Dict[str, Any]:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    payload = dict(state or {})
    temp = CLOSE_RECHECK_STATE.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
        encoding="utf-8",
    )
    os.replace(str(temp), str(CLOSE_RECHECK_STATE))
    _notify_persistent_write(CLOSE_RECHECK_STATE)
    return {"status": "written", "path": str(CLOSE_RECHECK_STATE)}


def _ensure_recent_ticker_cache() -> None:
    global _RECENT_CACHE_SIGNATURE
    signature = _signature(GENOME_SNAPSHOT_LOG)
    if _RECENT_CACHE_SIGNATURE == signature:
        return
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in _read_recent_rows(GENOME_SNAPSHOT_LOG, 4000):
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        grouped.setdefault(ticker, []).append(row)
        grouped[ticker] = grouped[ticker][-4:]
    _RECENT_BY_TICKER.clear()
    _RECENT_BY_TICKER.update(grouped)
    _RECENT_CACHE_SIGNATURE = signature


def _update_recent_ticker_cache(row: Dict[str, Any]) -> None:
    global _RECENT_CACHE_SIGNATURE
    ticker = str((row or {}).get("ticker") or "").strip().upper()
    if ticker:
        _RECENT_BY_TICKER.setdefault(ticker, []).append(dict(row))
        _RECENT_BY_TICKER[ticker] = _RECENT_BY_TICKER[ticker][-4:]
    _RECENT_CACHE_SIGNATURE = _signature(GENOME_SNAPSHOT_LOG)


def get_recent_genome_snapshots(ticker: str, limit: int = 2) -> List[Dict[str, Any]]:
    _ensure_recent_ticker_cache()
    key = str(ticker or "").strip().upper()
    return [dict(row) for row in _RECENT_BY_TICKER.get(key, [])[-max(1, int(limit)):]]


def load_recent_genomes(limit: int = 500) -> List[Dict[str, Any]]:
    return _read_recent_rows(GENOME_SNAPSHOT_LOG, limit)


def load_recent_detections(limit: int = 500) -> List[Dict[str, Any]]:
    return _read_recent_rows(DETECTION_EVENT_LOG, limit)


def research_storage_status() -> Dict[str, Any]:
    def _one(path: Path) -> Dict[str, Any]:
        sig = _signature(path)
        return {"path": str(path), "exists": path.exists(), "size_bytes": sig[1] if sig[1] >= 0 else 0}
    status = {
        "research_dir": str(RESEARCH_DIR),
        "seed": _one(RESEARCH_SEED_LOG),
        "genome": _one(GENOME_SNAPSHOT_LOG),
        "detection": _one(DETECTION_EVENT_LOG),
        "close_recheck": _one(CLOSE_RECHECK_LOG),
        "close_recheck_state": _one(CLOSE_RECHECK_STATE),
        "macro_event": _one(MACRO_EVENT_LOG),
        "long_term_registered": True,
    }
    try:
        from tino_persistent_store import remote_status  # type: ignore
        remote = remote_status()
        status["remote"] = {
            "configured": bool(remote.get("configured")),
            "status": str(remote.get("status") or ""),
            "last_sync_at_tw": remote.get("last_sync_at_tw"),
            "last_verified_at_tw": remote.get("last_verified_at_tw"),
            "error": remote.get("error"),
        }
    except Exception:
        status["remote"] = {"configured": False, "status": "unavailable"}
    return status


def load_research_dashboard(genome_limit: int = 500, detection_limit: int = 500) -> Dict[str, Any]:
    genomes = load_recent_genomes(genome_limit)
    detections = load_recent_detections(detection_limit)
    close_rechecks = load_recent_close_rechecks(200)
    macro_events = load_recent_macro_events(200)
    latest_by_ticker: Dict[str, Dict[str, Any]] = {}
    for row in genomes:
        ticker = str(row.get("ticker") or "").strip().upper()
        if ticker:
            latest_by_ticker[ticker] = row
    return {
        "genomes": genomes,
        "detections": detections,
        "close_rechecks": close_rechecks,
        "macro_events": macro_events,
        "close_recheck_state": load_close_recheck_state(),
        "latest_by_ticker": latest_by_ticker,
        "storage": research_storage_status(),
    }
