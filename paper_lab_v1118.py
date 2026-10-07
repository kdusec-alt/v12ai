"""Forward-only T+1 unit paper experiments, isolated from formal decision weights.
No broker API, extra quote downloads or historical signal reconstruction.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timezone
import hashlib
import math
from pathlib import Path
import threading
from zoneinfo import ZoneInfo

VERSION = "V1118_T1_UNIT_OPEN_V1"
_LOCK = threading.RLock()


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _dict(value):
    return value if isinstance(value, dict) else {}


def _research(prediction):
    """Freeze an existing phenotype for this exact prediction; never borrow others."""
    try:
        from v13_research.repository import get_shadow_phenotype_for_prediction
        row = get_shadow_phenotype_for_prediction(str(prediction.get("id") or ""), limit=300)
        if row and str(row.get("run_time_tw") or "") == str(prediction.get("run_time_tw") or ""):
            return {k: row.get(k) for k in ("phenotype_id", "selected_candidate_id", "shadow_direction", "shadow_bias", "run_time_tw")}
    except Exception:
        pass
    return {}


def build_experiment(prediction, fundamental=None, research=None):
    """Consume the logged formal BUY only; a displayed conditional zone is not BUY."""
    p = _dict(prediction)
    if not p.get("id") or not p.get("target_trade_date") or p.get("skipped"):
        return None
    snap = _dict(p.get("public_decision_snapshot"))
    plan = _dict(snap.get("entry"))
    stop = number(plan.get("invalidation_price"))
    target = number(p.get("next_high_est"))
    price = number(p.get("anchor_close"))
    ready = (snap.get("action_code") == "BUY" and stop is not None and target is not None
             and price is not None and 0 < stop < price < target)
    identity = "|".join((VERSION, str(p.get("market")), str(p.get("ticker")), str(p.get("target_trade_date"))))
    # First observation per symbol/session: repeated searches cannot multiply trials.
    exp_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
    f = _dict(fundamental)
    observed = str(p.get("run_time_tw") or "")
    recorded_date = observed[:10]
    fdate = str(f.get("date") or "")[:10]
    finance_valid = (p.get("asset_type") != "etf" and f.get("accepted") is True
                     and bool(fdate) and bool(recorded_date) and fdate <= recorded_date)
    finance = {k: f.get(k) for k in ("source", "date", "quarter", "quarterly_source_date", "revenue", "revenue_kind", "qoq", "qoq_verified", "yoy", "yoy_verified", "eps", "eps_kind", "eps_basis", "gaap_eps", "adjusted_eps", "eps_yoy", "eps_yoy_verified", "earnings_yoy_for_decision", "next_earnings")} if finance_valid else {}
    r = _dict(research)
    # A later Research Lab record must never enter a previously frozen decision.
    if str(r.get("run_time_tw") or "") != observed:
        r = {}
    return {"experiment_id": exp_id, "version": VERSION, "prediction_id": p["id"],
            "ticker": p.get("ticker"), "market": p.get("market"), "asset_type": p.get("asset_type"),
            "target_trade_date": str(p["target_trade_date"])[:10], "observed_at": observed,
            "signal_date": str(snap.get("session_date") or recorded_date)[:10],
            "formal_action": snap.get("action_code") or "BLOCK", "reason": snap.get("reason"),
            "status": "PENDING" if ready else "OBSERVE", "stop": stop, "target": target,
            "quantity": 1, "research": r, "fundamental": finance,
            "policy": "正式BUY後下一交易日開盤模擬一股；停損優先；未出場則T1收盤；非完整資金組合回測",
            "fee_bps_per_side": 14.25 if p.get("market") == "TW" else 0.0,
            "sell_tax_bps": (10.0 if p.get("asset_type") == "etf" else 30.0) if p.get("market") == "TW" else 0.0,
            "slippage_bps_per_side": 5.0,
            "fee_note": "示意百分比費率；未含券商最低手續費／最低交易費；一股為標準化研究單位"}


def settle_experiment(exp, audit):
    """Exact verified target OHLC only; no current bars or earlier-session fills."""
    a = _dict(audit)
    if (a.get("target") != "next" or a.get("actual_valid") is not True
        or a.get("prediction_id") != exp.get("prediction_id")
        or a.get("ticker") != exp.get("ticker")
        or str(a.get("target_trade_date") or "")[:10] != exp.get("target_trade_date")
        or str(a.get("actual_price_date") or "")[:10] != exp.get("target_trade_date")):
        return None
    # Compare in the exchange's timezone. A US premarket decision at 14:36
    # Taiwan time can still precede the 09:30 New York opening on the same date.
    try:
        market = str(exp.get("market") or "").upper()
        zone = ZoneInfo("America/New_York" if market == "US" else "Asia/Taipei")
        opening = time(9, 30) if market == "US" else time(9, 0)
        observed = datetime.fromisoformat(str(exp.get("observed_at") or ""))
        if observed.tzinfo is None:
            raise ValueError("missing observation timezone")
        target = date.fromisoformat(str(exp.get("target_trade_date") or ""))
        target_open = datetime.combine(target, opening, tzinfo=zone)
        in_time = observed.astimezone(zone) < target_open and str(exp.get("signal_date") or "")[:10] < target.isoformat()
    except (TypeError, ValueError):
        in_time = False
    if not in_time:
        return {"experiment_id": exp["experiment_id"], "status": "EXCLUDED", "reason": "判斷未早於目標市場開盤，或缺少可驗證時區"}
    o, h, l, c = [number(a.get("actual_" + k)) for k in ("open", "high", "low", "close")]
    if any(v is None or v <= 0 for v in (o, h, l, c)) or not l <= min(o, c) <= max(o, c) <= h:
        return None
    base = {"experiment_id": exp["experiment_id"], "prediction_id": exp["prediction_id"],
            "ticker": exp["ticker"], "date": exp["target_trade_date"], "actual_source": a.get("actual_price_source"),
            "underlying_return_pct": round((c / o - 1) * 100, 4), "fundamental": exp.get("fundamental", {}),
            "research": exp.get("research", {}), "settled_at": datetime.now(timezone.utc).isoformat()}
    if exp["status"] != "PENDING":
        return {**base, "status": "OBSERVED", "reason": "正式決策未買進，保留空手結果"}
    stop, target = exp["stop"], exp["target"]
    if not stop < o < target:
        return {**base, "status": "CANCELLED", "reason": "開盤跳空超出原停損／目標價格階層"}
    if exp.get("market") == "TW" and o == h == l == c:
        return {**base, "status": "UNVERIFIED", "reason": "單一價格日無法確認成交流動性"}
    # Market-on-open entry precedes daily extremes. Both hit -> conservative stop.
    raw_exit = stop if l <= stop else target if h >= target else c
    why = "STOP" if l <= stop else "TARGET" if h >= target else "T1_CLOSE"
    slip = exp["slippage_bps_per_side"] / 10000
    buy, sell = o * (1 + slip), raw_exit * (1 - slip)
    fees = (buy + sell) * exp["fee_bps_per_side"] / 10000 + sell * exp["sell_tax_bps"] / 10000
    return {**base, "status": "CLOSED", "entry_price": round(buy, 6), "exit_price": round(sell, 6),
            "exit_reason": why, "both_levels_hit": bool(l <= stop and h >= target),
            "net_return_pct": round((sell - buy - fees) / buy * 100, 4),
            "fees_unit": round(fees, 6), "fee_note": exp["fee_note"]}


def _paths():
    from memory_store import MEMORY_DIR
    root = Path(MEMORY_DIR) / "paper_lab"
    return root / "experiments.jsonl", root / "outcomes.jsonl"


def capture_query(prediction, forecast):
    from memory_store import read_jsonl, append_jsonl
    frame = getattr(forecast, "price_frame", None)
    context = _dict(getattr(frame, "context", None))
    row = build_experiment(prediction, context.get("fundamental"), _research(_dict(prediction)))
    if row is None:
        return {"status": "skipped"}
    path, _ = _paths()
    with _LOCK:
        if any(r.get("experiment_id") == row["experiment_id"] for r in read_jsonl(path, 2000)):
            return {"status": "duplicate"}
        append_jsonl(path, row)
    return {"status": "recorded", "experiment_id": row["experiment_id"]}


def reconcile_local():
    """Use already-confirmed local audits; bounded reads, zero quote requests."""
    from memory_store import read_jsonl, read_audit_log, append_jsonl
    seeds_path, outcomes_path = _paths()
    with _LOCK:
        seeds = read_jsonl(seeds_path, 2000)
        known = {r.get("experiment_id") for r in read_jsonl(outcomes_path, 4000)}
        audits = {a.get("prediction_id"): a for a in read_audit_log(2000)
                  if a.get("target") == "next" and a.get("actual_valid") is True}
        count = 0
        for exp in seeds:
            if exp.get("experiment_id") in known:
                continue
            result = settle_experiment(exp, audits.get(exp.get("prediction_id")))
            if result:
                append_jsonl(outcomes_path, result)
                known.add(exp["experiment_id"])
                count += 1
        return {"settled": count}


def learning_summary(rows):
    """Descriptive financial groups and frozen lab agreement; never update weights."""
    groups = defaultdict(list)
    for row in rows:
        if row.get("status") not in {"CLOSED", "OBSERVED", "CANCELLED", "UNVERIFIED"}:
            continue
        f = _dict(row.get("fundamental"))
        yoy = number(f.get("yoy"))
        if f.get("yoy_verified") is True and yoy is not None:
            group = "營收年增正成長" if yoy > 0 else "營收年增非正成長"
            groups[group].append(row)
        research = _dict(row.get("research"))
        if row.get("status") == "CLOSED" and research.get("shadow_direction") in {"UP", "DOWN", "NEUTRAL"}:
            groups["Lab " + research["shadow_direction"] + "／正式買進"].append(row)
    output = []
    for name, samples in groups.items():
        returns = [r["underlying_return_pct"] for r in samples if number(r.get("underlying_return_pct")) is not None]
        trades = [r["net_return_pct"] for r in samples if r.get("status") == "CLOSED"]
        output.append({"分類": name, "樣本": len(returns), "隔日開收均報酬%": round(sum(returns)/len(returns), 3) if returns else None,
                       "模擬成交": len(trades), "模擬淨報酬均值%": round(sum(trades)/len(trades), 3) if trades else None,
                       "研究狀態": "可提出研究建議，需另行驗證" if len(returns) >= 20 else "樣本不足20筆"})
    return output


def render_paper_lab(st, symbol):
    from memory_store import read_jsonl
    from tino_persistent_store import remote_status
    seeds_path, outcomes_path = _paths()
    seeds = [s for s in read_jsonl(seeds_path, 2000) if s.get("ticker") == symbol]
    outcomes = [r for r in read_jsonl(outcomes_path, 4000) if r.get("ticker") == symbol]
    st.markdown("前瞻 T+1 單位模擬：正式 BUY 後於下一交易日開盤模擬一股；含示意費率與滑價。空手、取消與虧損都保留。")
    remote = remote_status()
    st.markdown("紀錄保存：" + ("已設定遠端備份，實際同步依系統狀態" if remote.get("configured") else "目前為本機紀錄，尚未確認遠端持久備份"))
    capture = _dict(st.session_state.get("last_paper_capture"))
    if capture:
        status_label = {"recorded": "已記錄", "duplicate": "已有相同市場日紀錄", "prediction_skipped": "正式預測未通過", "skipped": "未建立樣本", "error": "保存失敗", "not_eligible": "未通過正式樣本門檻"}.get(capture.get("status"), "待確認")
        st.markdown("本次保存狀態：**" + status_label + "**" + ("｜" + str(capture.get("reason")) if capture.get("reason") else ""))
    closed = [r for r in outcomes if r.get("status") == "CLOSED"]
    cols = st.columns(3)
    cols[0].metric("已記錄判斷", len(seeds))
    cols[1].metric("完成模擬", len(closed))
    cols[2].metric("平均淨報酬", f'{sum(r["net_return_pct"] for r in closed)/len(closed):+.2f}%' if closed else "待驗證")
    labels = {"PENDING": "等待下一交易日", "OBSERVE": "空手觀察", "OBSERVED": "空手已驗證", "CLOSED": "已模擬出場", "CANCELLED": "跳空取消", "UNVERIFIED": "成交無法驗證", "EXCLUDED": "時點不符已排除"}
    if not seeds:
        st.info("尚無前瞻樣本。下一次個股分析且預測紀錄已啟用時，自動保存；不回填舊訊號。")
    else:
        completed = {r["experiment_id"]: r for r in outcomes}
        if not closed:
            st.info("已保存判斷，尚無已驗證的正式 BUY 模擬出場。空手判斷只記錄觀察結果，不會產生交易報酬。")
        st.dataframe([{"觀測日期": s["observed_at"], "目標交易日": s["target_trade_date"], "正式判斷": s["formal_action"],
                       "模擬狀態": labels.get(completed.get(s["experiment_id"], s)["status"], "待確認"),
                       "成交價": completed.get(s["experiment_id"], {}).get("entry_price"),
                       "出場價": completed.get(s["experiment_id"], {}).get("exit_price"),
                       "淨報酬%": completed.get(s["experiment_id"], {}).get("net_return_pct"),
                       "Lab": _dict(s.get("research")).get("shadow_direction") or "未產生",
                       "財報來源": _dict(s.get("fundamental")).get("source") or "未驗證"} for s in seeds[-50:]], use_container_width=True, hide_index=True)
    st.markdown("**財報與 Research Lab 學習**")
    summary = learning_summary(outcomes)
    if summary:
        st.dataframe(summary, use_container_width=True, hide_index=True)
    else:
        st.markdown("等待已確認收盤結果與有效財報／Lab 樣本，尚無可用統計。")
    st.markdown("研究只產生對照統計，未自動更改正式模型。Lab 須有同一筆預測的既存訊號；無訊號時保留缺值。現階段依查詢與既有審計更新，網頁關閉時尚無獨立常駐模擬排程。")
