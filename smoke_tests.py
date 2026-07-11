# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import os
os.environ.setdefault("TINO_OFFLINE_TEST", "1")
from ticker_resolver import resolve_ticker
from models import DataTruth, PriceFrame
from orchestrator import orchestrate
from data_sources import fetch_price, fetch_news
from config import FORBIDDEN_MAIN_UI_STRINGS


def assert_true(x, msg):
    if not x:
        raise AssertionError(msg)


def test_resolver():
    assert_true(resolve_ticker("2337").resolved_symbol == "2337.TW", "2337 resolver")
    assert_true(resolve_ticker("6770").resolved_symbol == "6770.TW", "6770 resolver")
    assert_true(resolve_ticker("2454").resolved_symbol == "2454.TW", "2454 resolver")
    assert_true(resolve_ticker("6586").resolved_symbol == "6586.TWO", "6586 resolver")
    assert_true(resolve_ticker("00919").asset_type == "etf", "00919 ETF Mode")
    assert_true(resolve_ticker("ONDS").market == "US", "ONDS US")


def test_zero_stop():
    t = resolve_ticker("6770")
    p = PriceFrame(t, DataTruth("unit", "2026-06-27", False, True, "unit"), 0, 0, 0, 0, 0, 0, 0, 0)
    f = orchestrate(p)
    assert_true(f.stopped, "0 price must STOP")
    assert_true(f.final_t1 is None, "STOP cannot create T1")


def test_six_tickers_run():
    for code in ["2337", "6770", "2454", "6586", "00919", "ONDS"]:
        p = fetch_price(code)
        f = orchestrate(p, news_items=fetch_news(code))
        assert_true(not f.stopped, f"{code} should run")
        assert_true(f.final_t1 is not None, f"{code} T1")
        assert_true(f.deep_report.count("【") >= 9, f"{code} deep V9 report")


def test_etf_guard():
    f = orchestrate(fetch_price("00919"), news_items=fetch_news("00919"))
    text = "\n".join([f.radar.get("BSI", ""), f.radar.get("基本面", ""), f.radar.get("資券 / 融資融券", "")])
    assert_true("ETF" in text, "ETF guard text")
    assert_true("個股財報" in text or "不套" in text, "ETF no stock fundamental")


def test_trace_rebuild():
    f = orchestrate(fetch_price("6770"))
    r = f.trace.reconstruct_final_t1()
    assert_true(abs(r - f.final_t1) < 0.011, f"trace rebuild {r} vs {f.final_t1}")
    for name in ["VWAP", "FQC", "LCR", "BSI", "RCRS", "法人", "資券", "Macro", "GRR"]:
        assert_true(any(s.name == name for s in f.trace.steps), f"trace missing {name}")


def test_macro_grr_price_neutral():
    f = orchestrate(fetch_price("6770"), manual_macro="bearish")
    for step in f.trace.steps:
        if step.name in {"Macro", "GRR", "事件", "財報"}:
            assert_true(abs(step.adjustment) < 1e-9, f"{step.name} must be price neutral")


def test_no_forbidden_ui_strings():
    for fn in ["app.py", "ui_v9_battle_panel.py", "ui_v9_radar.py"]:
        txt = Path(fn).read_text(encoding="utf-8")
        for bad in FORBIDDEN_MAIN_UI_STRINGS:
            assert_true(bad not in txt, f"forbidden main UI string {bad} in {fn}")



def test_no_shared_template_outputs():
    a = orchestrate(fetch_price("6770"), news_items=fetch_news("6770"))
    b = orchestrate(fetch_price("2337"), news_items=fetch_news("2337"))
    assert_true(a.radar.get("ABC 多空情境") != b.radar.get("ABC 多空情境"), "ABC cannot be identical template")
    assert_true("v12_core" in a.decision_card, "V12 core remains available for Admin/Trace")
    assert_true("新聞來源 / 外部事件" not in a.radar, "news source row must not appear in main radar")
    assert_true("籌碼 Proxy / 方向參考" not in a.radar, "proxy row must not appear in main radar")


def test_proxy_isolation_truth_guard():
    f = orchestrate(fetch_price("6770"), news_items=fetch_news("6770"))
    formal_keys = ["BSI 借券空方", "三大法人", "資券 / 融資融券"]
    formal_text = "\n".join(f.radar.get(k, "") for k in formal_keys)
    assert_true("V12_DERIVED_PROXY" not in formal_text, "proxy must not appear in official BSI/institutional/margin rows")
    assert_true(f.radar.get("三大法人", "").strip() != "", "institutional row must always carry tactical information")
    assert_true("待同步" not in f.radar.get("三大法人", ""), "institutional main row must not hide behind wait status")
    assert_true("待同步" not in f.radar.get("資券 / 融資融券", ""), "margin main row must not hide behind wait status")
    assert_true("籌碼 Proxy / 方向參考" not in f.radar, "proxy must be removed from main radar")


def test_finmind_institutional_parser_nonzero():
    import data_sources_tw as tw
    old = tw._finmind_query
    rows = [
        {"date": "2026-06-26", "stock_id": "6770", "name": "Foreign_Investor", "buy": 10000, "sell": 7000},
        {"date": "2026-06-26", "stock_id": "6770", "name": "Investment_Trust", "buy": 2000, "sell": 500},
        {"date": "2026-06-26", "stock_id": "6770", "name": "Dealer", "buy": 800, "sell": 1000},
        {"date": "2026-06-25", "stock_id": "6770", "name": "Foreign_Investor", "buy_sell": -1200},
        {"date": "2026-06-25", "stock_id": "6770", "name": "Investment_Trust", "buy_sell": 600},
        {"date": "2026-06-25", "stock_id": "6770", "name": "Dealer", "buy_sell": 300},
    ]
    try:
        tw._finmind_query = lambda dataset, stock_id, start, end=None: rows
        inst = tw._fetch_finmind_inst("6770.TW", "2026-06-29")
        assert_true(inst["accepted"] is True, "institutional parser should accept nonzero official rows")
        assert_true(inst["foreign"] == 3, "foreign net buy-sell parse as lots")
        assert_true(inst["trust"] == 2, "trust net buy-sell parse as lots")
        assert_true(inst["dealer"] == 0, "dealer net buy-sell parse as lots")
        assert_true(inst["date"] == "2026-06-26", "must use latest valid date, not today's missing date")
    finally:
        tw._finmind_query = old


def test_finmind_institutional_all_zero_rejected():
    import data_sources_tw as tw
    old = tw._finmind_query
    rows = [
        {"date": "2026-06-26", "name": "Foreign_Investor", "buy": 0, "sell": 0},
        {"date": "2026-06-26", "name": "Investment_Trust", "buy": 0, "sell": 0},
        {"date": "2026-06-26", "name": "Dealer", "buy": 0, "sell": 0},
    ]
    try:
        tw._finmind_query = lambda dataset, stock_id, start, end=None: rows
        try:
            tw._fetch_finmind_inst("6770.TW", "2026-06-29")
        except RuntimeError:
            return
        raise AssertionError("all-zero institutional data must be rejected as missing, not official zero")
    finally:
        tw._finmind_query = old


def test_clear_button_no_auto_research_guard():
    app_txt = Path("app.py").read_text(encoding="utf-8")
    input_txt = Path("ui_v9_input.py").read_text(encoding="utf-8")
    assert_true("suppress_auto_once" in app_txt, "clear must suppress auto analyze once")
    assert_true("input_was_cleared" in input_txt, "clear must keep input empty after rerun")
    assert_true("on_click=_clear_input_state" in input_txt, "clear must reset widget state before rebuild")
    assert_true("analyze and symbol" in app_txt, "manual analyze must require non-empty symbol")


def test_tv_foreign_pressure_formula():
    import data_sources_tw as tw
    import os
    old = tw._latest_two_or_three_closes
    old_env = os.environ.pop("TINO_OFFLINE_TEST", None)
    try:
        def fake(symbol, period="7d"):
            if symbol == "USDTWD=X":
                return [32.00, 32.10, 32.20], "2026-06-26"
            if symbol == "^TWII":
                return [23000.0, 22500.0, 21600.0], "2026-06-26"
            return [], ""
        tw._latest_two_or_three_closes = fake
        ctx = tw._tv_pressure_context("6770.TW", "2026-06-26", [80, 79], 78.9, 79.5, {}, previous_close=80.0, inst={"accepted": True, "foreign": -1000})
        assert_true(ctx["accepted"] is True, "TV foreign pressure formula should be accepted when FX and TAIEX are available")
        assert_true(ctx["direction"] == "預估大盤外資賣壓", "TV pressure must use market foreign sell pressure wording")
        assert_true(ctx["amount_billion"] == 5313, "0.10 USDTWD depreciation with -4% TAIEX should apply 4.25 crash boost")
        assert_true(ctx["source"] == "V9_TV_FX_DIFF_FORMULA", "TV pressure must not be proxy source")
    finally:
        tw._latest_two_or_three_closes = old
        if old_env is not None:
            os.environ["TINO_OFFLINE_TEST"] = old_env

def test_no_hardcoded_foreign_amount_80b():
    txt = Path("orchestrator.py").read_text(encoding="utf-8") + Path("data_sources_tw.py").read_text(encoding="utf-8")
    assert_true("今日預估外資賣超 80億" not in txt, "foreign amount must not use fixed 80億 fallback")
    assert_true("V9_FX_AMOUNT_PRESSURE_MEMORY" not in txt, "foreign amount must not come from fixed V9 memory")

def test_file_size_guard():
    # Legacy monoliths are technical debt inherited before V12.1.  Keep a hard
    # per-file budget so they cannot grow without an explicit refactor, while
    # all new modules remain under the normal 700-line limit.
    legacy_budgets = {
        "data_sources_tw.py": 1800,
        "tino_persistent_store.py": 1250,
        "orchestrator.py": 1250,
        "learning.py": 1050,
        "data_sources_us.py": 800,
    }
    for path in Path(".").glob("*.py"):
        lines = path.read_text(encoding="utf-8").splitlines()
        budget = legacy_budgets.get(path.name, 700)
        assert_true(len(lines) <= budget, f"{path} over line budget {budget}: {len(lines)} lines")


if __name__ == "__main__":
    tests = [test_resolver, test_zero_stop, test_six_tickers_run, test_etf_guard, test_trace_rebuild, test_macro_grr_price_neutral, test_no_forbidden_ui_strings, test_no_shared_template_outputs, test_proxy_isolation_truth_guard, test_finmind_institutional_parser_nonzero, test_finmind_institutional_all_zero_rejected, test_tv_foreign_pressure_formula, test_no_hardcoded_foreign_amount_80b, test_file_size_guard, test_clear_button_no_auto_research_guard]
    for t in tests:
        t()
        print(f"OK {t.__name__}")
    print("ALL TESTS OK")
