# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import List
from models import PriceFrame, SignalPacket


def _accepted(block: dict) -> bool:
    source = str(block.get("source", ""))
    return bool(block.get("accepted", False)) and "V12_DERIVED_PROXY" not in source and "PROXY" not in source.upper()


def tw_signals(price: PriceFrame) -> List[SignalPacket]:
    d = price.truth.date
    last = float(price.last)
    vwap = float(price.vwap or last)
    atr = max(float(price.atr14), 0.01)
    change_pct = (last - price.previous_close) / price.previous_close * 100.0 if price.previous_close else 0.0
    inst = price.context.get("inst", {})
    margin = price.context.get("margin", {})
    bsi = price.context.get("bsi", {})
    fundamental = price.context.get("fundamental", {})
    proxy = price.context.get("chip_proxy", {})
    tv = price.context.get("tv_pressure", {})
    fqc_strength = min(abs(last - vwap) / atr * 30.0, 45.0)
    fqc_signal = "FQC折疊壓縮｜先看止穩" if last < vwap else "FQC轉強｜買盤延續"
    bsi_cover = float(bsi.get("cover_rate", 0))
    bsi_signal = "空方回補啟動｜反彈條件改善" if bsi_cover >= 60 else "空方待驗證｜降權"
    inst_ok, margin_ok, bsi_ok = _accepted(inst), _accepted(margin), _accepted(bsi)
    foreign = float(inst.get("foreign", 0)) if inst_ok else 0.0
    trust = float(inst.get("trust", 0)) if inst_ok else 0.0
    dealer = float(inst.get("dealer", 0)) if inst_ok else 0.0
    total_inst = foreign + trust + dealer
    margin_delta = float(margin.get("margin", 0)) if margin_ok else 0.0
    short_delta = float(margin.get("short", 0)) if margin_ok else 0.0
    proxy_pressure = 0.0
    if proxy:
        proxy_pressure = max(min((float(proxy.get("foreign", 0)) + float(proxy.get("trust", 0))) / 120000.0, 0.08), -0.08)
    return [
        SignalPacket("FQC", fqc_signal, -5.0 if last < vwap else 6.0, 2.0, fqc_strength / 4, -0.10 if last < vwap else 0.08, f"強度 {fqc_strength:.1f}%｜VWAP {'下方' if last < vwap else '上方'}", "FQC", d, True),
        SignalPacket("BSI", bsi_signal, 5.0 if bsi_cover >= 60 and bsi_ok else 0.0, 0.0 if not bsi_ok else 1.0, 2.0 if bsi_cover >= 60 else 6.0, 0.12 if bsi_cover >= 60 and bsi_ok else 0.0, f"回補率 {bsi_cover:.0f}%｜風險 {bsi.get('risk','NA')}｜{bsi.get('reason','')}", bsi.get("source", "WAIT_SBL"), bsi.get("date", d), bsi_ok),
        SignalPacket("RCRS", "真崩盤防守" if change_pct < -5 and last < vwap else "風險可控", -10.0 if change_pct < -5 and last < vwap else 1.0, -2.0 if change_pct < -5 else 0.0, 12.0 if change_pct < -5 else 2.0, -0.18 if change_pct < -5 and last < vwap else 0.0, "Risk Cascade / 跌幅 / VWAP", "features_tw", d, True),
        SignalPacket("法人", f"正式法人 {'同步' if inst_ok else '未顯示'}", total_inst / 12000.0 if inst_ok else 0.0, 0.0, 4.0 if inst_ok and total_inst < 0 else 1.0, max(min(total_inst / 80000.0, .18), -.18) if inst_ok else 0.0, f"來源 {inst.get('source','WAIT_OFFICIAL')}｜{inst.get('reason','')}", inst.get("source", "WAIT_OFFICIAL"), inst.get("date", d), inst_ok),
        SignalPacket("資券", f"正式資券 {'同步' if margin_ok else '未顯示'}", -4.0 if margin_ok and margin_delta > 0 and last < vwap else (2.0 if margin_ok else 0.0), 0.0, 5.0 if margin_ok and margin_delta > 0 and last < vwap else 1.0, -0.07 if margin_ok and margin_delta > 0 and last < vwap else (0.02 if margin_ok else 0.0), f"來源 {margin.get('source','WAIT_OFFICIAL')}｜{margin.get('reason','')}", margin.get("source", "WAIT_OFFICIAL"), margin.get("date", d), margin_ok),
        SignalPacket("TV外資買賣壓", "待接 V9 匯率差公式" if not tv.get("accepted", False) else f"{tv.get('direction','預估大盤外資買賣壓')}｜{tv.get('amount_billion','待估')}億｜{tv.get('alert','警戒觀察')}｜個股：{tv.get('stock_fire','主力觀察')}", 0.0, 1.2 if tv.get("accepted", False) else 0.0, 3.0 if tv.get("level") == "高" else 1.0, 0.0, f"{tv.get('reason','只調信心')}｜信心 {tv.get('confidence','NA')}%", tv.get("source", "WAIT_V9_FX_DIFF_FORMULA"), tv.get("date", d), bool(tv.get("accepted", False))),
        SignalPacket("籌碼Proxy", "Proxy hidden from main dashboard", 0.0, 0.0, 2.0, 0.0, f"proxy_pressure {proxy_pressure:+.3f}｜hidden from official rows", proxy.get("source", "V12_DERIVED_PROXY"), proxy.get("date", d), False),
        SignalPacket("外資期貨", "待驗證｜降權", 0.0, -1.0, 4.0, 0.0, "期貨淨空單只降信心，未接正式 API 前不硬改價", "futures_proxy", d, False),
        SignalPacket("基本面", f"{fundamental.get('month','最近月')}｜營收 {fundamental.get('revenue','')}｜MoM {fundamental.get('mom','')}｜YoY {fundamental.get('yoy','')}｜EPS {fundamental.get('eps','')}", 0.0, 0.0, 1.0, 0.0, "財報/事件只進敘事與信心", fundamental.get("source", "WAIT_FINANCIAL"), d, bool(fundamental.get("accepted", False))),
        SignalPacket("Liquidity", "LBI 壓力層｜防守優先" if last < vwap else "LBI 可控", -5.0 if last < vwap else 2.0, 0.0, 6.0 if last < vwap else 2.0, -0.08 if last < vwap else 0.03, "量價障礙與 VWAP", "liquidity_engine", d, True),
    ]
