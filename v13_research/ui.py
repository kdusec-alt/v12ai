# -*- coding: utf-8 -*-
"""Independent AI Research Lab UI.

The page reads only bounded V13 research logs.  It never fetches market data,
runs formal prediction, or recalculates Genome snapshots.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

from .genome_engine import GENE_LABELS, GENE_ORDER
from .repository import load_research_dashboard

_MUTATION_ZH = {
    "baseline": "基準建立",
    "stable": "穩定",
    "minor": "輕微突變",
    "major": "重大突變",
    "structural": "結構突變",
}
_STATUS_ZH = {
    "baseline": "建立基準",
    "stable": "穩定",
    "watch": "觀察中",
    "confirmed": "已確認",
    "degraded": "資料降級",
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _gene_score(snapshot: Mapping[str, Any], name: str) -> float | None:
    genes = snapshot.get("genes") if isinstance(snapshot.get("genes"), Mapping) else {}
    gene = genes.get(name) if isinstance(genes.get(name), Mapping) else {}
    try:
        return float(gene.get("score")) if gene.get("score") is not None else None
    except Exception:
        return None


def _latest_detection_for(ticker: str, detections: list[Dict[str, Any]]) -> Dict[str, Any]:
    key = str(ticker or "").upper()
    for row in reversed(detections):
        if str(row.get("ticker") or "").upper() == key:
            return row
    return {}


def _render_gene_panel(st, snapshot: Mapping[str, Any]) -> None:
    st.markdown("### 🧬 Bubble Genome / 泡沫基因體")
    left, right = st.columns(2, gap="large")
    for index, name in enumerate(GENE_ORDER):
        target = left if index % 2 == 0 else right
        with target:
            score = _gene_score(snapshot, name)
            label = GENE_LABELS.get(name, name)
            if score is None:
                st.caption(f"{label}｜資料不足")
                st.progress(0)
            else:
                st.caption(f"{label}｜{score:.1f}")
                st.progress(max(0, min(100, int(round(score)))))


def render_research_lab(st) -> None:
    st.markdown("## 🔬 AI Research Lab / 市場研究實驗室")
    st.caption("獨立研究平台｜只讀正式 Prediction Log 衍生資料｜不影響 AI Decision、Direction、T1 或 Confidence")

    dashboard = load_research_dashboard(genome_limit=600, detection_limit=600)
    genomes = list(dashboard.get("genomes") or [])
    detections = list(dashboard.get("detections") or [])
    latest_by_ticker = dict(dashboard.get("latest_by_ticker") or {})

    last_report = st.session_state.get("last_v13_research_report") or {}
    if str(last_report.get("status") or "") == "degraded":
        st.warning("V13 最近一次研究執行已安全降級；V12 正式分析未受影響。")

    critical = sum(1 for row in detections if str(row.get("mutation_level")) in {"major", "structural"})
    quality_alerts = sum(1 for row in detections if row.get("quality_flags"))
    avg_calc = (
        sum(_number(row.get("calc_ms")) for row in genomes) / len(genomes)
        if genomes else 0.0
    )
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Genome 快照", len(genomes))
    c2.metric("研究標的", len(latest_by_ticker))
    c3.metric("重大／結構突變", critical)
    c4.metric("資料品質警報", quality_alerts)
    c5.metric("Genome 平均耗時", f"{avg_calc:.3f} ms")

    if not latest_by_ticker:
        st.info("目前尚無 Genome 快照。請在個股分析完成一次正式 Prediction Log 後再回到本頁。")
        if last_report:
            with st.expander("最近一次 V13 執行狀態", expanded=False):
                st.json(last_report)
        return

    tickers = sorted(latest_by_ticker)
    default_ticker = str(st.session_state.get("research_ticker") or tickers[-1])
    if default_ticker not in latest_by_ticker:
        default_ticker = tickers[-1]
    selected = st.selectbox("研究標的", tickers, index=tickers.index(default_ticker), key="research_ticker")
    latest = latest_by_ticker[selected]
    latest_detection = _latest_detection_for(selected, detections)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Genome Score", f"{_number(latest.get('genome_score')):.1f}")
    m2.metric("Genome Confidence", f"{_number(latest.get('genome_confidence')) * 100:.1f}%")
    m3.metric("Coverage", f"{_number(latest.get('coverage')) * 100:.0f}%")
    mutation = str(latest_detection.get("mutation_level") or "baseline")
    status = str(latest_detection.get("status") or "baseline")
    m4.metric("Mutation", f"{_MUTATION_ZH.get(mutation, mutation)}｜{_STATUS_ZH.get(status, status)}")

    st.caption(
        f"Genome ID：{latest.get('genome_id', '')} ｜ Fingerprint：{latest.get('fingerprint', '')} "
        f"｜ 資料時間：{latest.get('run_time_tw', '')}"
    )

    overview_tab, detection_tab, history_tab, status_tab = st.tabs([
        "🧬 Genome", "🚨 Detection", "🕒 Evolution", "⚙️ Research Status"
    ])
    with overview_tab:
        _render_gene_panel(st, latest)
        dominant = list(latest.get("dominant_genes") or [])
        if dominant:
            st.success("顯性基因：" + "、".join(GENE_LABELS.get(name, name) for name in dominant))
        else:
            st.info("目前尚未形成明確顯性基因。")

    with detection_tab:
        if not latest_detection:
            st.info("此標的尚無 Detection 記錄。")
        else:
            flags = list(latest_detection.get("quality_flags") or [])
            changed = list(latest_detection.get("changed_genes") or [])
            st.write(
                f"狀態：**{_STATUS_ZH.get(status, status)}** ｜ "
                f"層級：**{_MUTATION_ZH.get(mutation, mutation)}** ｜ "
                f"確認：**{'是' if latest_detection.get('confirmed') else '否'}**"
            )
            if changed:
                st.warning("變化基因：" + "、".join(GENE_LABELS.get(name, name) for name in changed))
            if flags:
                st.error("資料品質旗標：" + "、".join(flags))
            deltas = latest_detection.get("gene_deltas") if isinstance(latest_detection.get("gene_deltas"), Mapping) else {}
            if deltas:
                rows = [
                    {"gene": GENE_LABELS.get(name, name), "delta": value}
                    for name, value in sorted(deltas.items(), key=lambda item: abs(_number(item[1])), reverse=True)
                ]
                st.dataframe(rows, use_container_width=True, hide_index=True)

    with history_tab:
        history = [row for row in genomes if str(row.get("ticker") or "").upper() == selected][-30:]
        rows = [
            {
                "run_time_tw": row.get("run_time_tw"),
                "genome_score": row.get("genome_score"),
                "confidence": round(_number(row.get("genome_confidence")) * 100, 1),
                "coverage": round(_number(row.get("coverage")) * 100, 1),
                "fingerprint": row.get("fingerprint"),
            }
            for row in reversed(history)
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)
        if len(history) < 2:
            st.caption("累積至少兩個不同日期／正式預測快照後，Mutation 與 Evolution 才會更有研究意義。")

    with status_tab:
        report = dict(last_report) if isinstance(last_report, Mapping) else {}
        close_report = st.session_state.get("last_close_recheck_report") or {}
        if not isinstance(close_report, Mapping) or not close_report:
            close_state = dashboard.get("close_recheck_state") or {}
            close_report = dict(close_state) if isinstance(close_state, Mapping) else {}
        if close_report:
            st.markdown("**台股收盤自動重檢**")
            q1, q2, q3, q4, q5 = st.columns(5)
            q1.metric("狀態", str(close_report.get("status") or "已記錄"))
            q2.metric("正式寫入", int(_number(close_report.get("formal_written"))))
            q3.metric("情境更新", int(_number(close_report.get("context_updated"))))
            q4.metric("無變化", int(_number(close_report.get("unchanged"))))
            pending_text = (
                f"待資料 {int(_number(close_report.get('waiting_institution')))} / "
                f"錯誤 {int(_number(close_report.get('errors')))}"
            )
            q5.metric("待處理", pending_text)
            st.caption(
                f"資料日：{close_report.get('trade_date', '')}｜"
                f"今日已查：{int(_number(close_report.get('today_tickers')))}｜"
                f"剩餘：{int(_number(close_report.get('remaining')))}｜"
                "只重檢今日已查台股；正式預測有變才新增樣本，僅籌碼情境改變則只存研究紀錄；不影響 V12 Decision。"
            )

        r1, r2, r3 = st.columns(3)
        r1.metric("最近 Scheduler 狀態", str(report.get("status") or "尚無本次工作階段資料"))
        r2.metric("最近總耗時", f"{_number(report.get('total_ms')):.3f} ms")
        r3.metric("Decision Influence", "FALSE")
        with st.expander("Storage Status", expanded=False):
            st.json(dashboard.get("storage") or {})
        if report:
            with st.expander("最近一次 Scheduler Report", expanded=False):
                st.json(report)
