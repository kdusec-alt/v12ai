"""Evidence-bounded LLM research nomination. It never places an order."""
from __future__ import annotations

import json
import os


MODEL = os.environ.get("TINO_AI_RESEARCH_MODEL", "gpt-5-mini")
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "market_view": {"type": "string"},
        "decisions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "ticker": {"type": "string"},
                "verdict": {"type": "string", "enum": ["PAPER_REVIEW", "WATCH", "REJECT"]},
                "confidence": {"type": "integer"},
                "thesis": {"type": "string"},
                "bear_case": {"type": "string"},
                "risk_trigger": {"type": "string"},
                "missing_evidence": {"type": "string"},
            }, "required": ["ticker", "verdict", "confidence", "thesis", "bear_case",
                             "risk_trigger", "missing_evidence"],
        }},
    }, "required": ["market_view", "decisions"],
}

FIELDS = ("ticker", "name", "sector", "tag", "execution_status", "recommendation_score",
          "data_quality", "setup_edge_pct", "market_edge_pct", "trade_n", "win_rate",
          "avg_net_ret", "reward_risk", "entry", "stop_loss", "data_date",
          "rs_sector_5d_pct", "inst_flow_status", "v157_total_score")


def _output_text(payload):
    for block in payload.get("output") or []:
        if block.get("type") == "message":
            for content in block.get("content") or []:
                if content.get("type") == "output_text":
                    return str(content.get("text") or "")
    return ""


def validate_research(result, candidates):
    allowed = {str(row.get("ticker") or "").upper(): row for row in candidates}
    if not isinstance(result, dict) or not isinstance(result.get("decisions"), list):
        raise ValueError("invalid research response")
    decisions, seen = [], set()
    for item in result["decisions"]:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker") or "").upper().strip()
        if ticker not in allowed or ticker in seen:
            continue
        seen.add(ticker)
        verdict = item.get("verdict")
        try:
            confidence = int(item.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0
        if verdict not in {"PAPER_REVIEW", "WATCH", "REJECT"} or not 0 <= confidence <= 100:
            continue
        evidence = {k: str(item.get(k) or "").strip()[:500] for k in ("thesis", "bear_case", "risk_trigger", "missing_evidence")}
        if verdict == "PAPER_REVIEW" and (confidence < 70 or not all(evidence[k] for k in ("thesis", "bear_case", "risk_trigger"))):
            verdict = "WATCH"
        if verdict == "PAPER_REVIEW" and str(allowed[ticker].get("execution_status") or "").upper() != "ACTIONABLE":
            verdict = "WATCH"
        try:
            quality = float(allowed[ticker].get("data_quality") or 0)
        except (TypeError, ValueError):
            quality = 0
        if verdict == "PAPER_REVIEW" and quality < 70:
            verdict = "WATCH"
        decisions.append({"ticker": ticker, "verdict": verdict, "confidence": confidence, **evidence})
    return {"market_view": str(result.get("market_view") or "")[:800], "decisions": decisions}


def research_candidates(candidates, *, api_key=None, requester=None):
    key = api_key or os.environ.get("OPENAI_API_KEY", "")
    if not key:
        return {"status": "unconfigured", "market_view": "", "decisions": []}
    if requester is None:
        import requests
        requester = requests.post
    rows = [{k: str(row.get(k) or "")[:150] for k in FIELDS} for row in candidates[:12]]
    response = requester(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        json={
            "model": MODEL, "store": False,
            "instructions": (
                "你是 TINO 的審慎模擬投資研究員。跨產業比較，不偏向既有持股或每天重複同一檔。"
                "檢查成長證據、估值與市場預期、產業動能、事件時點、風險報酬和失效價格；資料沒有就明說缺失。"
                "主動挑戰自己的看多理由，權衡反方情境。可全部拒絕，絕不為湊成交而提名。"
                "只依輸入的當輪掃描資料，不得發明新聞、財報、估值或價格，也不能聲稱知道使用者持倉。"
                "每個判斷說明投資論點、反方情境、失效條件與缺口。"
                "只有具充分依據、execution_status=ACTIONABLE 且 data_quality 至少70才提 PAPER_REVIEW；"
                "WAIT 和 REJECT 是合理結果。你只提名研究標的，正式交易模型會獨立否決。"
            ),
            "input": json.dumps(rows, ensure_ascii=False),
            "text": {"format": {"type": "json_schema", "name": "tino_paper_research",
                                "strict": True, "schema": SCHEMA}},
        }, timeout=(10, 60),
    )
    response.raise_for_status()
    parsed = json.loads(_output_text(response.json()))
    return {"status": "completed", **validate_research(parsed, candidates)}
