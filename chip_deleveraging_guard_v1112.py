# -*- coding: utf-8 -*-
"""Read-only Taiwan chip interpretation adapted from the V10.5.2 guard.

This module adds an evidence narrative only. It never changes model scores,
entry levels, or formal trade decisions. Stale or mismatched official data is
reported as unclassified instead of being treated as a current signal.
"""
from __future__ import annotations

from typing import Any, Mapping


def _map(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _num(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "").replace("%", "").strip())
        return number if number == number and abs(number) != float("inf") else None
    except Exception:
        return None


def _same_session(block: Mapping[str, Any], price_date: str) -> bool:
    target = str(price_date or "")[:10]
    observed = str(block.get("date") or "")[:10]
    return bool(
        target and observed == target
        and block.get("accepted") is True
        and not block.get("fallback", False)
        and str(block.get("freshness") or "today") == "today"
        and not block.get("field_warning")
    )


def classify_tw_chip_context(forecast: Any) -> dict[str, str]:
    """Classify current-session Taiwan margin/short/institutional evidence."""
    ticker = getattr(forecast, "ticker", None)
    if str(getattr(ticker, "market", "")).upper() != "TW":
        return {"label": "跨市場不套用", "text": "台股資券去槓桿判讀不套用美股。"}

    price = getattr(forecast, "price_frame", None)
    context = _map(getattr(price, "context", {}))
    target_date = str(getattr(price, "price_date", "") or "")[:10]
    margin = _map(context.get("margin"))
    inst = _map(context.get("inst"))
    if (not target_date or not _same_session(margin, target_date)
            or not _same_session(inst, target_date)):
        dates = [str(block.get("date") or "未標日期")[:10]
                 for block in (margin, inst)]
        return {
            "label": "本次不分類",
            "text": "法人與資券未同時通過驗證並對齊股價基準日；不做籌碼方向推論。",
            "source_date": "／".join(dates),
        }

    margin_today = _num(margin.get("margin"))
    margin_3 = _num(margin.get("margin_3"))
    margin_5 = _num(margin.get("margin_5"))
    short_today = _num(margin.get("short"))
    short_3 = _num(margin.get("short_3"))
    short_5 = _num(margin.get("short_5"))
    short_ratio = _num(margin.get("ratio"))
    foreign = _num(inst.get("foreign")) or 0.0
    trust = _num(inst.get("trust")) or 0.0
    dealer = _num(inst.get("dealer")) or 0.0
    inst_total = foreign + trust + dealer

    closes = [_num(value) for value in (getattr(price, "recent_closes", []) or [])]
    closes = [value for value in closes if value is not None and value > 0]
    current = _num(getattr(price, "last", None))
    previous = _num(getattr(price, "previous_close", None))
    change_pct = ((current / previous) - 1) * 100 if current and previous else 0.0
    ret20 = ((closes[-1] / closes[-21]) - 1) * 100 if len(closes) >= 21 else None
    peak = max(closes[-20:]) if closes else None
    drawdown = ((current / peak) - 1) * 100 if current and peak else None

    financing_down = margin_today is not None and margin_today < 0 and (
        (margin_3 is not None and margin_3 < 0)
        or (margin_5 is not None and margin_5 < 0)
    )
    financing_up = margin_today is not None and margin_today > 0 and (
        (margin_3 is not None and margin_3 > 0)
        or (margin_5 is not None and margin_5 > 0)
    )
    short_down = short_today is not None and short_today <= 0 and (
        (short_3 is not None and short_3 < 0)
        or (short_5 is not None and short_5 < 0)
    )
    short_up = short_today is not None and short_today > 0 and (
        (short_3 is not None and short_3 > 0)
        or (short_5 is not None and short_5 > 0)
    )
    short_low = short_ratio is not None and short_ratio <= 2.0
    inst_sell = inst_total < -1000 or foreign < -1000 or (foreign < 0 and trust < 0 and dealer < 0)
    inst_heavy_sell = inst_total < -8000 or foreign < -12000
    cleaned_gain = (
        ret20 is not None and drawdown is not None and ret20 <= 3.0 and drawdown <= -3.0
    ) or change_pct <= -5.0

    if financing_down and (short_down or short_low) and cleaned_gain and not inst_heavy_sell:
        label = "健康去槓桿"
        detail = "融資回落、空方壓力低／同步下降，且前段漲幅已清洗；法人未達重賣門檻。"
    elif financing_up and inst_sell:
        label = "融資增加且法人偏賣"
        detail = "融資仍增加並遇法人賣壓，接刀籌碼風險升高；等待融資轉減或價格止穩。"
    elif financing_down and change_pct <= -5.0 and short_low:
        label = "去槓桿後段觀察"
        detail = "融資下降且價格明顯回檔；不追空，等低點收復或量價止穩。"
    elif financing_up and short_up:
        label = "多空交戰"
        detail = "融資與融券同增，分歧與波動可能放大；等待價格方向確認。"
    elif financing_down and short_down:
        label = "籌碼降槓桿"
        detail = "融資與融券同步下降；後續方向交由法人與價格結構確認。"
    elif short_up and not inst_sell:
        label = "空方增加、觀察回補"
        detail = "融券增加但法人未呈明顯賣壓；需再看價格能否站回關鍵位。"
    else:
        label = "籌碼待觀察"
        detail = "融資、融券與法人尚未形成一致訊號，不以單一籌碼欄位定方向。"

    margin_source = str(margin.get("source") or "資券來源待確認")
    inst_source = str(inst.get("source") or "法人來源待確認")
    return {
        "label": label,
        "text": f"{detail}（{target_date}；法人 {inst_source}／資券 {margin_source}）",
        "source_date": target_date,
    }
