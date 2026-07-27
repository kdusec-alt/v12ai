# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
import unittest

import ui_v9_battle_panel as battle
import ui_v9_radar as radar


class ResponsiveUiV1065Tests(unittest.TestCase):
    def test_radar_removes_repeated_row_prefixes(self):
        value = "Daily Headline｜Daily Headline｜中｜風險偏高"
        self.assertEqual(
            radar._strip_duplicate_label("Daily Headline", value),
            "中｜風險偏高",
        )
        self.assertEqual(
            radar._strip_duplicate_label("Policy/Geo", "Policy/Geo | 高｜關稅觀察"),
            "高｜關稅觀察",
        )

    def test_normal_desktop_no_longer_uses_old_1100_stack_breakpoint(self):
        source = inspect.getsource(battle.render_battle_panel)
        self.assertIn("@media(max-width:1020px) and (min-width:721px)", source)
        self.assertIn("@media(max-width:720px)", source)
        self.assertNotIn("@media(max-width:1100px)", source)

    def test_left_and_right_iframes_use_aligned_height(self):
        battle_source = inspect.getsource(battle.render_battle_panel)
        radar_source = inspect.getsource(radar.render_radar)
        self.assertIn("html_block(html, height=642, scrolling=False)", battle_source)
        self.assertIn("html_block(html, height=642, scrolling=False)", radar_source)
        self.assertIn("min-height:624px", radar_source)


if __name__ == "__main__":
    unittest.main()
