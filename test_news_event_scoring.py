# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from data_sources_tw import _score_news
from data_sources_us import _score_us_news


class NewsEventScoringTests(unittest.TestCase):
    def test_tw_forward_slowdown_outweighs_backward_beat(self):
        score, _tag = _score_news("台積電獲利創高但下一季需求放緩、毛利率下滑")
        self.assertLessEqual(score, -0.06)

    def test_tw_positive_forward_remains_positive(self):
        score, _tag = _score_news("台積電獲利優於預期並上調展望")
        self.assertGreater(score, 0.06)

    def test_us_forward_cut_outweighs_backward_beat(self):
        score, tag = _score_us_news(
            "TSMC earnings beat but cuts guidance as demand slows",
            "company",
        )
        self.assertLessEqual(score, -0.06)
        self.assertTrue(tag.startswith("bearish_"))

    def test_us_positive_forward_remains_positive(self):
        score, tag = _score_us_news("NVIDIA beats estimates and raises guidance", "company")
        self.assertGreater(score, 0.06)
        self.assertTrue(tag.startswith("bullish_"))


if __name__ == "__main__":
    unittest.main()
