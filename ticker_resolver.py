# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from models import TickerInfo
from config import TW_PRICE_LIMIT_PCT, TWO_PRICE_LIMIT_PCT

TW_NAME_MAP = {
    "2337": ("旺宏", "2337.TW", "TWSE"),
    "2454": ("聯發科", "2454.TW", "TWSE"),
    "6770": ("力積電", "6770.TW", "TWSE"),
    # 6586 醣基是興櫃/TPEx 類股，Yahoo/TPEX 盤中價格常比上市櫃慢。
    # resolver 保持 .TWO 給 Yahoo，但 exchange 標成 TPEX_EMERGING，讓 Price Truth Guard
    # 使用「興櫃延遲參考」規則，而不是直接 STOP。
    "6586": ("醣基", "6586.TWO", "TPEX_EMERGING"),
    "5483": ("中美晶", "5483.TWO", "TPEX"),
    "3264": ("欣銓", "3264.TWO", "TPEX"),
    "00919": ("群益台灣精選高息", "00919.TW", "TWSE"),
    "2308": ("台達電", "2308.TW", "TWSE"),
    "3037": ("欣興", "3037.TW", "TWSE"),
    "2408": ("南亞科", "2408.TW", "TWSE"),
    "5469": ("瀚宇博", "5469.TW", "TWSE"),
}

# RC2.4.3 Price Truth Guard
# A small hard guard for codes known to be TPEx/OTC so a plain numeric input
# like 3264 will not be silently normalized to 3264.TW.  The resolver can be
# expanded later with an official TWSE/TPEx symbol map, but these overrides
# prevent the exact failure where 3264 欣銓 was queried as a listed stock and
# fell through to a synthetic/default price.
TPEX_CODE_OVERRIDES = {
    "3264",  # 欣銓
    "5483",  # 中美晶
    "6586",  # 醣基 / 興櫃類，仍使用 .TWO 查價
}

# 興櫃類股盤中報價常由 Yahoo/TPEx 延遲回傳；不可 fallback 假價格，
# 但允許在「已取得有效真實價格」時作為延遲參考。
EMERGING_CODE_OVERRIDES = {
    "6586",  # 醣基
}

TW_NAME_ALIAS = {
    "聯發科": "2454", "MEDIATEK": "2454",
    "旺宏": "2337", "MACRONIX": "2337",
    "力積電": "6770",
    "醣基": "6586",
    "欣銓": "3264",
    "中美晶": "5483", "SAS": "5483",
    "台達電": "2308",
    "欣興": "3037", "欣興電子": "3037",
    "南亞科": "2408",
    "瀚宇博": "5469",
}

US_NAME_MAP = {
    "ONDS": ("Ondas Holdings", "ONDS", "NASDAQ"),
    "MRVL": ("Marvell Technology", "MRVL", "NASDAQ"),
    "MU": ("Micron Technology", "MU", "NASDAQ"),
    "NKE": ("Nike", "NKE", "NYSE"),
    "AAPL": ("Apple", "AAPL", "NASDAQ"),
    "NVDA": ("NVIDIA", "NVDA", "NASDAQ"),
    "TSM": ("Taiwan Semiconductor", "TSM", "NYSE"),
}

ETF_CODES = {"00919", "0050", "00918", "00929", "00981A", "009823", "00997A"}


def _clean(raw: str) -> str:
    return str(raw or "").strip().upper().replace(" ", "")


def _split_tw_suffix(text: str) -> tuple[str, str | None]:
    """Split Taiwan market suffix safely.

    IMPORTANT:
    - Check .TWO before .TW because .TWO starts with .TW.
    - Never convert the O in .TWO into 0.
    """
    if text.endswith(".TWO"):
        return text[:-4], ".TWO"
    if text.endswith(".TW"):
        return text[:-3], ".TW"
    return text, None


def resolve_ticker(raw: str) -> TickerInfo:
    text = _clean(raw)
    if not text:
        raise ValueError("Ticker 不可為空")

    base, explicit_suffix = _split_tw_suffix(text)

    if base in TW_NAME_ALIAS:
        base = TW_NAME_ALIAS[base]
        explicit_suffix = None

    if re.fullmatch(r"\d{4,5}[A-Z]?", base):
        mapped = TW_NAME_MAP.get(base)
        if mapped:
            name, symbol, exchange = mapped
        else:
            # Unknown numeric Taiwan symbols default to TWSE only after the
            # explicit TPEx override check.  Unknown codes are still validated
            # later by Price Truth Guard, so missing official quotes cannot
            # become fake 100/103/97 fallback forecasts.
            default_suffix = ".TWO" if base in TPEX_CODE_OVERRIDES else ".TW"
            ex = "TPEX_EMERGING" if base in EMERGING_CODE_OVERRIDES else ("TPEX" if default_suffix == ".TWO" else "TWSE")
            name, symbol, exchange = base, f"{base}{default_suffix}", ex

        if explicit_suffix == ".TWO":
            symbol = f"{base}.TWO"
            exchange = "TPEX_EMERGING" if base in EMERGING_CODE_OVERRIDES else "TPEX"
        elif explicit_suffix == ".TW":
            if base in TPEX_CODE_OVERRIDES:
                # Known TPEx codes must not be forced into .TW; this is a
                # market-resolver correction, not a user-facing engineering string.
                symbol = f"{base}.TWO"
                exchange = "TPEX_EMERGING" if base in EMERGING_CODE_OVERRIDES else "TPEX"
            else:
                symbol, exchange = f"{base}.TW", "TWSE"

        asset_type = "etf" if base in ETF_CODES or base.startswith("00") else "stock"
        pct = TWO_PRICE_LIMIT_PCT if symbol.endswith(".TWO") else TW_PRICE_LIMIT_PCT
        return TickerInfo(raw=raw, resolved_symbol=symbol, name=name, market="TW", asset_type=asset_type, exchange=exchange, currency="TWD", price_limit_pct=pct)

    symbol = text.split(".")[0]
    name, resolved, exchange = US_NAME_MAP.get(symbol, (symbol, symbol, "US"))
    return TickerInfo(raw=raw, resolved_symbol=resolved, name=name, market="US", asset_type="stock", exchange=exchange, currency="USD", price_limit_pct=None)
