# -*- coding: utf-8 -*-
"""V1087 evidence-aware public decision language.

This module only converts an already-arbitrated situation into precise user
language.  It never changes formal price forecasts, audit rows or weights.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _variant(symbol: str, size: int) -> int:
    return sum(ord(ch) for ch in _text(symbol)) % max(1, size)


def _pick(symbol: str, rows: Sequence[str]) -> str:
    return rows[_variant(symbol, len(rows))] if rows else ""


def compose_action_language(
    *, situation: str, code: str, symbol: str, current: str, confirmation: str,
    breakout: str, invalid: str, metrics: Mapping[str, Any],
    entry_state_label: str, leading_driver: str,
) -> dict[str, str]:
    """Return scenario-specific wording without inventing evidence."""
    t1 = metrics.get("t1_return_pct")
    a, b, c = metrics.get("abc_a"), metrics.get("abc_b"), metrics.get("abc_c")
    t1_text = f"T1預期 {float(t1):+.2f}%" if t1 is not None else "T1報酬尚未驗證"
    abc_text = (
        f"ABC A/B/C={float(a):.0f}/{float(b):.0f}/{float(c):.0f}%"
        if a is not None and b is not None and c is not None else "ABC情境未完整"
    )
    driver = _text(leading_driver) or "價格結構"
    repair = confirmation if confirmation not in {"", "--"} else "VWAP／關鍵均價"
    risk_line = invalid if invalid not in {"", "--"} else "有效防守線"

    if situation == "BUY_READY":
        instruction = _pick(symbol, (
            f"買進｜{current}附近先小量｜跌破 {invalid} 停損",
            f"買進條件成立｜{current}附近分批建立首倉｜{invalid}失效",
        ))
        return {"label": "買進", "instruction": instruction,
                "reason": f"{t1_text}、{abc_text}與{driver}共同通過買進閘門"}

    if situation == "BUY_LOW_ENTRY":
        return {
            "label": "可以低接｜小倉試單",
            "instruction": f"{current}附近建立20%～30%試單｜跌破 {invalid} 停損｜不一次買滿",
            "reason": f"已完成回測確認；{t1_text}已收斂、{abc_text}且無強空共振",
        }

    if situation == "BUY_RECOVERY_ENTRY":
        evidence = []
        if metrics.get("deleveraging_evidence"):
            evidence.append("融資／槓桿清洗")
        if metrics.get("positive_event"):
            evidence.append("正面新聞／基本面")
        if metrics.get("positive_market"):
            evidence.append("跨市場轉穩")
        evidence_text = "、".join(evidence) or "修復證據"
        return {
            "label": "修復布局｜第一層買進",
            "instruction": f"{current}附近建立15%～20%首倉｜跌破 {invalid} 停損｜正式轉強後才加碼",
            "reason": f"{evidence_text}與價格站回VWAP、回測確認共同成立；{t1_text}、{abc_text}",
        }

    if situation == "HOLD_T1_NEGATIVE":
        if bool(metrics.get("rebound_monitor")):
            instruction = _pick(symbol, (
                f"已止跌反彈但不追價｜等待回測承接或T1改善後再啟動｜持股守 {invalid}",
                f"短線反彈成立｜空手等待回測，不在連漲後追買｜持股守 {invalid}",
            ))
            return {
                "label": "反彈監控｜已止跌反彈，尚未確認轉強",
                "instruction": instruction,
                "reason": (
                    f"今日 {float(metrics.get('day_return_pct') or 0):+.2f}% 且已站回VWAP，"
                    f"價格確認止跌反彈；但{t1_text}、{abc_text}，尚不具追價資格"
                ),
            }
        instruction = _pick(symbol, (
            "本日不建立新部位｜待價格止跌、T1改善且ABC風險下降後重算",
            "空手暫停買進｜尚未形成價格止跌證據，不啟動價格觸發",
        ))
        return {"label": "低檔監控｜尚未止跌", "instruction": instruction,
                "reason": f"{t1_text}，且價格尚未形成明確反彈確認；{abc_text}"}

    if situation == "HOLD_ABC_DEFENSIVE":
        return {"label": "空手暫不買｜持股守防線",
                "instruction": "取消當日突破買點｜待A突破回升、C防守下降後再評估",
                "reason": f"{abc_text}顯示回測／防守路徑主導，尚不具買進資格"}

    if situation == "HOLD_OVERHEATED":
        if confirmation == "--" and breakout == "--":
            instruction = _pick(symbol, (
                f"今日不追價｜持股守 {invalid}｜下一Session依新VWAP與量能重算",
                f"空手不追｜持股續抱並守 {invalid}｜開板或回測後再判斷",
            ))
        else:
            instruction = _pick(symbol, (
                f"今日不追價｜回測站回 {confirmation} 或放量突破 {breakout} 後再買｜持股守 {invalid}",
                f"空手不追｜回測站回 {confirmation} 或放量突破 {breakout} 才重啟｜持股守 {invalid}",
            ))
        return {"label": "空手不追｜持股續抱", "instruction": instruction,
                "reason": f"價格位於過熱／流動性區；{abc_text}，追價風險高於即時報酬"}

    if situation == "HOLD_TRIGGER_PENDING":
        allow_pullback = bool(metrics.get("allow_pullback")) and confirmation != "--"
        allow_breakout = bool(metrics.get("allow_breakout")) and breakout != "--"
        if allow_pullback and not allow_breakout:
            instruction = _pick(symbol, (
                f"{entry_state_label}｜回測量縮後站回 {confirmation} 才買｜{invalid}取消",
                f"尚未買進｜只保留回測型條件：守穩後收復 {confirmation}｜失效 {invalid}",
            ))
            pending_reason = f"僅保留回測型買進；{t1_text}、{abc_text}，主導證據為{driver}"
        elif allow_breakout and not allow_pullback:
            instruction = f"{entry_state_label}｜放量站穩 {breakout} 才買｜{invalid}取消"
            pending_reason = f"僅保留突破型買進；{t1_text}、{abc_text}，主導證據為{driver}"
        else:
            instruction = _pick(symbol, (
                f"{entry_state_label}｜回測站回 {confirmation} 才買；或放量突破 {breakout}｜{invalid}取消",
                f"尚未買進｜先完成「{entry_state_label}」；確認 {confirmation}／突破 {breakout}｜失效 {invalid}",
            ))
            pending_reason = f"買進閘門通過但觸發流程未完成；{t1_text}、主導證據為{driver}"
        return {"label": "空手等條件｜持股續抱", "instruction": instruction,
                "reason": pending_reason}

    if situation == "REDUCE_WEAKNESS":
        return {"label": "減碼｜空手不買",
                "instruction": f"先降低持股｜跌破 {invalid} 全出｜未重新取得買進資格前不低接",
                "reason": f"價格尚未收復關鍵結構，且{t1_text}；偏空證據由{driver}主導"}

    if situation == "SELL_PRICE_INVALID":
        return {"label": "賣出", "instruction": f"賣出｜取消低接｜未重建結構前不進場",
                "reason": f"現價已跌破 {invalid}，原交易結構正式失效"}

    if situation in {"HOLD_RECLAIM_WATCH", "REDUCE_INTRADAY_BREACH_RECLAIMED"}:
        return {"label": "假跌破收復觀察",
                "instruction": f"現價已收回 {risk_line}｜持股觀察站穩；空手等待再次確認，不立即減碼或追價",
                "reason": f"盤中最低價曾跌破 {risk_line}，但現價 {current} 已收回；只有再次失守或量價轉弱才升級減碼"}

    if situation == "REDUCE_FAILED_BREAKOUT":
        return {"label": "突破失敗減碼｜空手不買",
                "instruction": f"退出短線突破部位｜未重新站回 {repair} 前不重進｜失效防線 {risk_line}",
                "reason": f"原突破條件已失敗，由{driver}主導；現價未跌破本卡失效價，不宣稱價格失效"}

    if situation == "REDUCE_SELLING_EXPANSION":
        return {"label": "減碼｜空手不接刀",
                "instruction": f"先降低持股風險｜等待賣壓量縮並收復 {repair}｜跌破 {risk_line} 才升級賣出",
                "reason": f"賣壓擴張且{driver}偏空；現價 {current} 仍未跌破 {risk_line}，因此不是結構失效賣出"}

    if situation == "BLOCK_DATA":
        return {"label": "禁止進場", "instruction": "同源價格或Session未驗證｜不建立新部位",
                "reason": "資料品質未通過，任何精準價格都可能是假訊號"}

    if situation == "BLOCK_RISK":
        return {"label": "禁止進場", "instruction": "強空方共振｜不建立新部位",
                "reason": f"價格接受度不足，且{t1_text}、{abc_text}與{driver}同步偏空"}

    return {"label": "本日無買點｜持股依防線管理",
            "instruction": f"空手不買｜持股守 {invalid}｜下一Session重新仲裁",
            "reason": f"跨模組尚未形成合格買進組合；{t1_text}、{abc_text}"}
