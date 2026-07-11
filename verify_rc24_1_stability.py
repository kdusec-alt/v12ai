# -*- coding: utf-8 -*-
"""Fast local verification for RC24.1 Stable Observation package."""
from __future__ import annotations

import compileall
import os
import tempfile
from pathlib import Path

import auto_audit_scheduler as audit
import data_sources_market_heat as heat
import tino_persistent_store as store


def main() -> int:
    root = Path(__file__).resolve().parent
    assert compileall.compile_dir(str(root), quiet=1), "compileall failed"

    a = audit.maybe_run_auto_audit_time_guard()
    assert a.get("status") == "disabled_bootsafe" and a.get("execute") is False

    h = heat.fetch_tw_market_heat("2026-07-09")
    # RC25 uses one bounded synchronous Yahoo reader.  Network success is not
    # required for this stability test, but the source contract must remain.
    assert h.get("source") == "Yahoo股市資券餘額"

    calls = []

    def _remote_boom(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("remote network was called from boot-safe initializer")

    old_restore = store.remote_restore_memory_files
    old_sync_all = store.sync_all_memory_files_to_remote
    old_sync_one = store._sync_file_to_remote
    old_remote_config = store._remote_config
    try:
        store.remote_restore_memory_files = _remote_boom
        store.sync_all_memory_files_to_remote = _remote_boom
        store._sync_file_to_remote = _remote_boom
        store._remote_config = lambda: {
            "configured": True,
            "backend": "github",
            "repo": "test/repo",
            "branch": "main",
            "memory_dir": ".tino_memory",
        }
        store._BOOTSAFE_INIT_CACHE.clear()
        os.environ.pop("TINO_INLINE_REMOTE_SYNC", None)
        with tempfile.TemporaryDirectory() as d:
            report = store.ensure_memory_initialized_bootsafe(
                migrate=False,
                path=Path(d) / "ledger.json",
            )
            assert report.get("status") == "PASS", report
            assert report.get("remote_restore", {}).get("status") == "SKIPPED_BOOTSAFE"
            assert report.get("remote_sync", {}).get("status") == "SKIPPED_BOOTSAFE"
            assert calls == [], calls
    finally:
        store.remote_restore_memory_files = old_restore
        store.sync_all_memory_files_to_remote = old_sync_all
        store._sync_file_to_remote = old_sync_one
        store._remote_config = old_remote_config

    app_text = (root / "app.py").read_text(encoding="utf-8")
    assert "ensure_memory_initialized_bootsafe" in app_text
    assert "maybe_run_auto_audit_time_guard(" not in app_text

    watch_text = (root / "ui_watch_center.py").read_text(encoding="utf-8")
    assert 'TINO_WATCH_FRAGMENT", "0"' in watch_text
    assert 'value=False, key="watch_auto_refresh"' in watch_text

    print("PASS: RC24.1 boot-safe, no inline Auto Audit, bounded Market Heat reader, no boot remote I/O")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
