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


def run_cycle(snapshot_dir, *, analyze=None, log=None, capture=None, settle=None, research=None, now=None):
    from memory_store import MEMORY_DIR, append_jsonl, read_jsonl

    now = now or datetime.now(timezone.utc)
    csv_path = result_csv(snapshot_dir)
    report_path = Path(MEMORY_DIR) / "paper_lab" / "scan_runs.jsonl"
    run_day = now.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Taipei")).date().isoformat()
    if any(r.get("run_day") == run_day and r.get("source")
           and (r.get("ai_status") == "completed" or r.get("shortlist") == 0)
           for r in read_jsonl(report_path, 100)):
        return {"status": "duplicate", "run_day": run_day}
    report = {"run_day": run_day, "run_at": now.isoformat(), "source": csv_path.name if csv_path else "",
              "status": "skipped", "shortlist": 0, "candidates": 0, "recorded": 0, "paper_buy": 0, "details": []}
    if csv_path is None:
        report["reason"] = "找不到完成的 Scanner 結果"
    elif datetime.fromtimestamp(csv_path.stat().st_mtime, timezone.utc).date() < now.date():
        report["reason"] = "Scanner 結果不是當日新鮮快照"
    else:
        with csv_path.open(encoding="utf-8-sig", newline="") as handle:
            candidates = select_candidates(csv.DictReader(handle))
        report["shortlist"] = len(candidates)
        if not candidates:
            report["reason"] = "本輪掃描沒有資料品質可供研究的候選"
        else:
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
            selected = {d["ticker"]: d for d in report["research_decisions"] if d.get("verdict") == "PAPER_REVIEW"}
            candidates = [r for r in candidates if r["ticker"] in selected and str(r.get("execution_status") or "").upper() == "ACTIONABLE"][:5]
            report["candidates"] = len(candidates)
            if not candidates:
                report["reason"] = "AI 研究未提名可執行標的" if report["ai_status"] == "completed" else "AI 研究模型尚未設定或暫時無法使用"
        if candidates and report["candidates"]:
            if analyze is None:
                from data_sources import fetch_news, fetch_price
                from orchestrator import orchestrate
                from learning import build_learning_signals, log_prediction
                from paper_lab_v1118 import capture_query
                analyze = lambda ticker: orchestrate(
                    fetch_price(ticker), "neutral", news_items=fetch_news(ticker),
                    extra_signals=build_learning_signals(ticker))
                log, capture = log_prediction, capture_query
            report["status"] = "completed"
            for candidate in candidates:
                symbol = candidate["ticker"]
                item = {"ticker": symbol, "scanner_score": candidate["score"], "research": selected[symbol]}
                try:
                    forecast = analyze(symbol)
                    if forecast is None or bool(getattr(forecast, "stopped", False)):
                        item["result"] = "資料品質未通過"
                    else:
                        row = log(forecast)
                        if not isinstance(row, dict) or row.get("skipped"):
                            item["result"] = "正式預測未通過"
                        else:
                            outcome = capture(row, forecast, origin="autonomous")
                            item["result"] = str(outcome.get("status") or "unknown")
                            item["formal_action"] = str((row.get("public_decision_snapshot") or {}).get("action_code") or "BLOCK")
                            if item["result"] in {"recorded", "duplicate"}:
                                report["recorded"] += 1
                            if outcome.get("experiment_status") == "PENDING" and item["result"] in {"recorded", "duplicate"}:
                                report["paper_buy"] += 1
                except Exception as exc:
                    item["result"] = "error"
                    item["reason"] = type(exc).__name__
                report["details"].append(item)
    # Reconcile prior decisions even when today's scanner recommends nobody.
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
    append_jsonl(report_path, report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-dir", required=True)
    args = parser.parse_args()
    from tino_persistent_store import ensure_memory_initialized_bootsafe, remote_status, _sync_file_to_remote
    from memory_store import MEMORY_DIR, PREDICTION_LOG, AUDIT_LOG
    if not remote_status().get("configured"):
        raise SystemExit("V1118 requires persistent memory before unattended paper research")
    ensure_memory_initialized_bootsafe()
    report = run_cycle(args.snapshot_dir)
    print(report)
    for path in (PREDICTION_LOG, AUDIT_LOG, Path(MEMORY_DIR)/"paper_lab"/"experiments.jsonl",
                 Path(MEMORY_DIR)/"paper_lab"/"outcomes.jsonl", Path(MEMORY_DIR)/"paper_lab"/"scan_runs.jsonl"):
        if path.exists():
            ok, error = _sync_file_to_remote(path, shrink_guard=True)
            if not ok:
                raise SystemExit(f"V1118 memory sync failed: {path.name}: {error}")
    if report["status"] == "skipped" and report.get("reason") == "找不到完成的 Scanner 結果":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
