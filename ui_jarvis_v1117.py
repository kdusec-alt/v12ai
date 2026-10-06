# -*- coding: utf-8 -*-
"""Lightweight command UI. Reads formal outputs; no network, workers or models."""
from __future__ import annotations

import html
import math
from collections.abc import Mapping


STYLE = """<style>
.j-shell{--j-cyan:#65e4ef;--j-muted:#8aabb8;color:#eef6f8;font-family:Arial,'Microsoft JhengHei',sans-serif}
.j-standby{text-align:center;max-width:820px;margin:26px auto 8px;padding:28px 24px 20px;border:1px solid #173640;border-radius:26px;background:radial-gradient(ellipse at 50% 18%,#0e2937 0%,#06131d 65%);position:relative;overflow:hidden}
.j-eyebrow{color:var(--j-muted);font-size:13px;letter-spacing:3px;text-transform:uppercase;font-weight:700}
.j-standby h1{font-size:clamp(27px,4vw,42px);letter-spacing:-1px;margin:12px 0;color:#eef6f8;line-height:1.25}
.j-standby p{font-size:14px;color:#aac2cc;line-height:1.8;margin:4px 0}
.j-orb{width:154px;height:154px;margin:18px auto;position:relative;display:grid;place-items:center}
.j-orb:before,.j-orb:after{content:'';position:absolute;border-radius:50%;inset:5px;border:1px solid #306273;border-top:2px solid #68dfea;border-right-color:transparent;animation:j-orbit 24s linear infinite}
.j-orb:after{inset:18px;border-color:#214d5b;border-bottom:2px solid #dbb96d;border-left-color:transparent;animation-direction:reverse;animation-duration:18s}
.j-core{width:99px;height:99px;border-radius:50%;display:grid;place-items:center;background:radial-gradient(circle,#215166 0%,#0c2737 55%,#0b1b29 75%);border:1px solid #6096a8;box-shadow:0 0 28px #3dcdde22,inset 0 0 25px #65e4ef12;color:#b4f5f7;font-size:23px;font-weight:800;letter-spacing:3px}
.j-state{display:inline-flex;align-items:center;gap:8px;border:1px solid #295344;border-radius:99px;padding:5px 12px;color:#a7dec2;font-size:13px;letter-spacing:1px}.j-dot{width:6px;height:6px;border-radius:50%;background:#91d9b1}
.j-command{display:grid;grid-template-columns:1fr 140px 1.4fr;gap:22px;align-items:center;margin:16px 0;padding:24px 28px;background:linear-gradient(120deg,#0a1c29,#07121b);border:1px solid #24434f;border-radius:22px}
.j-command .j-orb{width:115px;height:115px;margin:0}.j-command .j-core{width:75px;height:75px;font-size:17px}
.j-symbol{font-size:14px;letter-spacing:2px;color:#90b7c7;margin:7px 0}.j-name{font-size:26px;font-weight:800;margin:0 0 8px}.j-price{font-size:clamp(32px,4vw,48px);font-weight:800;letter-spacing:-1.5px;line-height:1.2}.j-price small{font-size:14px;letter-spacing:0;font-weight:400;color:#8ea9b6;margin-left:8px}
.j-meta{font-size:13px;color:#91acb8;margin-top:10px;line-height:1.7;overflow-wrap:anywhere}
.j-verdict{font-size:23px;font-weight:800;margin:8px 0;color:#e5c681}.j-command[data-tone='green'] .j-verdict{color:#90e4bb}.j-command[data-tone='red'] .j-verdict{color:#ffa4ae}
.j-thesis{font-size:14px;line-height:1.8;color:#d4e4e9}.j-risk{margin-top:10px;font-size:14px;color:#b6c7d1;line-height:1.7}
.j-levels{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:12px 0 16px}.j-level{background:#0a1a26;border:1px solid #23414f;border-radius:15px;padding:16px;min-width:0}.j-level span{font-size:13px;color:#91abb8;letter-spacing:.5px}.j-level strong{display:block;font-size:25px;margin:6px 0;color:#e9f7fa;overflow-wrap:anywhere}.j-level small{display:block;font-size:13px;color:#91abb8;line-height:1.65}.j-level:last-child{border-color:#624847}.j-level:last-child strong{color:#ffb2b4}
.j-note{padding:10px 14px;border-left:2px solid #4ab7c9;background:#071923;color:#9db9c5;font-size:14px;line-height:1.7;border-radius:0 10px 10px 0;margin:12px 0 18px}
.j-section{display:flex;justify-content:space-between;gap:12px;padding:14px 0 8px;border-top:1px solid #17313f;font-size:14px;color:#8daebb;letter-spacing:1px}
@keyframes j-orbit{to{transform:rotate(360deg)}}
@media(prefers-reduced-motion:reduce){.j-orb:before,.j-orb:after{animation:none}}
@media(max-width:760px){.j-command{grid-template-columns:1fr;padding:20px;gap:12px}.j-command>.j-orb{display:none}.j-levels{grid-template-columns:repeat(2,minmax(0,1fr))}.j-standby{margin-top:10px;padding:20px 16px}.j-standby .j-orb{width:125px;height:125px}.j-name{font-size:22px}.j-level{padding:13px}.j-level strong{font-size:22px}}
/* Compact console: full details remain in the original panels below. */
.j-standby{max-width:none;display:grid;grid-template-columns:90px 1fr;align-items:center;text-align:left;gap:4px 18px;margin:8px 0;padding:16px 24px;border-radius:18px}
.j-standby .j-orb{grid-column:1;grid-row:1/5;width:80px;height:80px;margin:0}
.j-standby .j-core{width:52px;height:52px;font-size:14px}
.j-standby .j-eyebrow,.j-standby .j-state,.j-standby h1,.j-standby p{grid-column:2}
.j-standby h1{font-size:24px;margin:3px 0}.j-standby .j-state{width:fit-content;padding:3px 10px}
.j-command{grid-template-columns:1fr 70px 1.8fr;gap:16px;padding:12px 18px;margin:8px 0;border-radius:16px}
.j-command .j-orb{width:68px;height:68px}.j-command .j-core{width:43px;height:43px;font-size:12px}
.j-name{font-size:20px;margin-bottom:4px}.j-price{font-size:30px}.j-meta{margin-top:4px}.j-verdict{font-size:20px;margin:4px 0}.j-risk{margin-top:4px}
.j-levels{gap:8px;margin:8px 0}.j-level{padding:9px 12px}.j-level strong{font-size:20px;margin:3px 0}.j-level small{display:none}
.j-note{padding:6px 12px;margin:6px 0;font-size:13px}.j-section{padding:7px 0 4px}
[data-testid="stRadio"] label p{color:#d8edf4!important;font-size:14px!important}
[data-testid="stTextInput"] input::placeholder{color:#91acbd!important;opacity:1!important}
@media(max-width:760px){.j-command{grid-template-columns:1fr}.j-standby{padding:12px;grid-template-columns:64px 1fr;gap:8px}.j-standby .j-orb{width:60px;height:60px}.j-standby h1{font-size:20px}.j-standby .j-eyebrow{letter-spacing:1px;font-size:12px}.j-standby p{font-size:13px}}
</style>"""


def _text(value, default="待確認"):
    return html.escape(str(default if value is None or value == "" else value), quote=True)


def _map(value):
    return value if isinstance(value, Mapping) else {}


def _price(value):
    try:
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            return "—"
        return f"{number:,.2f}"
    except (TypeError, ValueError, OverflowError):
        return "—"


def _orb():
    return '<div class="j-orb" aria-hidden="true"><div class="j-core">TINO</div></div>'


def standby_html():
    return STYLE + '<section class="j-shell j-standby">' + (
        '<div class="j-eyebrow">TINO · INTELLIGENCE CONSOLE</div>' + _orb() +
        '<div class="j-state"><span class="j-dot"></span>等待股票指令</div>'
        '<h1>今天，想研究哪一檔？</h1>'
        '<p>輸入台股、美股或 ETF，查看價格、進場條件與市場證據。</p>'
        '</section>'
    )


def render_standby(st):
    st.markdown(standby_html(), unsafe_allow_html=True)


def command_html(forecast, payload):
    """Render a read-only projection of the same snapshot used by the battle UI."""
    if getattr(forecast, "stopped", False):
        return STYLE + '<div class="j-shell j-note">' + _text(getattr(forecast, "stop_reason", "資料未通過驗證")) + '</div>'
    payload = _map(payload)
    snap = _map(payload.get("public_snapshot"))
    brief = _map(payload.get("decision_brief"))
    plan = _map(snap.get("entry"))
    ticker = getattr(forecast, "ticker", None)
    card = _map(getattr(forecast, "decision_card", None))
    frame = getattr(forecast, "price_frame", None)
    # The display source must match the current battle panel, including its truth guard.
    last = card.get("現價") if "現價" in card else getattr(frame, "last", None)
    truth = getattr(frame, "truth", None)
    date = getattr(frame, "price_date", None) or getattr(truth, "date", None)
    source = card.get("價格來源") or getattr(truth, "source", None)
    status = card.get("資料標題") or getattr(frame, "market_status", None)
    tone = str(snap.get("color") or "yellow").lower()
    tone = tone if tone in {"green", "red", "yellow"} else "yellow"
    # A brief's explicit '—/等待/禁止' must remain explicit; never replace it with raw entry.
    values = [
        ("AI 建議進場區", brief.get("entry_zone") or "等待條件確認", brief.get("entry_instruction") or "依正式進場條件執行"),
        ("確認價", brief.get("confirmation") or "—", brief.get("confirmation_instruction") or "確認成立後再評估"),
        ("加碼價", brief.get("breakout") or "—", brief.get("breakout_instruction") or "依分批計畫執行"),
        ("失效／防守", brief.get("invalidation") or "—", brief.get("invalidation_instruction") or "非保證成交的停損價"),
    ]
    levels = ''.join('<div class="j-level"><span>' + _text(label) + '</span><strong>' + _text(value) + '</strong><small>' + _text(note) + '</small></div>' for label, value, note in values)
    verdict = brief.get("verdict") or snap.get("label") or "正式決策待確認"
    thesis = brief.get("summary") or snap.get("reason") or "等待有效資料"
    risk = brief.get("primary_risk") or "風險條件待確認"
    confidence = brief.get("confidence_label") or "待確認"
    return STYLE + (
        '<section class="j-shell j-command" data-tone="' + tone + '"><div>'
        '<div class="j-eyebrow">STOCK INTELLIGENCE</div><div class="j-symbol">' + _text(getattr(ticker, "resolved_symbol", None)) + '</div>'
        '<div class="j-name">' + _text(getattr(ticker, "name", None)) + '</div>'
        '<div class="j-price">' + _price(last) + '<small>' + _text(getattr(ticker, "currency", None), "") + '</small></div>'
        '<div class="j-meta">' + _text(status) + ' · ' + _text(date) + '<br>來源：' + _text(source) +
        ('<br>' + _text(card.get("價格時間")) if card.get("價格時間") else '') + '</div></div>' + _orb() + '<div>'
        '<div class="j-eyebrow">AI DECISION · 信心 ' + _text(confidence) + '</div>'
        '<div class="j-verdict">' + _text(verdict) + '</div><div class="j-thesis">' + _text(thesis) + '</div>'
        '<div class="j-risk">主要風險：' + _text(risk) + '</div></div></section>'
        '<div class="j-shell j-levels">' + levels + '</div>'
        '<div class="j-shell j-note">隔日預測收盤 ' + _price(getattr(forecast, "final_t1", None)) +
        ' ｜ 隔日高點 ' + _price(getattr(forecast, "final_t1_high", None)) +
        ' ｜ 隔日低點 ' + _price(getattr(forecast, "final_t1_low", None)) +
        '<br>' + _text(brief.get("staged_entry") or plan.get("display_line") or "進場條件尚待確認") + '</div>'
    )


def render_command(st, forecast, payload):
    st.markdown(command_html(forecast, payload), unsafe_allow_html=True)
    st.markdown('<div class="j-shell j-section"><span>分析工作區</span><span>完整資訊 · 隨時切換</span></div>', unsafe_allow_html=True)


WORKSPACES = ("交易計畫", "籌碼與事件", "模擬與財報學習", "深度報告", "完整雙欄")


def render_workspace_picker(st):
    return st.radio("分析工作區", WORKSPACES, index=4, horizontal=True,
                    label_visibility="collapsed", key="jarvis_workspace_v1118")
