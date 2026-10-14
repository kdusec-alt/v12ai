"""Bounded, independent close audit for already-recorded predictions.

No scanner, new candidate discovery, broker connection or historical backfill.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path


def run_close_audit(market, *, audit=None, reconcile=None, portfolio=None, now=None):
    from memory_store import MEMORY_DIR, append_jsonl

    market = str(market or "").upper()
    if market not in {"TW", "US"}:
        raise ValueError("market must be TW or US")
    if audit is None:
        from learning import auto_audit_queried_predictions
        audit = auto_audit_queried_predictions
    if reconcile is None:
        from paper_lab_v1118 import reconcile_local
        reconcile = reconcile_local
    if portfolio is None:
        from paper_portfolio_v1118 import record_verified_trades
        portfolio = record_verified_trades
    started = now or datetime.now(timezone.utc)
    result = audit(limit=2000, max_tickers=12, apply_safe_learning=True, market_filter=market)
    errors = result.get("errors") or []
    report = {
        "market": market, "run_at": started.isoformat(),
        "status": "audited" if result.get("audited_t1_count", 0) else "deferred" if errors else "no_target_samples",
        "fetched": result.get("fetched_ticker_count", 0),
        "audited_t1": result.get("audited_t1_count", 0),
        "audited_today": result.get("audited_today_count", 0),
        "errors": errors[:12],
        "weight_status": (result.get("v14_weight_learning") or {}).get("status"),
    }
    try:
        report["settlement"] = reconcile()
        report["portfolio"] = portfolio()
    except Exception as exc:
        report["settlement"] = {"status": "deferred", "reason": type(exc).__name__}
    append_jsonl(Path(MEMORY_DIR) / "paper_lab" / "audit_runs.jsonl", report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=("TW", "US"), required=True)
    args = parser.parse_args()
    from tino_persistent_store import ensure_memory_initialized_bootsafe, remote_status, _sync_file_to_remote
    from memory_store import MEMORY_DIR, AUDIT_LOG, TICKER_PROFILE, LEARNING_WEIGHTS
    if not remote_status().get("configured"):
        raise SystemExit("Scheduled audit requires persistent memory")
    ensure_memory_initialized_bootsafe()
    report = run_close_audit(args.market)
    print({k: v for k, v in report.items() if k != "errors"})
    for path in (AUDIT_LOG, TICKER_PROFILE, LEARNING_WEIGHTS,
                 Path(MEMORY_DIR) / "paper_lab" / "outcomes.jsonl",
                 Path(MEMORY_DIR) / "paper_lab" / "portfolio_events.jsonl",
                 Path(MEMORY_DIR) / "paper_lab" / "audit_runs.jsonl"):
        if path.exists():
            ok, error = _sync_file_to_remote(path, shrink_guard=True)
            if not ok:
                raise SystemExit(f"Audit memory sync failed: {path.name}: {error}")


if __name__ == "__main__":
    main()
