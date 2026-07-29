import ast
from pathlib import Path
import unittest

from market_command_v1071 import assess_market_command


ROOT = Path(__file__).resolve().parent


class MarketCommandV1071Tests(unittest.TestCase):
    def test_bad_headline_alone_cannot_declare_crash(self):
        row = assess_market_command("US", {}, [{"tag": "severity=5", "title": "war"}])
        self.assertEqual(row["code"], "WAIT_CONFIRM")

    def test_broad_price_and_volatility_confirm_selloff(self):
        row = assess_market_command(
            "US",
            {"sox": -6.3, "nq": -3.0, "qqq": -2.8, "smh": -6.0, "vix": 29, "vix_change": 20},
            [{"tag": "shock_level=4", "title": "tariff escalation"}],
            {"市場風控": "偏空", "事件": "關稅"},
        )
        self.assertIn(row["code"], {"SELL_OFF", "CRASH"})
        self.assertTrue(row["price_confirmed"])
        self.assertGreaterEqual(row["radar_evidence_count"], 2)

    def test_missing_data_never_fabricates_precision(self):
        row = assess_market_command("TW", {"tx_night": None, "sox": None})
        self.assertEqual(row["code"], "WAIT_CONFIRM")
        self.assertEqual(row["facts"], [])

    def test_market_command_renderer_imports_html_escape(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_modules = {
            alias.name
            for node in tree.body
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        renderer = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_render_market_command"
        )
        renderer_source = ast.get_source_segment(source, renderer) or ""

        self.assertIn("html", imported_modules)
        self.assertIn("html.escape(", renderer_source)

    def test_market_command_is_independent_from_admin_event_watch(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        event_body = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_event_watch_fragment_body"
        )
        market_body = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_market_command_fragment_body"
        )
        main_body = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
        event_source = ast.get_source_segment(source, event_body) or ""
        market_source = ast.get_source_segment(source, market_body) or ""
        main_source = ast.get_source_segment(source, main_body) or ""

        self.assertNotIn("_render_market_command(", event_source)
        self.assertIn("_render_market_command(", market_source)
        self.assertIn("_market_command_fragment()", main_source)
        self.assertNotIn(
            'if bool(st.session_state.get("admin_authenticated", False)):\n'
            "            _market_command_fragment()",
            main_source,
        )

    def test_market_command_supports_us_market_family(self):
        row = assess_market_command(
            "US",
            {"sox": -2.0, "nq": -1.0, "qqq": -0.8, "vix": 21.0},
        )
        self.assertEqual(row["market"], "US")
        self.assertGreaterEqual(len(row["facts"]), 2)

    def test_coverage_is_not_presented_as_directional_hit_rate(self):
        row = assess_market_command(
            "TW",
            {"tx_night": -1.0, "tsm_adr": -1.2, "sox": -2.0, "nq": -0.8, "vix": 24.0},
            radar={"市場風控": "偏空"},
        )
        self.assertEqual(row["confidence_semantics"], "data_coverage_only")
        self.assertEqual(row["coverage"], row["confidence"])
        self.assertIn("跨市場", row["thesis"])

        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("市場資料覆蓋度", source)
        self.assertNotIn("方向判定可信度", source)
        radar_source = Path("ui_v9_radar.py").read_text(encoding="utf-8")
        self.assertIn("決策證據一致度", radar_source)
        self.assertNotIn("方向判定可信度", radar_source)


if __name__ == "__main__":
    unittest.main()
