"""Bounded autonomous discovery and paper-only decisions from a completed V156 scan.

The scheduled worker has no broker connector and never sends an order.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path


def result_csv(folder):
    paths = sorted(
        (p for p in Path(folder).glob("tino_market_scanner_v156_*.csv")
         if "_diagnostics_" not in p.name and "_tplus1_validation_" not in p.name),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    return paths[0] if paths else None


def select_candidates(rows, limit=12):
    """Build a diverse research shortlist; the AI and formal model may abstain."""
    approved = []
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker or ticker in {r["ticker"] for r in approved}:
            continue
        if str(row.get("execution_status") or "").strip().upper() not in {"ACTIONABLE", "WAIT_PULLBACK", "WAIT_RESET"}:
            continue
        try:
            score = float(row.get("recommendation_score") or 0)
        except (ValueError, TypeError):
            score = 0
        approved.append({**row, "ticker": ticker, "score": score})
    ranked = sorted(approved, key=lambda r: r["score"], reverse=True)
    # One issuer per sector in the first pass; then fill remaining places.
    shortlist, sectors = [], set()
    for row in ranked:
        sector = str(row.get("sector") or "").strip()
        if sector and sector not in sectors:
            shortlist.append(row); sectors.add(sector)
    shortlist.extend(r for r in ranked if r not in shortlist)
    return shortlist[:limit]


def run_cycle(snapshot_dir, *, analyze=None, log=None, capture=None, settle=None, research=None, now=None,
              scanner_status="success"):
    from memory_store import MEMORY_DIR, append_jsonl, read_jsonl

    now = now or datetime.now(timezone.utc)
    # A failed scanner may leave a partial CSV. Never let it enter paper research.
    csv_path = result_csv(snapshot_dir) if scanner_status == "success" else None
    report_path = Path(MEMORY_DIR) / "paper_lab" / "scan_runs.jsonl"
    run_day = now.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Taipei")).date().isoformat()
    if any(r.get("run_day") == run_day and r.get("source")
           and (r.get("ai_status") == "completed" or r.get("shortlist") == 0)
           for r in read_jsonl(report_path, 100)):
        return {"status": "duplicate", "run_day": run_day}
    report = {"run_day": run_day, "run_at": now.isoformat(), "source": csv_path.name if csv_path else "",
              "status": "skipped", "shortlist": 0, "candidates": 0, "recorded": 0, "paper_buy": 0, "details": []}
    if scanner_status != "success":
        report["reason"] = "Scanner 未完成；禁止使用可能不完整的快照"
        report["scanner_status"] = scanner_status
    elif csv_path is None:
        report["reason"] = "找不到完成的 Scanner 結果"
    elif datetime.fromtimestamp(csv_path.stat().st_mtime, timezone.utc).date() < now.date():
        report["reason"] = "Scanner 結果不是當日新鮮快照"
    else:
        with csv_path.open(encoding="utf-8-sig", newline="") as handle:
            snapshot_rows = list(csv.DictReader(handle))
        report["scan_items"] = snapshot_rows
        report["scanner_candidates"] = snapshot_rows
        report["scan_items_scope"] = "候選快照（含未獲推薦）"
        # Forecast the bounded candidate pool before applying permission to trade.
        research_pool = []
        for raw in snapshot_rows:
            ticker = str(raw.get("ticker") or "").strip().upper()
            if not ticker or ticker in {r["ticker"] for r in research_pool}:
                continue
            try:
                score = float(raw.get("recommendation_score") or 0)
            except (ValueError, TypeError):
                score = 0
            research_pool.append({**raw, "ticker": ticker, "score": score})
        research_pool = sorted(research_pool, key=lambda row: row["score"], reverse=True)[:12]
        candidates = select_candidates(snapshot_rows)
        report["shortlist"] = len(research_pool)
        report["forecast_details"] = []
        selected = {}
        allowed_codes = set()
        if candidates:
            if research is None:
                from ai_research_v1118 import research_candidates
                research = research_candidates
            try:
                research_result = research(candidates)
            except Exception as exc:
                research_result = {"status": "error", "reason": type(exc).__name__, "decisions": []}
            report["ai_status"] = research_result.get("status")
            report["market_view"] = research_result.get("market_view", "")
            report["research_decisions"] = research_result.get("decisions", [])
            selected = {d["ticker"]: d for d in report["research_decisions"] if d.get("ticker") and d.get("verdict") == "PAPER_REVIEW"}
            allowed_codes = {r["ticker"] for r in candidates if r["ticker"] in selected and str(r.get("execution_status") or "").upper() == "ACTIONABLE"}
            allowed_codes = set([r["ticker"] for r in candidates if r["ticker"] in allowed_codes][:5])
        report["candidates"] = len(allowed_codes)
        if not allowed_codes:
            report["reason"] = "未通過模擬買進條件；候選仍建立預測並等待收盤驗證"
        if research_pool:
            if analyze is None:
                from data_sources import fetch_news, fetch_price
                from orchestrator import orchestrate
                from learning import build_learning_signals, log_prediction
                from paper_lab_v1118 import capture_query
                analyze = lambda ticker: orchestrate(
                    fetch_price(ticker), "neutral", news_items=fetch_news(ticker),
                    extra_signals=build_learning_signals(ticker))
                log, capture = log_prediction, capture_query
            for candidate in research_pool:
                symbol = candidate["ticker"]
                permitted = symbol in allowed_codes
                item = {"ticker": symbol, "name": candidate.get("name"),
                        "scanner_score": candidate["score"], "scanner_status": candidate.get("execution_status"),
                        "research": selected.get(symbol, {}), "paper_trade_allowed": permitted}
                try:
                    forecast = analyze(symbol)
                    if forecast is None or bool(getattr(forecast, "stopped", False)):
                        item.update(result="資料品質未通過", reason=str(getattr(forecast, "stop_reason", "") or "無有效行情，無法建立預測"))
                    else:
                        row = log(forecast)
                        if not isinstance(row, dict) or row.get("skipped"):
                            item.update(result="正式預測未通過", reason=str((row or {}).get("reason") or "預測保存失敗"))
                        else:
                            entry_plan = (row.get("public_decision_snapshot") or {}).get("entry") or {}
                            zone = entry_plan.get("low_entry_zone") or {}
                            item.update(prediction_id=row.get("id"), observed_at=row.get("run_time_tw"),
                                target_trade_date=row.get("target_trade_date"),
                                anchor_price=row.get("anchor_close"), scanner_entry_price=candidate.get("entry"),
                                predicted_close=row.get("next_close_est"),
                                predicted_low=row.get("next_low_est"), predicted_high=row.get("next_high_est"),
                                entry_price=zone.get("lower") or row.get("entry_low_1"),
                                entry_price_upper=zone.get("upper") or row.get("entry_low_2"),
                                strategy_version="V1118_CANDIDATE_FORECAST_V2",
                                confirmation_price=row.get("public_confirmation_price"),
                                stop_price=row.get("public_invalidation_price") or row.get("defense_stop"),
                                formal_action=str((row.get("public_decision_snapshot") or {}).get("action_code") or "BLOCK"),
                                reason=row.get("public_action_reason") or "",
                                audit_status="等待目標交易日收盤")
                            # An AI/scanner veto permits observation, never a simulated position.
                            capture_row = dict(row)
                            capture_row["paper_trade_allowed"] = permitted
                            if not permitted:
                                capture_row["paper_gate_reason"] = "掃描或AI未允許進場，保留價格預測作空手觀察"
                            outcome = capture(capture_row, forecast, origin="autonomous")
                            item["result"] = str(outcome.get("status") or "unknown")
                            item["experiment_status"] = outcome.get("experiment_status")
                            if item["result"] in {"recorded", "duplicate"}:
                                report["recorded"] += 1
                            if permitted and outcome.get("experiment_status") == "PENDING" and item["result"] in {"recorded", "duplicate"}:
                                report["paper_buy"] += 1
                except Exception as exc:
                    item.update(result="error", reason=type(exc).__name__)
                report["details"].append(item)
                report["forecast_details"].append(item)
            report["status"] = "completed"
    # Preserve every inspected ticker independently of research eligibility.
    detail_path = Path(snapshot_dir) / "scan_universe_details.csv"
    if detail_path.exists() and datetime.fromtimestamp(detail_path.stat().st_mtime, timezone.utc).date() >= now.date():
        with detail_path.open(encoding="utf-8-sig", newline="") as handle:
            report["scan_items"] = list(csv.DictReader(handle))
        report["scan_items_scope"] = "全市場逐檔掃描紀錄（含不合格與失敗）"
    report["scan_items_count"] = len(report.get("scan_items") or [])
    # Reconcile prior decisions even when today's scanner recommends nobody.
    # A failed scan should not trigger extra quote requests during recovery.
    if scanner_status != "success":
        settle = lambda: {"status": "deferred", "reason": "scanner_failed"}
    try:
        if settle is None:
            from learning import auto_audit_queried_predictions
            from paper_lab_v1118 import reconcile_local
            def settle():
                auto_audit_queried_predictions(max_tickers=12, apply_safe_learning=False)
                return reconcile_local()
        report["settlement"] = settle()
    except Exception as exc:
        report["settlement"] = {"status": "deferred", "reason": type(exc).__name__}
    try:
        from paper_portfolio_v1118 import record_verified_trades
        report["portfolio"] = record_verified_trades()
    except Exception as exc:
        report["portfolio"] = {"status": "deferred", "reason": type(exc).__name__}
    append_jsonl(report_path, report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-dir", required=True)
    parser.add_argument("--scanner-status", choices=("success", "failure"), default="success")
    args = parser.parse_args()
    from tino_persistent_store import ensure_memory_initialized_bootsafe, remote_status, _sync_file_to_remote
    from memory_store import MEMORY_DIR, PREDICTION_LOG, AUDIT_LOG
    if not remote_status().get("configured"):
        raise SystemExit("V1118 requires persistent memory before unattended paper research")
    bootstrap = ensure_memory_initialized_bootsafe()
    restore_status = (bootstrap.get("remote_restore") or {}).get("status")
    if restore_status not in {"PASS", "EMPTY_REMOTE"}:
        raise SystemExit(f"V1118 remote memory restore incomplete: {restore_status}")
    sync_paths = (PREDICTION_LOG, AUDIT_LOG, Path(MEMORY_DIR)/"paper_lab"/"experiments.jsonl",
                  Path(MEMORY_DIR)/"paper_lab"/"outcomes.jsonl", Path(MEMORY_DIR)/"paper_lab"/"scan_runs.jsonl",
                  Path(MEMORY_DIR)/"paper_lab"/"portfolio_events.jsonl")
    before = {
        str(path): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sync_paths if path.exists()
    }
    report = run_cycle(args.snapshot_dir, scanner_status=args.scanner_status)
    print({key: value for key, value in report.items() if key != "scan_items"})
    for path in sync_paths:
        if path.exists():
            current = (path.stat().st_size, path.stat().st_mtime_ns)
            if before.get(str(path)) == current:
                continue
            ok, error = _sync_file_to_remote(path, shrink_guard=True)
            if not ok:
                raise SystemExit(f"V1118 memory sync failed: {path.name}: {error}")
    if report["status"] == "skipped" and report.get("reason") == "找不到完成的 Scanner 結果":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

