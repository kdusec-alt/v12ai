# -*- coding: utf-8 -*-
from types import SimpleNamespace
import unittest

from morning_brief_v1107 import analyze_candidate_symbols, build_morning_brief_row, rank_morning_brief
from models import DataTruth, TickerInfo


class MorningBriefV1107Tests(unittest.TestCase):
    def _forecast(self, *, code="3702", action="WAIT", confidence=65.0, accepted=True):
        ticker = TickerInfo(code, f"{code}.TW", "大聯大", "TW", "stock")
        price = SimpleNamespace(
            ticker=ticker,
            context={"fundamental": {}, "persona": {}},
            truth=DataTruth("TWSE", "2026-09-23", False, accepted, "official close"),
            last=114.5,
            previous_close=114.0,
            price_date="2026-09-23",
        )
        entry = {
            "low_entry_zone": {"lower": 111.0, "upper": 113.5},
            "confirmation_price": 115.5,
            "add_price": 118.0,
            "invalidation_price": 108.5,
            "conditional_next_session": {},
        }
        evidence = [
            {"accepted": True, "verified": True, "family": "flow", "correlation_group": "flow", "label": "投信", "direction": 1, "strength": 80, "confidence": 90, "source": "MOPS", "reason": "連續買超"},
            {"accepted": True, "verified": True, "family": "price", "correlation_group": "price", "label": "量價", "direction": 1, "strength": 70, "confidence": 75, "source": "TWSE", "reason": "站回短均線"},
        ]
        snapshot = {
            "ticker": f"{code}.TW", "market": "TW", "action_code": action,
            "label": "條件成立，等待價格確認", "instruction": "不追高",
            "reason": "等待量價確認", "entry": entry,
            "reasoning": {"recommended_entry": entry, "action_decision": {"code": action, "label": "等待"}, "top_drivers": []},
            "evidence": evidence,
        }
        return SimpleNamespace(
            ticker=ticker, price_frame=price,
            decision_snapshot=SimpleNamespace(to_dict=lambda: snapshot),
            radar={}, final_t1=117.0, confidence=confidence, stopped=False,
        )

    def test_row_uses_the_existing_staged_plan_and_verified_evidence(self):
        row = build_morning_brief_row(self._forecast())
        self.assertIn("111", row["entry"])
        self.assertEqual(row["invalidation"], "108.5")
        self.assertEqual(row["industry"], "電子通路")
        self.assertIn("投信", row["evidence"])
        self.assertGreater(row["sort_score"], 0)

    def test_unverified_fallback_price_is_demoted(self):
        good = build_morning_brief_row(self._forecast(code="3702", accepted=True))
        fallback = build_morning_brief_row(self._forecast(code="3045", accepted=False))
        self.assertGreater(good["sort_score"], fallback["sort_score"])
        self.assertFalse(fallback["accepted_truth"])

    def test_rank_is_limited_to_six_and_only_ranks_supplied_pool(self):
        pool = [
            {"symbol": str(i), "accepted_truth": True, "sort_score": float(i), "confidence": 50}
            for i in range(10)
        ]
        result = rank_morning_brief(pool)
        self.assertEqual(len(result), 6)
        self.assertEqual(result[0]["symbol"], "9")
        self.assertEqual([row["rank"] for row in result], list(range(1, 7)))

    def test_one_fetch_failure_does_not_abort_other_candidates(self):
        def fetch_price(symbol):
            if symbol == "BAD":
                raise RuntimeError("source down")
            return symbol

        result = analyze_candidate_symbols(
            ["BAD", "GOOD"],
            price_fetcher=fetch_price,
            news_fetcher=lambda _symbol: [],
            orchestrator=lambda price, news_items: self._forecast(code="3702"),
        )
        self.assertEqual(len(result), 2)
        failed = next(row for row in result if row["symbol"] == "BAD")
        self.assertEqual(failed["action"], "分析未完成")
        self.assertFalse(failed["accepted_truth"])


if __name__ == "__main__":
    unittest.main()
