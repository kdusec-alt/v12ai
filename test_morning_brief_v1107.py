# -*- coding: utf-8 -*-
from types import SimpleNamespace
import unittest

from morning_brief_v1107 import analyze_candidate_symbols, build_morning_brief_row
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
        self.assertIn("9/23", row["price_status"])
        self.assertIn("法人籌碼", row["narrative_evidence"])
        self.assertNotIn("rank", row)

    def test_unverified_fallback_price_is_not_presented_as_reference(self):
        good = build_morning_brief_row(self._forecast(code="3702", accepted=True))
        fallback = build_morning_brief_row(self._forecast(code="3045", accepted=False))
        self.assertIn("114.5", good["price_status"])
        self.assertIn("未驗證", fallback["price_status"])
        self.assertFalse(fallback["accepted_truth"])

    def test_one_fetch_failure_does_not_abort_or_reorder_other_candidates(self):
        def fetch_price(symbol):
            if symbol == "BAD":
                raise RuntimeError("source down")
            return symbol

        result = analyze_candidate_symbols(
            ["BAD", "GOOD"],
            price_fetcher=fetch_price,
            news_fetcher=lambda _symbol: [],
            orchestrator=lambda price, news_items: self._forecast(code=price),
        )
        self.assertEqual(len(result), 2)
        self.assertEqual([row["symbol"] for row in result], ["BAD", "GOOD"])
        failed = next(row for row in result if row["symbol"] == "BAD")
        self.assertEqual(failed["action"], "分析未完成")
        self.assertFalse(failed["accepted_truth"])
        self.assertTrue(all("rank" not in row for row in result))


if __name__ == "__main__":
    unittest.main()
