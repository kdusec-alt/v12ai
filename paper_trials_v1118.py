"""Prospective paper-only policy comparison; separate from the virtual portfolio."""
from collections.abc import Mapping
from datetime import date
from math import sqrt
from pathlib import Path
from threading import Lock
from time import monotonic

VERSION = "V1118_TRIALS_V1"
FILES = ("paper_lab/trial_orders.jsonl", "paper_lab/trial_results.jsonl")
POLICIES = (
    ("01", "原規則對照", "掃描合格＋AI 提名＋正式 BUY"),
    ("02", "只看正式 BUY", "移除 AI 與掃描資格"),
    ("03", "AI 提名", "保留原 AI 提名，允許正式 HOLD"),
    ("04", "掃描合格", "掃描 ACTIONABLE，允許正式 HOLD"),
    ("05", "候選含觀察", "掃描合格或等待回測／重設，允許 HOLD"),
    ("06", "正向預估", "隔日模型收盤高於基準價"),
    ("07", "正向且防守低", "模型預估至少 +0.5%，ABC 防守 ≤30%"),
    ("08", "回測主導", "ABC 回測 ≥45%，防守 ≤40%，預估 ≥−0.5%"),
    ("09", "量價確認", "站上 VWAP，ABC 防守 ≤40%，預估非負"),
    ("10", "寬鬆候選基準", "共通風控過關，含已驗證的隔日條件候選"),
)


def _map(value):
    return value if isinstance(value, Mapping) else {}


def build_trial(prediction, candidate, formal_permission):
    from paper_lab_v1118 import before_target_open, number
    p = prediction
    snap = _map(p.get("public_decision_snapshot"))
    market = p.get("market")
    day = str(p.get("target_trade_date") or "")
    session = str(snap.get("session_date") or "")[:10]
    if not p.get("id") or not p.get("ticker") or market not in {"TW", "US"}:
        return None
    row = {"id": f"{VERSION}:{market}:{p['ticker']}:{day}", "version": VERSION,
           "prediction_id": p["id"], "ticker": p["ticker"], "market": market,
           "asset_type": p.get("asset_type"), "target_trade_date": day,
           "signal_date": session, "observed_at": p.get("run_time_tw")}
    try:
        date.fromisoformat(session)
        date.fromisoformat(day)
    except ValueError:
        return None
    if not before_target_open(row):
        return None
    entry = _map(snap.get("entry"))
    gate = _map(_map(snap.get("reasoning")).get("cross_module_gate"))
    action = snap.get("action_code")
    next_plan = _map(entry.get("conditional_next_session"))
    stop, anchor, target, close = map(number, (entry.get("invalidation_price"),
        p.get("anchor_close"), p.get("next_high_est"), p.get("next_close_est")))
    edge = (close / anchor - 1) * 100 if close and anchor and anchor > 0 else None
    b, c = number(gate.get("abc_b")), number(gate.get("abc_c"))
    quality = p.get("price_sample_quality") == "verified" and not p.get("skipped")
    reference_candidate = (action == "BLOCK" and next_plan.get("eligible") is True
                           and p.get("trial_market_status") in {"closed_reference", "after_close", "closed"})
    if reference_candidate and (stop is None or anchor is None or not 0 < stop < anchor):
        stop = number(next_plan.get("invalidation_price"))
    common = (quality and bool(gate) and (action in {"BUY", "HOLD"} or reference_candidate)
              and snap.get("situation_code") not in {"HOLD_POSITION_UNKNOWN", "HOLD_OVERHEATED"}
              and (gate.get("code") != "DATA_BLOCK" or reference_candidate)
              and not gate.get("event_bearish_veto") and not gate.get("event_reaction_wait")
              and stop is not None and anchor is not None and target is not None
              and 0 < stop < anchor < target)
    scanner = str(candidate.get("execution_status") or "").upper()
    # The control mirrors the existing paper entry gate, independently of new shared filters.
    control = formal_permission and action == "BUY" and stop is not None and anchor is not None and target is not None and 0 < stop < anchor < target
    tests = (control, action == "BUY", formal_permission, scanner == "ACTIONABLE",
             scanner in {"ACTIONABLE", "WAIT_PULLBACK", "WAIT_RESET"},
             edge is not None and edge > 0,
             edge is not None and edge >= 0.5 and c is not None and c <= 30,
             edge is not None and edge >= -0.5 and b is not None and b >= 45 and c is not None and c <= 40,
             edge is not None and edge >= 0 and gate.get("price_above_vwap") is True and c is not None and c <= 40,
             True)
    row.update(stop=stop, target=target, anchor=anchor, formal_action=action, scanner_status=scanner,
               estimated_return_pct=edge, abc_b=b, abc_c=c,
               shared_gate=bool(common), orders=[pid for (pid, _, _), yes in zip(POLICIES, tests)
                                                  if yes and (pid == "01" or (common and (not reference_candidate or pid == "10")))],
               fee_bps_per_side=14.25 if market == "TW" else 0.0,
               sell_tax_bps=(10.0 if p.get("asset_type") == "etf" else 30.0) if market == "TW" else 0.0,
               slippage_bps_per_side=5.0, fee_note="固定示意成本，未含最低手續費")
    return row


def capture_trial(prediction, candidate, formal_permission, *, now=None):
    from datetime import datetime, timezone
    from memory_store import MEMORY_DIR, read_jsonl, append_jsonl
    from paper_lab_v1118 import before_target_open
    row = build_trial(prediction, candidate, formal_permission)
    if row is None:
        return None
    # Decision time is the actual policy evaluation, not the earlier prediction time.
    row["observed_at"] = (now or datetime.now(timezone.utc)).isoformat()
    if not before_target_open(row):
        return None
    path = Path(MEMORY_DIR) / FILES[0]
    if any(old.get("id") == row["id"] for old in read_jsonl(path, 2000)):
        return None
    append_jsonl(path, row)
    return row


def settle_trial(row, audit):
    from paper_lab_v1118 import settle_experiment
    if not row["orders"] or not audit or audit.get("market") != row["market"]:
        return None
    if audit.get("price_sample_quality") != "verified":
        # Preserve explicit rejection of reference-only samples in the denominator.
        if (audit.get("actual_valid") is True and audit.get("target") == "next"
            and audit.get("prediction_id") == row["prediction_id"] and audit.get("ticker") == row["ticker"]
            and audit.get("target_trade_date") == audit.get("actual_price_date") == row["target_trade_date"]):
            return {"id": row["id"], "status": "UNVERIFIED", "reason": "預測行情未達正式驗證門檻"}
        return None
    result = settle_experiment({**row, "experiment_id": row["id"], "status": "PENDING"}, audit)
    return {"id": row["id"], **result} if result else None


def reconcile_trials():
    from memory_store import MEMORY_DIR, read_jsonl, append_jsonl, read_audit_log
    orders, results = (Path(MEMORY_DIR) / name for name in FILES)
    known = {r["id"] for r in read_jsonl(results, 4000) if "id" in r}
    audits = {a.get("prediction_id"): a for a in read_audit_log(2000)
              if a.get("target") == "next" and a.get("actual_valid") is True}
    count = 0
    for row in read_jsonl(orders, 2000):
        if row.get("version") != VERSION or row["id"] in known:
            continue
        result = settle_trial(row, audits.get(row["prediction_id"]))
        if result:
            append_jsonl(results, result)
            known.add(row["id"])
            count += 1
    return count


def scorecard(rows, results):
    output = []
    for pid, label, rule in POLICIES:
        orders = [r for r in rows if pid in r["orders"]]
        settled = [results[r["id"]] for r in orders if r["id"] in results]
        returns = [r["net_return_pct"] for r in settled if r.get("status") == "CLOSED"]
        n = len(returns)
        wins = sum(r > 0 for r in returns)
        lo = hi = None
        if n:
            p = wins / n
            z = 1.96
            center = (p + z*z/(2*n))/(1+z*z/n)
            radius = z*sqrt((p*(1-p)+z*z/(4*n))/n)/(1+z*z/n)
            lo, hi = max(0, center-radius), min(1, center+radius)
        output.append({"方案": f"{pid} {label}", "觸發條件": rule, "候選樣本": len(rows),
                       "下單": len(orders), "下單率": f"{len(orders)/len(rows):.1%}" if rows else "—",
                       "成交": n, "跳空取消": sum(r.get("status") == "CANCELLED" for r in settled),
                       "無法驗證／排除": sum(r.get("status") in {"EXCLUDED", "UNVERIFIED"} for r in settled),
                       "待驗證": len(orders)-len(settled), "獲利筆數": wins,
                       "歷史勝率": f"{wins/n:.1%}" if n else "—",
                       "勝率區間（參考）": f"{lo:.0%}～{hi:.0%}" if n else "—",
                       "平均淨報酬": f"{sum(returns)/n:+.2f}%" if n else "—"})
    return output


_REFRESH_LOCK = Lock()
_REFRESH_STATE = {}


def refresh_trials(root, ttl=600):
    """Read only the two small trial logs; never sync from a page rerun."""
    from tino_persistent_store import _github_read_file, _reconcile_local_with_remote
    from os import environ
    if environ.get("TINO_OFFLINE_TEST") == "1":
        return True
    root = Path(root)
    with _REFRESH_LOCK:
        previous = _REFRESH_STATE.get(root)
        now = monotonic()
        if previous and now - previous[0] < ttl:
            return previous[1]
        ok = True
        for name in FILES:
            try:
                exists, blob, _, error = _github_read_file(name)
                if exists and blob is not None:
                    merged, _, _ = _reconcile_local_with_remote(root / name, blob)
                    ok = ok and merged
                elif error != "remote_missing":
                    ok = False
            except Exception:
                ok = False
        _REFRESH_STATE[root] = (monotonic(), ok)
        return ok


def render_trials(st):
    from memory_store import MEMORY_DIR, read_jsonl
    refreshed = refresh_trials(MEMORY_DIR)
    orders, results = (Path(MEMORY_DIR) / name for name in FILES)
    rows = [r for r in read_jsonl(orders, 2000) if r.get("version") == VERSION]
    outcomes = {r.get("id"): r for r in read_jsonl(results, 4000)}
    st.markdown("### 10 套虛擬下單方案｜並行試驗")
    if not refreshed:
        st.caption("暫時無法確認遠端最新實驗紀錄，顯示本機最後同步資料。")
    st.caption("同一候選可同時進多套方案；各套獨立統計，不佔原模擬帳戶資金。次日開盤模擬，跳空超出停損／目標取消；結果扣示意成本。樣本可能重疊，勝率區間僅供參考。")
    st.caption("統計範圍：最多最近 2,000 組候選；未取得驗證結果的單不納入勝率。")
    st.dataframe(scorecard(rows, outcomes), hide_index=True, use_container_width=True)
    if not rows:
        st.caption("新方案從下一次有效排程累積，不補造歷史單。")
        return
    with st.expander("查看策略虛擬單明細（最近 100 組）"):
        st.dataframe([{"股票": r["ticker"], "目標日": r["target_trade_date"],
                       "方案": "、".join(r["orders"]), "留存時間": r["observed_at"],
                       "結果": outcomes.get(r["id"], {}).get("status", "待驗證"),
                       "成交價": outcomes.get(r["id"], {}).get("entry_price"),
                       "出場價": outcomes.get(r["id"], {}).get("exit_price"),
                       "淨報酬 %": outcomes.get(r["id"], {}).get("net_return_pct")}
                      for r in rows[-100:][::-1] if r["orders"]], hide_index=True, use_container_width=True)
