# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

_TRACE_PATH = Path(os.environ.get("TINO_RUNTIME_TRACE", "/tmp/tino_runtime_trace.jsonl"))
_TW = timezone(timedelta(hours=8))

def _rss_mb() -> float | None:
    try:
        import resource
        value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        # Linux reports KiB; macOS reports bytes.
        return round(value / (1024.0 if value < 10_000_000 else 1024.0 * 1024.0), 2)
    except Exception:
        return None

def mark_runtime_stage(stage: str, **meta: Any) -> None:
    """Best-effort local stage marker; never raises into the app."""
    try:
        _TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "time_tw": datetime.now(_TW).isoformat(timespec="seconds"),
            "pid": os.getpid(),
            "stage": str(stage),
            "rss_peak_mb": _rss_mb(),
            "threads": threading.active_count(),
        }
        row.update({str(k): v for k, v in meta.items()})
        payload = json.dumps(row, ensure_ascii=False, default=str)
        with _TRACE_PATH.open("a", encoding="utf-8") as f:
            f.write(payload + "\n")
            f.flush()
        # Community Cloud logs preserve the last completed stage even if the
        # process is later killed by a native/resource failure.
        print("[TINO_RUNTIME] " + payload, flush=True)
    except Exception:
        return
