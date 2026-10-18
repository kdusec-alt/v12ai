"""Bounded, independent close audit for already-recorded predictions.

No scanner, new candidate discovery, broker connection or historical backfill.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path


def update_candidate_checks(market):
    """Project verified audits into the daily candidate ledger without rewriting forecasts."""
    from memory_store import MEMORY_DIR, read_jsonl, read_audit_log, append_jsonl
    from paper_lab_v1118 import before_target_open
    path = Path(MEMORY_DIR) / "paper_lab" / "scan_runs.jsonl"
    by_prediction = {}
    for row in read_audit_log(2000):
        if row.get("target") == "next" and row.get("actual_valid") is True and str(row.get("market") or "").upper() == market:
            by_prediction[row.get("prediction_id")] = row
    latest_runs = {}
    for row in read_jsonl(path, 100):
        key = (row.get("run_day"), row.get("run_at"))
        if key not in latest_runs or str(row.get("last_audit_at") or "") >= str(latest_runs[key].get("last_audit_at") or ""):
            latest_runs[key] = row
    changed = 0
    for report in latest_runs.values():
        dirty = False
        for item in report.get("forecast_details") or []:
            audit = by_prediction.get(item.get("prediction_id"))
            if not audit or str(audit.get("actual_price_date") or "")[:10] != str(item.get("target_trade_date") or "")[:10]:
                continue
            actual = audit.get("actual_close")
            predicted = item.get("predicted_close")
            if not isinstance(actual, (int, float)) or not isinstance(predicted, (int, float)) or actual <= 0 or predicted <= 0:
                continue
            status = "已驗證" if audit.get("price_sample_quality") == "verified" and before_target_open({"market": market, "target_trade_date": item.get("target_trade_date"), "run_time_tw": item.get("observed_at")}) else "已比對／未達正式計分門檻"
            fields = {"actual_close": actual, "close_error_pct": round((actual / predicted - 1) * 100, 4),
                      "audit_status": status, "actual_source": audit.get("actual_price_source")}
            if any(item.get(k) != v for k, v in fields.items()):
                item.update(fields)
                dirty = True
        if dirty:
            report["last_audit_at"] = datetime.now(timezone.utc).isoformat()
            append_jsonl(path, report)
            changed += 1
    return changed


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
    updated_candidate_runs = update_candidate_checks(market)
    errors = result.get("errors") or []
    report = {
        "market": market, "run_at": started.isoformat(), "updated_candidate_runs": updated_candidate_runs,
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
    bootstrap = ensure_memory_initialized_bootsafe()
    restore_status = (bootstrap.get("remote_restore") or {}).get("status")
    if restore_status not in {"PASS", "EMPTY_REMOTE"}:
        raise SystemExit(f"Audit remote memory restore incomplete: {restore_status}")
    report = run_close_audit(args.market)
    print({k: v for k, v in report.items() if k != "errors"})
    for path in (AUDIT_LOG, TICKER_PROFILE, LEARNING_WEIGHTS,
                 Path(MEMORY_DIR) / "paper_lab" / "scan_runs.jsonl",
                 Path(MEMORY_DIR) / "paper_lab" / "outcomes.jsonl",
                 Path(MEMORY_DIR) / "paper_lab" / "portfolio_events.jsonl",
                 Path(MEMORY_DIR) / "paper_lab" / "audit_runs.jsonl"):
        if path.exists():
            ok, error = _sync_file_to_remote(path, shrink_guard=True)
            if not ok:
                raise SystemExit(f"Audit memory sync failed: {path.name}: {error}")


if __name__ == "__main__":
    main()
