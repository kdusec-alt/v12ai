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


def before_target_open(prediction):
    p = _dict(prediction)
    try:
        market = str(p.get("market") or "").upper()
        zone = ZoneInfo("America/New_York" if market == "US" else "Asia/Taipei")
        opening = time(9, 30) if market == "US" else time(9, 0)
        observed = datetime.fromisoformat(str(p.get("observed_at") or p.get("run_time_tw") or ""))
        if observed.tzinfo is None:
            return False
        target = date.fromisoformat(str(p.get("target_trade_date") or ""))
        session = str(p.get("signal_date") or _dict(p.get("public_decision_snapshot")).get("session_date") or "")[:10]
        return observed.astimezone(zone) < datetime.combine(target, opening, tzinfo=zone) and session < target.isoformat()
    except (TypeError, ValueError):
        return False


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
    ready = (before_target_open(p) and snap.get("action_code") == "BUY" and stop is not None and target is not None
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
    if not before_target_open(exp):
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


def capture_query(prediction, forecast, origin="manual"):
    from memory_store import read_jsonl, append_jsonl
    frame = getattr(forecast, "price_frame", None)
    context = _dict(getattr(frame, "context", None))
    row = build_experiment(prediction, context.get("fundamental"), _research(_dict(prediction)))
    if row is None:
        return {"status": "skipped"}
    row["origin"] = "autonomous" if origin == "autonomous" else "manual"
    path, _ = _paths()
    with _LOCK:
        existing = next((r for r in read_jsonl(path, 2000) if r.get("experiment_id") == row["experiment_id"]), None)
        if existing:
            return {"status": "duplicate", "experiment_status": existing.get("status")}
        append_jsonl(path, row)
    return {"status": "recorded", "experiment_id": row["experiment_id"], "experiment_status": row["status"]}


def prediction_audit_scorecard(predictions, audits, symbol=None, as_of=None):
    """Read-only, one verified T+1 outcome per market/ticker/target session.

    Reference-limited or undated actuals stay outside the accuracy denominator.
    This never interprets forecast accuracy as simulated trading performance.
    """
    key = str(symbol or "").upper()
    def sample_id(row):
        if row.get("official_sample_key"):
            return str(row["official_sample_key"])
        return "|".join(str(row.get(field) or "") for field in ("market", "ticker", "target_trade_date"))

    predicted = {
        sample_id(row): row for row in predictions
        if isinstance(row, dict) and row.get("ticker") and row.get("target_trade_date")
        and (not key or str(row.get("ticker") or "").upper() == key)
    }
    by_sample = {}
    for row in audits:
        if not isinstance(row, dict) or row.get("target") != "next":
            continue
        if key and str(row.get("ticker") or "").upper() != key:
            continue
        target = str(row.get("target_trade_date") or "")[:10]
        if (row.get("actual_valid") is not True or not target
                or str(row.get("actual_price_date") or "")[:10] != target):
            continue
        if any(number(row.get(field)) is None or number(row.get(field)) <= 0
               for field in ("predicted_close", "actual_close", "anchor_close")):
            continue
        identity = sample_id(row)
        if identity not in by_sample or str(row.get("audit_time_tw") or "") > str(by_sample[identity].get("audit_time_tw") or ""):
            by_sample[identity] = row
    verified = [row for row in by_sample.values() if row.get("price_sample_quality") == "verified"]
    limited = len(by_sample) - len(verified)
    errors = [abs((float(r["actual_close"]) - float(r["predicted_close"])) / float(r["predicted_close"])) * 100 for r in verified]
    directions = [r["direction_hit"] for r in verified if isinstance(r.get("direction_hit"), bool)]
    ranges = [r["close_in_predicted_range"] for r in verified if isinstance(r.get("close_in_predicted_range"), bool)]
    recent = sorted(verified, key=lambda r: str(r.get("audit_time_tw") or ""), reverse=True)[:20]
    unresolved = [row for identity, row in predicted.items() if identity not in by_sample]
    clock = as_of or datetime.now(timezone.utc)
    waiting, overdue = 0, 0
    for row in unresolved:
        try:
            market_tz = ZoneInfo("America/New_York" if str(row.get("market") or "").upper() == "US" else "Asia/Taipei")
            current_day = clock.astimezone(market_tz).date()
            target_day = date.fromisoformat(str(row["target_trade_date"])[:10])
        except (ValueError, TypeError, KeyError):
            overdue += 1
            continue
        if target_day >= current_day:
            waiting += 1
        else:
            overdue += 1
    return {"samples": len(verified), "pending": len(unresolved), "waiting": waiting,
            "overdue": overdue, "limited": limited,
            "mae_pct": round(sum(errors) / len(errors), 2) if errors else None,
            "direction_pct": round(100 * sum(directions) / len(directions), 1) if directions else None,
            "direction_n": len(directions),
            "range_pct": round(100 * sum(ranges) / len(ranges), 1) if ranges else None,
            "range_n": len(ranges), "recent": recent}


def render_audit_scorecard(st, predictions, audits, symbol=None):
    result = prediction_audit_scorecard(predictions, audits, symbol)
    st.markdown("**預測稽核｜正式收盤後才計分**")
    cols = st.columns(4)
    cols[0].metric("有效 T+1 樣本", result["samples"])
    cols[1].metric("平均收盤誤差", f'{result["mae_pct"]:.2f}%' if result["mae_pct"] is not None else "樣本不足")
    cols[2].metric("三向判斷正確率", f'{result["direction_pct"]:.1f}%' if result["direction_pct"] is not None else "樣本不足")
    cols[3].metric("收盤落入預測區間", f'{result["range_pct"]:.1f}%' if result["range_pct"] is not None else "樣本不足")
    st.caption(f'方向樣本 {result["direction_n"]}｜區間樣本 {result["range_n"]}｜等待目標收盤 {result["waiting"]}｜逾期未取得有效收盤 {result["overdue"]}｜參考或來源未達正式門檻 {result["limited"]}。每個標的與目標交易日只計一次；預測準確度不是模擬交易報酬。')
    if result["overdue"]:
        st.warning(f'有 {result["overdue"]} 筆預測已過目標日，仍缺有效正式收盤；稽核覆蓋不足，暫勿將上述統計視為完整模型成績。')
    if result["recent"]:
        with st.expander("查看最近正式稽核"):
            st.dataframe([{"標的": r.get("ticker"), "目標日": r.get("target_trade_date"),
                           "預測收盤": r.get("predicted_close"), "正式收盤": r.get("actual_close"),
                           "絕對誤差%": round(abs(float(r["actual_close"])-float(r["predicted_close"])) / float(r["predicted_close"]) * 100, 2),
                           "方向正確": r.get("direction_hit"), "來源": r.get("actual_price_source")}
                          for r in result["recent"]], use_container_width=True, hide_index=True)


def render_autonomous_dashboard(st):
    """Public read-only ledger; does not run scans or access a broker."""
    from memory_store import MEMORY_DIR, read_jsonl, read_prediction_log, read_audit_log
    from paper_portfolio_v1118 import snapshot, set_capital, _path as portfolio_path
    root = Path(MEMORY_DIR) / "paper_lab"
    runs = read_jsonl(root / "scan_runs.jsonl", 100)
    seeds = [r for r in read_jsonl(root / "experiments.jsonl", 2000) if r.get("origin") == "autonomous"]
    outcomes = {r.get("experiment_id"): r for r in read_jsonl(root / "outcomes.jsonl", 4000)}
    st.markdown("## 🤖 V1118 自主模擬研究")
    st.markdown("每日市場掃描 → AI 比較論點與風險 → 正式模型否決／確認 → 模擬判斷 → 次日驗證。這裡只有研究紀錄，不連接券商下單。")
    render_audit_scorecard(st, read_prediction_log(2000), read_audit_log(2000))
    portfolio = snapshot()
    st.markdown("**虛擬資金池｜已驗證 T+1 模擬成交**")
    funds = st.columns(2)
    for col, currency, label in ((funds[0], "TWD", "台幣"), (funds[1], "USD", "美元")):
        col.metric(f"{label}模擬現金", f"{portfolio['cash'][currency]:,.2f}",
                   f"累計損益 {portfolio['cash'][currency]-portfolio['initial'][currency]:+,.2f}")
        col.caption(f"配置本金 {portfolio['initial'][currency]:,.2f} {currency}")
    if st.session_state.get("admin_authenticated", False):
        with st.expander("調整虛擬本金（管理員）"):
            with st.form("paper_capital_form"):
                twd = st.number_input("台幣配置本金", min_value=1.0, value=float(portfolio["initial"]["TWD"]), step=10000.0)
                usd = st.number_input("美元配置本金", min_value=1.0, value=float(portfolio["initial"]["USD"]), step=1000.0)
                submitted = st.form_submit_button("儲存資金設定")
            if submitted:
                try:
                    if float(twd) != portfolio["initial"]["TWD"]:
                        set_capital("TWD", twd)
                    if float(usd) != portfolio["initial"]["USD"]:
                        set_capital("USD", usd)
                    from tino_persistent_store import _sync_file_to_remote
                    ok, error = _sync_file_to_remote(portfolio_path(), shrink_guard=True)
                    if not ok:
                        st.error("本機已更新，但遠端同步失敗：" + str(error))
                    else:
                        st.success("資金配置已儲存；既有模擬交易不會重算。")
                except Exception as exc:
                    st.error("設定未完成：" + str(exc))
    if portfolio["trades"]:
        st.dataframe([{"日期": x.get("date"), "標的": x.get("ticker"), "幣別": x.get("currency"),
                       "股數": x.get("quantity"), "買進": x.get("entry_price"), "賣出": x.get("exit_price"),
                       "本筆淨損益": x.get("profit")} for x in portfolio["trades"][-30:]],
                     use_container_width=True, hide_index=True)
    st.caption("每筆最多使用該幣別配置本金 10%；只計入已驗證的自主 T+1 模擬成交。待驗證訊號不扣款。無匯率換算、不接券商。")
    if not runs:
        st.warning("尚未取得自主掃描完成紀錄，無法確認排程已產生可用快照；請檢查 Actions 掃描、雲端發布與紀錄同步。沒有紀錄不代表已模擬下單。")
        return
    latest = runs[-1]
    st.markdown(f"最近一輪：**{latest.get('run_day') or '日期未知'}**｜狀態 **{latest.get('status') or '未知'}**｜AI 研究 **{latest.get('ai_status') or '未執行'}**｜來源 {latest.get('source') or '無快照'}")
    cols = st.columns(3)
    cols[0].metric("AI 提名研究", latest.get("candidates", 0))
    cols[1].metric("本輪模擬買進訊號", latest.get("paper_buy", 0))
    cols[2].metric("累計已模擬出場", sum(1 for s in seeds if outcomes.get(s.get("experiment_id"), {}).get("status") == "CLOSED"))
    if latest.get("reason"):
        st.info(str(latest["reason"]))
    if latest.get("market_view"):
        st.markdown("**本輪市場看法**｜" + str(latest["market_view"]))
    decisions = latest.get("research_decisions") or []
    if decisions:
        st.markdown("**AI 候選比較與否決紀錄**")
        st.dataframe([{"標的": d.get("ticker"), "研究判斷": d.get("verdict"), "信心": d.get("confidence"), "投資論點": d.get("thesis"), "反方情境": d.get("bear_case"), "失效條件": d.get("risk_trigger"), "待補證據": d.get("missing_evidence")} for d in decisions], use_container_width=True, hide_index=True)
    details = latest.get("details") or []
    if details:
        st.markdown("**本輪自主選股與正式判斷**")
        st.dataframe([{"標的": r.get("ticker"), "掃描分數": r.get("scanner_score"), "正式判斷": r.get("formal_action", "未通過"), "結果": r.get("result"), "原因": r.get("reason", "")} for r in details], use_container_width=True, hide_index=True)
    if seeds:
        st.markdown("**自主模擬部位與驗證**")
        st.dataframe([{"標的": s.get("ticker"), "判斷時間": s.get("observed_at"), "目標交易日": s.get("target_trade_date"), "正式動作": s.get("formal_action"), "模擬狀態": outcomes.get(s.get("experiment_id"), {}).get("status") or s.get("status"), "進場價": outcomes.get(s.get("experiment_id"), {}).get("entry_price"), "出場價": outcomes.get(s.get("experiment_id"), {}).get("exit_price"), "淨報酬%": outcomes.get(s.get("experiment_id"), {}).get("net_return_pct")} for s in seeds[-50:]], use_container_width=True, hide_index=True)
    else:
        st.markdown("尚無自主模擬部位；本輪若沒有合格標的或正式 BUY，維持空手。")
    st.caption("手動查詢紀錄不計入上方自主模擬部位；結算需有目標交易日的有效正式 OHLC。")


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
    from memory_store import read_jsonl, read_prediction_log, read_audit_log
    from memory_store import MEMORY_DIR
    from tino_persistent_store import remote_status
    seeds_path, outcomes_path = _paths()
    seeds = [s for s in read_jsonl(seeds_path, 2000) if s.get("ticker") == symbol]
    outcomes = [r for r in read_jsonl(outcomes_path, 4000) if r.get("ticker") == symbol]
    runs = read_jsonl(Path(MEMORY_DIR) / "paper_lab" / "scan_runs.jsonl", 10)
    st.markdown("**自主尋標研究**")
    render_audit_scorecard(st, read_prediction_log(2000), read_audit_log(2000), symbol)
    if runs:
        latest = runs[-1]
        st.markdown(f"最近掃描：{latest.get('run_day', '日期未知')}｜AI 研究 {latest.get('ai_status', '未執行')}｜提名 {latest.get('candidates', 0)}｜正式判斷記錄 {latest.get('recorded', 0)}｜模擬 BUY {latest.get('paper_buy', 0)}")
        if latest.get("reason"):
            st.info(str(latest["reason"]))
        if latest.get("details"):
            st.dataframe([{"股票": x.get("ticker"), "掃描分數": x.get("scanner_score"), "正式動作": x.get("formal_action", "未通過"), "結果": x.get("result"), "原因": x.get("reason", "")} for x in latest["details"]], use_container_width=True, hide_index=True)
    else:
        st.info("自主尋標排程尚無完成紀錄。手動查詢的樣本與自主掃描分開計數。")
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
    st.markdown("研究只產生對照統計，未自動更改正式模型。Lab 須有同一筆預測的既存訊號；無訊號時保留缺值。自主掃描由獨立排程執行，實際候選、模擬與結算以上方完成紀錄為準。")
