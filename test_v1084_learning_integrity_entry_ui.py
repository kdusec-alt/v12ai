# -*- coding: utf-8 -*-
from pathlib import Path
from types import SimpleNamespace
import unittest

from learning_market_clock import target_trade_date_for_forecast


ROOT = Path(__file__).resolve().parent


class V1084LearningIntegrityEntryUiTests(unittest.TestCase):
    def test_us_intraday_t1_targets_next_market_session(self):
        forecast = SimpleNamespace(
            ticker=SimpleNamespace(market="US"),
            decision_card={"資料標題": "盤中資料"},
            data_truths=[SimpleNamespace(date="2026-07-31")],
        )
        self.assertEqual(target_trade_date_for_forecast(forecast), "2026-08-03")

    def test_us_premarket_t1_also_targets_next_market_session(self):
        forecast = SimpleNamespace(
            ticker=SimpleNamespace(market="US"),
            decision_card={"資料標題": "盤前資料"},
            data_truths=[SimpleNamespace(date="2026-07-30")],
        )
        self.assertEqual(target_trade_date_for_forecast(forecast), "2026-07-31")

    def test_entry_ui_has_one_current_action_and_five_unified_columns(self):
        source = (ROOT / "ui_v9_battle_panel.py").read_text(encoding="utf-8")
        self.assertIn("class='action-now'", source)
        self.assertIn("目前動作｜", source)
        self.assertIn("def _entry_map_tiles", source)
        for label in ("現在", "低接", "確認", "加碼", "失效"):
            self.assertIn(f'"label": "{label}"', source)
        self.assertIn("public_snapshot", source)
        self.assertIn("display_line", source)

    def test_entry_ui_keeps_legacy_fallback_and_v9_height(self):
        source = (ROOT / "ui_v9_battle_panel.py").read_text(encoding="utf-8")
        self.assertIn('entry.get("price_tiles")', source)
        self.assertIn("legacy_price_tiles", source)
        self.assertIn("html_block(html, height=642, scrolling=False)", source)


if __name__ == "__main__":
    unittest.main()
