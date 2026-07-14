# -*- coding: utf-8 -*-
"""Fast append-only repository for V13 Research records.

Research artifacts can always be rebuilt from the formal V12 Prediction Log,
so the default write path favours low latency and skips per-record ``fsync``.
Set ``TINO_V13_DURABLE_WRITE=1`` only when forced disk flush is required.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from memory_store import MEMORY_DIR

RESEARCH_DIR = Path(MEMORY_DIR) / "v13_research"
RESEARCH_SEED_LOG = RESEARCH_DIR / "research_seed.jsonl"
GENOME_SNAPSHOT_LOG = RESEARCH_DIR / "genome_snapshot.jsonl"

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_ID_CACHE: Dict[Tuple[str, str], Tuple[int, int, set[str]]] = {}


def _durable_write_enabled() -> bool:
    return str(os.environ.get("TINO_V13_DURABLE_WRITE", "0") or "0").strip().lower() in _TRUE_VALUES


def _tail_lines(path: Path, limit: int, max_bytes: int = 4 * 1024 * 1024) -> List[str]:
    """Read only the bounded tail instead of scanning a multi-year JSONL file."""
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


def _recent_ids(path: Path, id_key: str, limit: int = 3000) -> set[str]:
    cache_key = (str(path), id_key)
    try:
        stat = path.stat()
        signature = (int(stat.st_mtime_ns), int(stat.st_size))
    except FileNotFoundError:
        _ID_CACHE[cache_key] = (0, 0, set())
        return set()
    except Exception:
        signature = (-1, -1)

    cached = _ID_CACHE.get(cache_key)
    if cached is not None and (cached[0], cached[1]) == signature:
        return cached[2]

    ids: set[str] = set()
    for line in _tail_lines(path, limit):
        try:
            row = json.loads(line)
            value = row.get(id_key) if isinstance(row, dict) else None
            if value:
                ids.add(str(value))
        except Exception:
            continue
    _ID_CACHE[cache_key] = (signature[0], signature[1], ids)
    return ids


def _append_once(path: Path, row: Dict[str, Any], id_key: str) -> Dict[str, Any]:
    record_id = str((row or {}).get(id_key) or "").strip()
    if not record_id:
        raise ValueError(f"{id_key} is required")

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    if record_id in _recent_ids(path, id_key):
        return {"status": "duplicate", id_key: record_id, "path": str(path)}

    payload = json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
    fd = os.open(str(path), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
        if _durable_write_enabled():
            os.fsync(fd)
    finally:
        os.close(fd)

    # Update the in-process cache immediately, avoiding another tail read on
    # Streamlit reruns. Cross-process changes are still detected by file stat.
    try:
        stat = path.stat()
        cache_key = (str(path), id_key)
        ids = set(_ID_CACHE.get(cache_key, (0, 0, set()))[2])
        ids.add(record_id)
        if len(ids) > 4000:
            ids = set(list(ids)[-3000:])
        _ID_CACHE[cache_key] = (int(stat.st_mtime_ns), int(stat.st_size), ids)
    except Exception:
        pass

    return {"status": "written", id_key: record_id, "path": str(path)}


def append_research_seed(row: Dict[str, Any]) -> Dict[str, Any]:
    return _append_once(RESEARCH_SEED_LOG, row, "seed_id")


def append_genome_snapshot(row: Dict[str, Any]) -> Dict[str, Any]:
    return _append_once(GENOME_SNAPSHOT_LOG, row, "snapshot_id")
