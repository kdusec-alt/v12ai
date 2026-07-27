# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


def _source(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _load_pure_function(filename: str, function_name: str):
    tree = ast.parse(_source(filename), filename=filename)
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == function_name
    )
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {}
    exec(compile(module, filename, "exec"), namespace)
    return namespace[function_name]


class ResponsiveUiV1065Tests(unittest.TestCase):
    def test_radar_removes_repeated_row_prefixes(self):
        strip_label = _load_pure_function("ui_v9_radar.py", "_strip_duplicate_label")
        self.assertEqual(
            strip_label("Daily Headline", "Daily Headline｜Daily Headline｜中｜風險偏高"),
            "中｜風險偏高",
        )
        self.assertEqual(
            strip_label("Policy/Geo", "Policy/Geo | 高｜關稅觀察"),
            "高｜關稅觀察",
        )

    def test_normal_desktop_no_longer_uses_old_1100_stack_breakpoint(self):
        source = _source("ui_v9_battle_panel.py")
        self.assertIn("@media(max-width:1020px) and (min-width:721px)", source)
        self.assertIn("@media(max-width:720px)", source)
        self.assertNotIn("@media(max-width:1100px)", source)

    def test_left_and_right_iframes_use_aligned_height(self):
        battle_source = _source("ui_v9_battle_panel.py")
        radar_source = _source("ui_v9_radar.py")
        self.assertIn("html_block(html, height=642, scrolling=False)", battle_source)
        self.assertIn("html_block(html, height=642, scrolling=False)", radar_source)
        self.assertIn("min-height:624px", radar_source)


if __name__ == "__main__":
    unittest.main()
