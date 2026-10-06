import ast
import copy
import inspect
from pathlib import Path
from types import SimpleNamespace
import unittest

import ui_jarvis_v1117 as ui


def fixture():
    forecast = SimpleNamespace(stopped=False, ticker=SimpleNamespace(
        resolved_symbol="6770.TW", name="力積電", currency="TWD"),
        decision_card={"現價": 74.3, "資料標題": "收盤資料"},
        price_frame=SimpleNamespace(last=74.3, price_date="2026-10-06",
            truth=SimpleNamespace(source="TWSE", date="2026-10-06")),
        final_t1=75.1, final_t1_high=76.5, final_t1_low=72.9)
    payload = {"public_snapshot": {"label": "條件式風險觀察", "color": "yellow",
        "reason": "等待價格確認", "entry": {}}, "decision_brief": {
        "verdict": "條件未齊，保持觀察", "summary": "等待價格確認與量價結構完成。",
        "confidence_label": "中高", "entry_zone": "等待", "confirmation": "—",
        "breakout": "—", "invalidation": "72.37", "primary_risk": "法人／外資流向偏空",
        "staged_entry": "條件成立前，不建立新部位。"}}
    return forecast, payload


class UIContractTest(unittest.TestCase):
    def test_snapshot_is_read_only(self):
        f, p = fixture()
        before = copy.deepcopy((f.__dict__, p))
        ui.command_html(f, p)
        self.assertEqual(f.__dict__, before[0])
        self.assertEqual(p, before[1])

    def test_wait_does_not_become_raw_entry(self):
        f, p = fixture()
        f.low_entry = 66.6
        output = ui.command_html(f, p)
        self.assertIn("<strong>等待</strong>", output)
        self.assertNotIn("66.6", output)

    def test_escaped_external_content(self):
        f, p = fixture()
        f.ticker.name = '<script>alert(1)</script>'
        p["decision_brief"]["summary"] = '<img src=x onerror=alert(1)>'
        output = ui.command_html(f, p)
        self.assertNotIn("<script>", output)
        self.assertNotIn("<img", output)
        self.assertIn("&lt;script&gt;", output)

    def test_invalid_and_missing_price(self):
        f, p = fixture()
        for value in (None, float("nan"), float("inf"), 0, -1, "bad"):
            f.decision_card["現價"] = value
            f.price_frame.last = value
            self.assertIn('<div class="j-price">—', ui.command_html(f, p))

    def test_stopped_forecast_has_no_entry(self):
        f, p = fixture()
        f.stopped = True
        f.stop_reason = "價格驗證拒絕"
        output = ui.command_html(f, p)
        self.assertIn("價格驗證拒絕", output)
        self.assertNotIn("AI 建議進場區", output)

    def test_explicit_missing_card_price_is_not_replaced(self):
        f, p = fixture()
        f.decision_card["現價"] = None
        self.assertIn('<div class="j-price">—', ui.command_html(f, p))

    def test_missing_snapshot_never_claims_buy(self):
        f, _ = fixture()
        output = ui.command_html(f, None)
        self.assertIn("正式決策待確認", output)
        self.assertIn("等待條件確認", output)

    def test_us_currency_and_time_are_preserved(self):
        f, p = fixture()
        f.ticker = SimpleNamespace(resolved_symbol="MU", name="Micron", currency="USD")
        f.price_frame.price_date = "2026-10-05"
        output = ui.command_html(f, p)
        for text in ("MU", "Micron", "USD", "2026-10-05"):
            self.assertIn(text, output)

    def test_no_fake_target_or_simulated_fills(self):
        f, p = fixture()
        output = ui.command_html(f, p)
        self.assertIn("隔日預測收盤", output)
        self.assertNotIn("模擬成交", output)
        self.assertNotIn("T2", output)

    def test_ui_has_no_network_or_background_imports(self):
        tree = ast.parse(Path(ui.__file__).read_text())
        imported = {n.names[0].name.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.Import)}
        self.assertFalse(imported & {"requests", "yfinance", "threading", "pandas", "numpy"})
        self.assertIn("prefers-reduced-motion", ui.STYLE)

    def test_workspace_renders_only_requested_panel(self):
        app = ast.parse(Path(__file__).with_name("app.py").read_text())
        function = next(n for n in app.body if isinstance(n, ast.FunctionDef) and n.name == "_render_forecast")
        class Context:
            def __enter__(self): return self
            def __exit__(self, *args): pass
        class ST:
            selection = "交易計畫"
            def markdown(self, *args, **kwargs): pass
            def radio(self, *args, **kwargs): return self.selection
            def columns(self, *args, **kwargs): return Context(), Context()
            def container(self): return Context()
        st = ST()
        calls = []
        def battle(st, f, analysis_payload=None): calls.append("battle")
        namespace = {"st": st, "build_stock_analysis_payload": lambda f: fixture()[1],
            "render_battle_panel": battle, "render_radar": lambda *a: calls.append("radar"),
            "render_deep_report": lambda *a: calls.append("deep"), "inspect": inspect,
            "mark_runtime_stage": lambda *a, **k: None,
            "_log_exception": lambda *a: self.fail(str(a))}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "app.py", "exec"), namespace)
        expected = {"交易計畫": ["battle"], "籌碼與事件": ["radar"], "深度報告": ["deep"], "完整雙欄": ["battle", "radar", "deep"]}
        for workspace, panels in expected.items():
            st.selection = workspace
            calls.clear()
            namespace["_render_forecast"](fixture()[0])
            self.assertEqual(calls, panels)


if __name__ == "__main__":
    unittest.main()
