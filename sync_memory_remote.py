# -*- coding: utf-8 -*-
"""External-only TINO memory restore/sync entrypoint.

Do not import or call this script from Streamlit app.py.  Run manually, by cron,
GitHub Action, Replit scheduled job, or another isolated worker.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from memory_store import MEMORY_DIR
from tino_persistent_store import ensure_memory_initialized

TW_TZ = timezone(timedelta(hours=8))
LOCK_PATH = Path(MEMORY_DIR) / "memory_remote_sync.lock"
STATUS_PATH = Path(MEMORY_DIR) / "memory_remote_sync_status.json"


def _write_status(payload: dict) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, STATUS_PATH)


def main() -> int:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("ascii", "ignore"))
        os.close(fd)
    except FileExistsError:
        payload = {
            "status": "LOCKED",
            "at_tw": datetime.now(TW_TZ).isoformat(timespec="seconds"),
            "reason": "another memory remote sync is already running",
        }
        _write_status(payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 2

    try:
        report = ensure_memory_initialized(migrate=True, allow_remote=True)
        payload = {
            "status": report.get("status", "UNKNOWN"),
            "at_tw": datetime.now(TW_TZ).isoformat(timespec="seconds"),
            "report": report,
        }
        _write_status(payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0 if report.get("status") in ("PASS", "LOCAL_ONLY") else 1
    except Exception as exc:
        payload = {
            "status": "FAIL",
            "at_tw": datetime.now(TW_TZ).isoformat(timespec="seconds"),
            "error": f"{type(exc).__name__}: {exc}",
        }
        _write_status(payload)
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        try:
            LOCK_PATH.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
