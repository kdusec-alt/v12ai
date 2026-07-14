# -*- coding: utf-8 -*-
"""Local append-only repository for V13 Research seeds.

This Phase-0 repository is deliberately separate from Prediction/Audit logs.
It performs no network I/O and has no dependency on the forecasting engine.
"""
from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
from typing import Any, Dict

from memory_store import MEMORY_DIR

RESEARCH_DIR = Path(MEMORY_DIR) / "v13_research"
RESEARCH_SEED_LOG = RESEARCH_DIR / "research_seed.jsonl"


def _recent_seed_ids(limit: int = 2000) -> set[str]:
    if not RESEARCH_SEED_LOG.exists():
        return set()
    lines: deque[str] = deque(maxlen=max(1, int(limit)))
    try:
        with RESEARCH_SEED_LOG.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.strip():
                    lines.append(line)
    except Exception:
        return set()

    ids: set[str] = set()
    for line in lines:
        try:
            row = json.loads(line)
            if isinstance(row, dict) and row.get("seed_id"):
                ids.add(str(row["seed_id"]))
        except Exception:
            continue
    return ids


def append_research_seed(row: Dict[str, Any]) -> Dict[str, Any]:
    seed_id = str((row or {}).get("seed_id") or "").strip()
    if not seed_id:
        raise ValueError("research seed_id is required")

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    if seed_id in _recent_seed_ids():
        return {"status": "duplicate", "seed_id": seed_id, "path": str(RESEARCH_SEED_LOG)}

    payload = json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
    fd = os.open(str(RESEARCH_SEED_LOG), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return {"status": "written", "seed_id": seed_id, "path": str(RESEARCH_SEED_LOG)}
