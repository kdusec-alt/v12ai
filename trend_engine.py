# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from models import PriceFrame


@dataclass(frozen=True)
class TrendSnapshot:
    direction: str
    streak_days: int
    streak_return_pct: Optional[float]
    ret_5d: Optional[float]
    ret_10d: Optional[float]
    ret_20d: Optional[float]
    ret_60d: Optional[float]
    ma20: Optional[float]
    ma60: Optional[float]
    ma20_gap_pct: Optional[float]
    ma60_gap_pct: Optional[float]
    ma_alert: str
    source: str
    mode: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _positive_closes(values: List[Any]) -> List[float]:
    out: List[float] = []
    for v in values or []:
        try:
            x = float(v)
            if x > 0:
                out.append(x)
        except Exception:
            continue
    return out


def _pct(now: float, base: float) -> Optional[float]:
    try:
        if float(base) <= 0:
            return None
        return (float(now) / float(base) - 1.0) * 100.0
    except Exception:
        return None


def _mean_tail(values: List[float], n: int) -> Optional[float]:
    if len(values) < n:
        return None
    tail = values[-n:]
    return sum(tail) / float(n)


def _is_live_unconfirmed(price: PriceFrame) -> bool:
    status = str(getattr(price, "market_status", "") or "")
    meta = (price.context or {}).get("price_meta", {})
    if not isinstance(meta, dict):
        meta = {}
    # 台股盤中 / 收盤確認期與美股盤前盤後都不是正式日K收盤。
    if status in {"intraday", "close_confirm", "pre_market", "after_hours"}:
        return True
    # 若資料源明確標示延遲參考，也不拿來算正式連漲連跌。
    if bool(meta.get("limited_price_mode")):
        return True
    return False


def _confirmed_closes(price: PriceFrame) -> List[float]:
    closes = _positive_closes(list(getattr(price, "recent_closes", []) or []))
    if len(closes) >= 2 and _is_live_unconfirmed(price):
        # data_sources 可能把 live last 塞到 closes[-1]；正式連漲連跌必須先排除。
        return closes[:-1]
    return closes


def _streak_from_closes(closes: List[float]) -> tuple[str, int, Optional[float]]:
    if len(closes) < 2:
        return "盤勢觀察", 0, None
    last_delta = closes[-1] - closes[-2]
    if abs(last_delta) < 1e-9:
        return "盤整", 0, 0.0
    up = last_delta > 0
    count = 1
    for i in range(len(closes) - 2, 0, -1):
        delta = closes[i] - closes[i - 1]
        if abs(delta) < 1e-9 or (delta > 0) != up:
            break
        count += 1
    start_index = max(0, len(closes) - count - 1)
    ret = _pct(closes[-1], closes[start_index])
    return ("連漲" if up else "連跌"), count, ret


def _fixed_return(closes: List[float], n: int, current: Optional[float] = None) -> Optional[float]:
    if len(closes) < n:
        return None
    now = float(current) if current and current > 0 else closes[-1]
    return _pct(now, closes[-n])


def _ma_alert(last: float, ma20: Optional[float], ma60: Optional[float]) -> str:
    parts: List[str] = []
    if ma20 and ma20 > 0:
        gap20 = (last / ma20 - 1.0) * 100.0
        if abs(gap20) <= 1.5:
            parts.append("月線攻防")
        elif gap20 > 0:
            parts.append("月線上方")
        else:
            parts.append("跌破月線")
    if ma60 and ma60 > 0:
        gap60 = (last / ma60 - 1.0) * 100.0
        if abs(gap60) <= 2.5:
            parts.append("季線攻防")
        elif gap60 > 0:
            parts.append("季線上方")
        else:
            parts.append("跌破季線")
    return "｜".join(parts) if parts else "均線資料不足"


def build_trend_snapshot(price: PriceFrame) -> TrendSnapshot:
    raw_closes = _positive_closes(list(getattr(price, "recent_closes", []) or []))
    confirmed = _confirmed_closes(price)
    last = float(getattr(price, "last", 0) or 0)
    current_for_fixed = last if last > 0 else (raw_closes[-1] if raw_closes else None)

    direction, days, streak_ret = _streak_from_closes(confirmed)
    ma20 = _mean_tail(confirmed, 20)
    ma60 = _mean_tail(confirmed, 60)
    ma20_gap = _pct(current_for_fixed, ma20) if ma20 and current_for_fixed else None
    ma60_gap = _pct(current_for_fixed, ma60) if ma60 and current_for_fixed else None

    mode = "正式日K" if not _is_live_unconfirmed(price) else "盤中參考"
    return TrendSnapshot(
        direction=direction,
        streak_days=days,
        streak_return_pct=streak_ret,
        ret_5d=_fixed_return(confirmed, 5, current_for_fixed),
        ret_10d=_fixed_return(confirmed, 10, current_for_fixed),
        ret_20d=_fixed_return(confirmed, 20, current_for_fixed),
        ret_60d=_fixed_return(confirmed, 60, current_for_fixed),
        ma20=ma20,
        ma60=ma60,
        ma20_gap_pct=ma20_gap,
        ma60_gap_pct=ma60_gap,
        ma_alert=_ma_alert(current_for_fixed or 0.0, ma20, ma60),
        source="TrendEngine_CloseToClose",
        mode=mode,
    )


def _fmt_pct(v: Optional[float]) -> str:
    if v is None:
        return "--"
    return f"{float(v):+.2f}%"


def _fmt_price(v: Optional[float]) -> str:
    if v is None:
        return "--"
    x = float(v)
    if abs(x) >= 1000:
        return f"{x:,.0f}"
    if abs(x) >= 100:
        return f"{x:,.1f}"
    return f"{x:,.2f}"


def _display_streak(price: PriceFrame) -> tuple[str, int, Optional[float], str]:
    """Return the streak shown on the UI without changing formal model inputs.

    Formal trend calculations intentionally exclude unconfirmed intraday prices.
    For the battle-panel header, however, Taiwan intraday / close-confirm sessions
    should include the current live price as a provisional close; otherwise the
    header shows yesterday's streak percentage next to today's large move.
    """
    confirmed = _confirmed_closes(price)
    status = str(getattr(price, "market_status", "") or "")
    mode = ""

    if status in {"intraday", "close_confirm"}:
        last = float(getattr(price, "last", 0) or 0)
        if last > 0 and confirmed:
            direction, days, streak_ret = _streak_from_closes([*confirmed, last])
            return direction, days, streak_ret, "｜盤中參考"
        mode = "｜盤中參考"
    elif status == "pre_market":
        mode = "｜盤前參考"
    elif status == "after_hours":
        mode = "｜盤後參考"
    elif _is_live_unconfirmed(price):
        mode = "｜盤中參考"

    direction, days, streak_ret = _streak_from_closes(confirmed)
    return direction, days, streak_ret, mode


def trend_tag(price: PriceFrame) -> str:
    direction, days, streak_ret, suffix = _display_streak(price)
    if days <= 0 or streak_ret is None:
        return f"{direction}{suffix}"
    return f"{direction}{days}天 {_fmt_pct(streak_ret)}{suffix}"


def _ma_piece(label: str, value: Optional[float], gap: Optional[float], need_days: int) -> str:
    if value is None or gap is None:
        return f"{label}資料不足(<{need_days}日)"
    return f"{label} {_fmt_price(value)}({_fmt_pct(gap)})"


def trend_radar_line(price: PriceFrame) -> str:
    s = build_trend_snapshot(price)
    streak = trend_tag(price)
    return (
        f"{streak}｜5D {_fmt_pct(s.ret_5d)}｜10D {_fmt_pct(s.ret_10d)}｜20D {_fmt_pct(s.ret_20d)}"
        f"｜{_ma_piece('月線MA20', s.ma20, s.ma20_gap_pct, 20)}"
        f"｜{_ma_piece('季線MA60', s.ma60, s.ma60_gap_pct, 60)}｜{s.ma_alert}"
    )
