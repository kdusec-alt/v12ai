# -*- coding: utf-8 -*-
from pathlib import Path
import unittest

from evidence_reasoning_v1082 import build_evidence_reasoning
from test_decision_architecture_v1081 import make_forecast


ROOT = Path(__file__).resolve().parent


class EvidenceReasoningV1082Tests(unittest.TestCase):
    def test_bad_fundamental_but_strong_price_is_negative_absorbed(self):
        forecast = make_forecast(
            last=106.0,
            day_pct=6.0,
            vwap=102.0,
            open_price=101.0,
            high=107.0,
            low=100.5,
            thesis_state="bad_news_absorbed",
            fundamental_text="財報低於預期｜EPS衰退｜來源：正式財報",
        )
        row = build_evidence_reasoning(forecast)
        self.assertEqual(row["price_acceptance"]["code"], "NEGATIVE_ABSORBED")
        self.assertIn("利空被價格吸收", row["decision_message"])
        self.assertIn(row["short_term_bias"], {"偏多", "中性偏多"})
        self.assertIn(row["medium_term_bias"], {"偏空", "中性偏空"})
        self.assertIn("主導：價格結構", row["headline"])

    def test_positive_fundamental_but_weak_price_is_rejected(self):
        forecast = make_forecast(
            last=96.0,
            day_pct=-3.0,
            vwap=100.0,
            open_price=101.0,
            high=102.0,
            low=95.5,
            fundamental_text="財報優於預期｜EPS成長｜來源：正式財報",
        )
        row = build_evidence_reasoning(forecast)
        self.assertEqual(row["price_acceptance"]["code"], "POSITIVE_REJECTED")
        self.assertIn("利多未被價格接受", row["decision_message"])
        self.assertIn(row["short_term_bias"], {"偏空", "中性偏空"})
        self.assertIn(row["medium_term_bias"], {"偏多", "中性偏多"})
        self.assertIn("主導：價格結構", row["headline"])

    def test_relative_weakness_outranks_supportive_market_background(self):
        forecast = make_forecast(
            last=96.0,
            day_pct=-2.0,
            vwap=100.0,
            open_price=100.0,
            high=101.0,
            low=95.5,
            evidence="盤中即時 TAIEX +6.00%｜SOX +8.00%｜NQ +4.00%",
            same_session=True,
        )
        row = build_evidence_reasoning(forecast)
        self.assertIn(row["short_term_bias"], {"偏空", "中性偏空"})
        self.assertEqual(row["dominant_category"], "price")
        self.assertIn("價格結構", row["top_driver_summary"])

    def test_unverified_event_cannot_become_dominant_driver_or_acceptance_catalyst(self):
        forecast = make_forecast(
            market="US",
            last=104.0,
            day_pct=4.0,
            vwap=101.0,
            evidence="官方市場傳聞重大訂單｜severity=4｜source_verified=0",
            event_verified=False,
            event_severity=4,
            same_session=True,
        )
        row = build_evidence_reasoning(forecast)
        event_rows = [item for item in row["top_drivers"] if item["category"] == "event"]
        self.assertTrue(all(not item["verified"] for item in event_rows))
        self.assertNotEqual(row["dominant_category"], "event")
        self.assertEqual(row["price_acceptance"]["code"], "UNRESOLVED")

    def test_research_growth_support_is_context_not_formal_catalyst(self):
        forecast = make_forecast(
            last=111.0,
            day_pct=9.36,
            vwap=104.0,
            fundamental_text="EPS 0.11｜成長支撐 -11｜研究模式，不介入決策｜來源 FinMind財報",
        )
        row = build_evidence_reasoning(forecast)
        fundamental = next(item for item in row["top_drivers"] if item["category"] == "fundamental")
        self.assertEqual(fundamental["stance"], "偏空")
        self.assertFalse(fundamental["verified"])
        self.assertEqual(row["price_acceptance"]["code"], "UNRESOLVED")
        self.assertEqual(row["medium_term_bias"], "中性／待確認")

    def test_verified_eps_yoy_decline_is_formal_negative_catalyst(self):
        forecast = make_forecast(
            last=111.0,
            day_pct=9.36,
            vwap=104.0,
            fundamental_text="EPS YoY -80%｜來源 FinMind財報",
        )
        row = build_evidence_reasoning(forecast)
        fundamental = next(item for item in row["top_drivers"] if item["category"] == "fundamental")
        self.assertEqual(fundamental["stance"], "偏空")
        self.assertTrue(fundamental["verified"])
        self.assertEqual(row["price_acceptance"]["code"], "NEGATIVE_ABSORBED")
        self.assertIn(row["medium_term_bias"], {"偏空", "中性偏空"})

    def test_numeric_institution_flow_is_parsed_without_ticker_rules(self):
        forecast = make_forecast(last=104.0, day_pct=4.0, vwap=101.0)
        forecast.radar["三大法人"] = "外資 今日 -1,198張｜投信 今日 +532張｜自營 今日 +381張｜來源 TWSE"
        row = build_evidence_reasoning(forecast)
        chip_rows = [item for item in row["top_drivers"] if item["category"] == "chip"]
        self.assertTrue(chip_rows)
        self.assertEqual(chip_rows[0]["stance"], "偏空")

    def test_limit_liquidity_reasoning_never_becomes_direct_buy_command(self):
        forecast = make_forecast(
            last=109.99,
            day_pct=9.99,
            vwap=107.0,
            open_price=109.99,
            high=109.99,
            low=109.99,
            limit_locked=None,
        )
        row = build_evidence_reasoning(forecast)
        self.assertEqual(row["entry_state"], "LIMIT_LIQUIDITY_WAIT")
        self.assertIn("成交條件", row["decision_message"])
        self.assertNotIn("直接買入", row["decision_message"])

    def test_only_three_unique_top_drivers_are_rendered(self):
        forecast = make_forecast(
            last=104.0,
            day_pct=4.0,
            vwap=101.0,
            evidence="官方重大事件已驗證｜event_verified=1｜SOX +5.0%｜NQ +3.0%",
            event_verified=True,
            event_severity=3,
            same_session=True,
            fundamental_text="財報優於預期｜來源：正式財報",
        )
        forecast.radar["三大法人"] = "外資買超｜來源：TWSE｜已驗證"
        row = build_evidence_reasoning(forecast)
        self.assertLessEqual(len(row["top_drivers"]), 3)
        self.assertEqual(len({item["category"] for item in row["top_drivers"]}), len(row["top_drivers"]))

    def test_reasoning_is_narrative_only_and_does_not_mutate_forecast(self):
        forecast = make_forecast(last=102.0, day_pct=2.0, vwap=100.0)
        original_t1 = getattr(forecast, "final_t1", None)
        row = build_evidence_reasoning(forecast)
        self.assertTrue(row["narrative_only"])
        self.assertFalse(row["decision_influence"])
        self.assertTrue(row["formal_forecast_unchanged"])
        self.assertTrue(row["learning_sample_unchanged"])
        self.assertEqual(getattr(forecast, "final_t1", None), original_t1)

    def test_module_contains_no_ticker_or_company_special_case(self):
        source = (ROOT / "evidence_reasoning_v1082.py").read_text(encoding="utf-8")
        self.assertNotIn("ticker ==", source)
        self.assertNotIn("resolved_symbol ==", source)
        for code in ("2330", "2308", "5483", "6217", "6770"):
            self.assertNotIn(code, source)


if __name__ == "__main__":
    unittest.main()
