import unittest

from ui_stock_analysis_table_v1113 import render_stock_analysis_table


class _FakeStreamlit:
    def __init__(self):
        self.calls = []

    def markdown(self, body, **kwargs):
        self.calls.append((body, kwargs))


class StockAnalysisTableV1112Tests(unittest.TestCase):
    def test_renders_unranked_four_column_row_and_escapes_values(self):
        st = _FakeStreamlit()
        render_stock_analysis_table(st, {
            "industry": "AI伺服器",
            "price_status": "9/23收盤 335.5元",
            "entry": "329～333止穩先1/3；站回338再加",
            "risk": "跌破325；ODM毛利與連假賣壓",
            "evidence": "AMD / AI推論伺服器連結較直接／中高 <風險>",
        }, symbol="2382.TW", name="廣達")
        body, kwargs = st.calls[0]
        for label in ("產業／價格狀態", "條件式進場與分批", "失效條件／主要風險", "證據"):
            self.assertIn(label, body)
        self.assertIn("2382.TW｜廣達", body)
        self.assertIn("AMD / AI推論伺服器連結較直接／中高 &lt;風險&gt;", body)
        self.assertNotIn("排名", body)
        self.assertTrue(kwargs["unsafe_allow_html"])


if __name__ == "__main__":
    unittest.main()
