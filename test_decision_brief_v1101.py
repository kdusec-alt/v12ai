# -*- coding: utf-8 -*-
from pathlib import Path
import unittest

from decision_brief_v1101 import build_decision_brief


ROOT = Path(__file__).resolve().parent


def snapshot(code="HOLD"):
    return {
        "action_code": code,
        "label": "條件式風險觀察",
        "instruction": "部位資料未知｜若有持股才依 96.15 管理；空手不進場",
        "reason": "價格尚未收復關鍵結構",
        "entry": {
            "low_entry_zone": {"lower": 97.2, "upper": 99.7},
            "confirmation_price": 103.5,
            "add_price": 106.8,
            "invalidation_price": 96.15,
        },
        "reasoning": {"action_decision": {
            "label": "條件式風險觀察",
            "reason": "價格尚未收復關鍵結構",
        }},
        "evidence": [
            {"label": "價格結構", "correlation_group": "price", "direction": -1, "strength": 92, "confidence": 90, "accepted": True, "reason": "現價仍在VWAP下方"},
            {"label": "VWAP位置", "correlation_group": "price", "direction": -1, "strength": 89, "confidence": 90, "accepted": True, "reason": "尚未站回VWAP"},
            {"label": "法人流向", "correlation_group": "chip", "direction": -1, "strength": 80, "confidence": 88, "accepted": True, "reason": "外資仍偏賣"},
            {"label": "跨市場", "correlation_group": "market", "direction": 1, "strength": 70, "confidence": 82, "accepted": True, "reason": "國際科技股偏強"},
        ],
    }


class DecisionBriefV1101Tests(unittest.TestCase):
    def test_keeps_one_reason_per_correlation_group(self):
        brief = build_decision_brief(snapshot())
        self.assertEqual(len(brief["reasons"]), 3)
        self.assertEqual(sum("價格" in row or "VWAP" in row for row in brief["reasons"]), 1)

    def test_never_invents_executable_prices(self):
        brief = build_decision_brief(snapshot())
        self.assertEqual(brief["entry_zone"], "97.20～99.70")
        self.assertEqual(brief["confirmation"], "103.5")
        self.assertEqual(brief["invalidation"], "96.15")
        self.assertTrue(brief["formal_model_unchanged"])

    def test_holding_and_flat_actions_are_separated(self):
        brief = build_decision_brief(snapshot())
        self.assertIn("先 1/3", brief["flat_action"])
        self.assertIn("96.15", brief["holding_action"])

    def test_next_session_candidate_is_actionable_without_relaxing_formal_gate(self):
        item = snapshot("BLOCK")
        item["entry"] = {
            "conditional_next_session": {
                "eligible": True,
                "label": "下一交易日條件候選",
                "entry_zone": {"lower": 97.2, "upper": 99.7},
                "entry_text": "回測區量縮止穩，先建立 1/3；未觸發不追價",
                "confirmation_price": 103.5,
                "confirmation_text": "站回確認價後再補 1/3",
                "breakout_price": 106.8,
                "breakout_text": "放量站穩突破價，才考慮最後 1/3",
                "invalidation_price": 96.15,
                "invalidation_text": "跌破失效價取消條件單，不攤平",
                "risk": "正式進場門檻尚未通過；這是下一交易日條件單，不是即時買進指令",
                "formal_gate_preserved": True,
            }
        }
        brief = build_decision_brief(item)
        self.assertTrue(brief["candidate_mode"])
        self.assertEqual(brief["verdict"], "下一交易日條件候選")
        self.assertEqual(brief["entry_zone"], "97.20～99.70")
        self.assertIn("1/3", brief["flat_action"])
        self.assertIn("不是即時買進", brief["summary"])

    def test_verified_formal_price_ladder_is_a_conditional_order(self):
        brief = build_decision_brief(snapshot("HOLD"))
        self.assertTrue(brief["candidate_mode"])
        self.assertEqual(brief["verdict"], "條件式候選｜等待觸發")
        self.assertIn("先 1/3", brief["flat_action"])
        self.assertEqual(brief["breakout"], "106.8")

    def test_v1105_builds_executive_thesis_risk_and_confidence(self):
        brief = build_decision_brief(snapshot("HOLD"), radar={
            "Company News": "Company News｜TEST｜中高｜產品/技術展示｜偏多事件｜新品獲客戶採用",
        })
        self.assertIn("主導", brief["thesis"])
        self.assertIn("公司催化", brief["thesis"])
        self.assertIn(brief["confidence_label"], {"高", "中高", "中", "低"})
        self.assertTrue(brief["primary_risk"])

    def test_v1106_price_leads_and_risk_contains_invalidation(self):
        brief = build_decision_brief(snapshot("HOLD"), radar={})
        self.assertIn("價格結構", brief["thesis"])
        self.assertIn("96.15", brief["primary_risk"])
        self.assertIn("取消條件單", brief["primary_risk"])
        self.assertIn("最後 1/3", brief["staged_entry"])
        self.assertEqual(brief["schema"], "TINO_DECISION_BRIEF_V1106")

    def test_single_price_zone_is_not_rendered_as_fake_range(self):
        item = snapshot("HOLD")
        item["entry"]["low_entry_zone"] = {"lower": 4889.0, "upper": 4889.0}
        item["entry"]["confirmation_price"] = 4945.0
        item["entry"]["add_price"] = 5040.0
        item["entry"]["invalidation_price"] = 4780.0
        brief = build_decision_brief(item)
        self.assertEqual(brief["entry_zone"], "4,889 附近")
        self.assertNotIn("4,889～4,889", brief["staged_entry"])

    def test_stale_data_notice_stays_out_of_executive_risk_and_top_three(self):
        item = snapshot("HOLD")
        item["evidence"].insert(0, {
            "label": "融資去槓桿", "correlation_group": "leverage",
            "direction": -1, "strength": 99, "confidence": 99,
            "accepted": True, "reason": "資券最近有效 09/18 非今日資料",
        })
        brief = build_decision_brief(item)
        self.assertNotIn("非今日資料", brief["primary_risk"])
        self.assertFalse(any("非今日資料" in row for row in brief["reasons"]))

    def test_macro_event_cannot_masquerade_as_company_catalyst(self):
        brief = build_decision_brief(snapshot("HOLD"), radar={
            "Company News": "Company News｜MRVL｜主事件《Global Event Core｜油價快速回落》",
        })
        self.assertIn("公司催化未驗證", brief["thesis"])
        self.assertNotIn("油價快速回落", brief["thesis"])

    def test_scenario_changes_with_price_chip_and_fresh_company_catalyst(self):
        item = snapshot("HOLD")
        item["evidence"] = [
            {"label": "價格／VWAP結構", "family": "price", "correlation_group": "price_structure",
             "direction": -1, "strength": 90, "confidence": 92, "accepted": True,
             "value": -3.0, "unit": "pct_above_vwap",
             "metadata": {"last": 97.0, "vwap": 100.0, "day_return_pct": -3.0},
             "reason": "現價在VWAP下方"},
            {"label": "法人流向", "family": "chip", "correlation_group": "tw_institutional",
             "direction": 1, "strength": 80, "confidence": 85, "accepted": True, "reason": "外資買超"},
            {"label": "融資去槓桿", "family": "leverage", "correlation_group": "tw_margin",
             "direction": 1, "strength": 70, "confidence": 80, "accepted": True, "reason": "融資下降"},
        ]
        brief = build_decision_brief(item)
        self.assertIn("價格仍弱", brief["thesis"])
        self.assertIn("法人承接", brief["thesis"])
        other = snapshot("HOLD")
        other["evidence"] = [
            {"label": "價格／VWAP結構", "family": "price", "correlation_group": "price_structure",
             "direction": 1, "strength": 90, "confidence": 92, "accepted": True,
             "value": 2.0, "unit": "pct_above_vwap",
             "metadata": {"last": 102.0, "vwap": 100.0, "day_return_pct": 1.2},
             "reason": "現價在VWAP上方"},
            {"label": "法人流向", "family": "chip", "correlation_group": "tw_institutional",
             "direction": -1, "strength": 80, "confidence": 85, "accepted": True, "reason": "外資賣超"},
            {"label": "新聞事件", "family": "event", "correlation_group": "verified_company_event",
             "direction": 1, "strength": 72, "confidence": 75, "accepted": True, "reason": "公司新品通過客戶認證"},
        ]
        second = build_decision_brief(other)
        self.assertIn("價格偏強但籌碼分歧", second["thesis"])
        self.assertNotEqual(brief["thesis"], second["thesis"])

    def test_unadjudicated_generic_verdict_still_has_unique_scenario(self):
        item = snapshot()
        item["evidence"][0]["family"] = "price"
        item["evidence"][0]["correlation_group"] = "price_structure"
        item["evidence"][0]["value"] = -2.0
        item["evidence"][0]["unit"] = "pct_above_vwap"
        item["evidence"][0]["metadata"] = {"last": 98, "vwap": 100, "day_return_pct": -2.5}
        brief = build_decision_brief(item)
        self.assertIn("價格走弱", brief["thesis"])
        self.assertIn("近3交易日無已仲裁", brief["thesis"])

    def test_ui_uses_brief_and_keeps_full_audit(self):
        source = (ROOT / "ui_v9_battle_panel.py").read_text(encoding="utf-8")
        self.assertIn("build_decision_brief", source)
        self.assertIn("AI執行計畫", source)
        self.assertIn("空手｜", source)
        self.assertIn("持股｜", source)
        self.assertIn("展開完整 AI 證據", source)
        self.assertIn("AI策略判斷", source)
        self.assertIn("失效／主要風險", source)
        self.assertIn("進場與分批", source)
        self.assertNotIn("目前動作｜最終決策｜", source)


if __name__ == "__main__":
    unittest.main()
