"""Independent, read-only risk explanation; never grants execution permission.
Weights are proposed research parameters, not a calibrated probability model.
"""
from collections.abc import Mapping
from datetime import date
import math

WEIGHTS = {"財報獲利品質": 30, "技術趨勢": 30, "估值合理性": 20, "產業與總體": 15, "資金與籌碼": 5}


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) and not isinstance(value, bool) else None
    except (ValueError, TypeError, OverflowError):
        return None


def mapping(value):
    return value if isinstance(value, Mapping) else {}


def fresh(report_date, reference):
    try:
        age = (date.fromisoformat(str(reference)[:10]) - date.fromisoformat(str(report_date)[:10])).days
        return 0 <= age <= 150
    except (ValueError, TypeError):
        return False


def assess(frame, price=None):
    """Use only supplied observations. Missing/stale data never becomes healthy."""
    ctx = mapping(getattr(frame, "context", {}))
    f = mapping(ctx.get("fundamental"))
    short = mapping(ctx.get("short"))
    reference = str(getattr(frame, "price_date", "") or "")
    last = number(price if price is not None else getattr(frame, "last", None))
    truth = getattr(frame, "truth", None)
    history = list(getattr(frame, "recent_closes", []) or [])
    closes = [number(x) for x in history]
    valid = bool(closes and all(x is not None and x > 0 for x in closes)
                 and getattr(truth, "accepted", False) and not getattr(truth, "fallback", True))
    ma20 = sum(closes[-20:]) / 20 if valid and len(closes) >= 20 else None
    ma60 = sum(closes[-60:]) / 60 if valid and len(closes) >= 60 else None
    below = last < ma60 if last and last > 0 and ma60 else None
    trend = "季線下方" if below else "季線上方" if below is False else "趨勢待確認"
    risks, recovery, checks = [], [], []
    for label, ma in (("月線 MA20", ma20), ("季線 MA60", ma60)):
        if last and last > 0 and ma:
            distance = (last / ma - 1) * 100
            checks.append(f"{label} {ma:.2f}｜距離 {distance:+.2f}%")
            if last < ma:
                risks.append(f"跌破{label} {ma:.2f}（{distance:+.2f}%）")
                recovery.append(f"正式盤收復{label} {ma:.2f}，並確認量能與後續站穩")
    report_date = f.get("quarterly_source_date")
    report_ok = bool(f.get("accepted") and fresh(report_date, reference))
    eps = number(f.get("gaap_eps"))
    margin = number(f.get("quarterly_gross_margin"))
    cash = number(f.get("quarterly_free_cashflow"))
    quality_ok = bool(report_ok and f.get("quality_period_consistent"))
    health = "待確認"
    if quality_ok and eps is not None and margin is not None and cash is not None:
        health = "強勁" if eps > 0 and margin > 0 and cash > 0 else "疲弱"
        if health == "疲弱":
            risks.append("最新同季 GAAP 獲利、毛利率或自由現金流非正值，需檢查虧損與融資需求")
            recovery.append("同季 GAAP 獲利、毛利率及自由現金流改善，並核對財測")
    if health == "待確認":
        risks.append("財報健康待確認，營收或 EPS 成長不能替代現金流檢驗")
    quadrants = {
        ("強勁", False): "成長與趨勢同步，仍須通過原交易條件",
        ("強勁", True): "基本面強、技術轉弱，等待修復",
        ("疲弱", False): "股價與基本面背離，留意估值及融資風險",
        ("疲弱", True): "基本面與技術雙弱，優先防守",
    }
    interpretation = quadrants.get((health, below), f"財報健康{health}；{trend}，避免把價格弱勢直接解讀為公司惡化")
    is_etf = f.get("asset_type") == "etf" or ctx.get("etf_mode")
    if is_etf:
        health = "不適用（ETF）"
        interpretation = f"ETF 不使用單一公司財報健康分類；{trend}"
        risks = [x for x in risks if not x.startswith("財報健康")]
    eps_kind = f.get("eps_kind") or "期間未確認"
    eps_basis = f.get("eps_basis") or "會計口徑未確認"
    checks.extend([
        f"價格參考日 {reference or '未知'}；均線使用正式日K，盤前價格僅供比較",
        f"財報期末 {report_date or '未知'}｜{'期間可用' if report_ok else '過期／缺漏／未驗證'}｜來源 {f.get('source') or '未知'}",
        f"EPS {f.get('eps') if number(f.get('eps')) is not None else '缺漏'}｜{eps_kind}｜{eps_basis}；不可直接年化季度 EPS",
        f"Forward PE {f.get('forward_pe') if number(f.get('forward_pe')) is not None else '缺漏'}｜預估年度與分母尚未核對，不據此判低估",
        "尚待核對：財測、負債與融資需求、歷史估值區間、利率、產業需求及 AI 資本支出；資料覆蓋率不代表資料正確",
    ])
    if quality_ok:
        checks.append(f"同季毛利率 {margin}%；自由現金流 {cash}（營業現金流減資本支出，財報幣別）")
    next_event = str(f.get("next_earnings") or "未取得確切日期")
    checks.append(f"下次財報 {next_event}；重新分析時以新財報重算，本模組沒有獨立排程")
    sf = number(short.get("short_float"))
    short_verified = bool(short.get("accepted") and fresh(short.get("date"), reference))
    if short_verified and sf is not None and sf >= 15:
        risks.append(f"放空占流通股 {sf:.2f}%，雙向波動風險較高")
    dimensions = [
        ("財報獲利品質", health),
        ("技術趨勢", trend),
        ("估值合理性", "待核對年度、預估 EPS 與歷史區間"),
        ("產業與總體", "參照既有大盤雷達；本模組尚未完成跨維度驗證"),
        ("資金與籌碼", f"放空比例 {sf:.2f}%（非相對強弱完整評分）" if short_verified and sf is not None else "資料日期／相對強弱待確認"),
    ]
    return {"version": "fundamental_technical_v1", "health": health, "below_ma60": below,
            "ma20": ma20, "ma60": ma60, "interpretation": interpretation,
            "risk": "｜".join(risks) or "暫無已確認新增風險；仍須遵守原交易風控",
            "recovery": "；".join(recovery) or "補齊財報、估值及趨勢證據後重評",
            "checks": checks, "dimensions": dimensions, "weights": dict(WEIGHTS),
            "risk_band": "中高（技術風險）" if below else "待確認（非低風險）",
            "execution_authority": False}
