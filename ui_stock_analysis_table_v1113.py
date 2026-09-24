# -*- coding: utf-8 -*-
"""Wide, four-column stock-analysis summary used in single and multi-stock views."""
from __future__ import annotations

import html
from typing import Any, Mapping


def _escape(value: Any) -> str:
    return html.escape(str(value or "資料待確認"), quote=True)


def _cell(title: str, content: Any, detail: Any = "") -> str:
    sub = f"<div class='tino-brief-sub'>{_escape(detail)}</div>" if detail else ""
    return (
        "<section class='tino-brief-cell'>"
        f"<div class='tino-brief-label'>{_escape(title)}</div>"
        f"<div class='tino-brief-value'>{_escape(content)}</div>{sub}</section>"
    )


def render_stock_analysis_table(st: Any, row: Mapping[str, Any], *,
                                symbol: str = "", name: str = "") -> None:
    """Render the requested per-stock, no-ranking four-column analysis row."""
    identity = "｜".join(item for item in (str(symbol or "").strip(), str(name or "").strip()) if item)
    industry = str(row.get("industry") or "產業資料待確認")
    price = str(row.get("price_status") or "價格／日期未驗證")
    model_low = str(row.get("model_low") or "")
    evidence = str(row.get("evidence") or row.get("narrative_evidence") or "有效證據不足；來源待確認")
    cells = "".join((
        _cell("產業／價格狀態", industry, "；".join(part for part in (price, model_low) if part)),
        _cell("條件式進場與分批", row.get("entry") or "等待模型位階"),
        _cell("失效條件／主要風險", row.get("risk") or row.get("risk_cell") or "風險條件待確認"),
        _cell("證據", evidence),
    ))
    title = f"<div class='tino-brief-title'>{_escape(identity)}</div>" if identity else ""
    st.markdown(
        """<style>
        .tino-brief-wrap{margin:10px 0 14px;border:1px solid rgba(54,230,255,.30);border-radius:10px;overflow:hidden;background:#061321;color:#eaf6ff}
        .tino-brief-title{padding:8px 12px;background:#0a1b2a;border-bottom:1px solid rgba(115,190,225,.24);font-size:16px;font-weight:850;color:#f3f9ff}
        .tino-brief-grid{display:grid;grid-template-columns:19% 26% 22% 33%}
        .tino-brief-cell{min-width:0;padding:10px 12px;border-right:1px solid rgba(115,190,225,.20);border-bottom:1px solid rgba(115,190,225,.20);overflow-wrap:anywhere}
        .tino-brief-cell:last-child{border-right:0}
        .tino-brief-label{font-size:12px;font-weight:800;color:#a9dbf3;margin-bottom:6px}
        .tino-brief-value{font-size:14px;line-height:1.42;font-weight:650;color:#f4f8fc;white-space:pre-line}
        .tino-brief-sub{font-size:12px;line-height:1.35;color:#b9d7e8;margin-top:4px;white-space:pre-line}
        @media(max-width:900px){.tino-brief-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.tino-brief-cell:nth-child(2){border-right:0}}
        @media(max-width:560px){.tino-brief-grid{grid-template-columns:1fr}.tino-brief-cell{border-right:0;padding:8px 10px}.tino-brief-cell:last-child{border-bottom:0}}
        </style>"""
        f"<div class='tino-brief-wrap'>{title}<div class='tino-brief-grid'>{cells}</div></div>",
        unsafe_allow_html=True,
    )
