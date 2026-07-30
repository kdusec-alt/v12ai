# -*- coding: utf-8 -*-
from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from event_reassessment import assess_event_delta
from models import NewsItem
from news_causal_intelligence_v1073 import select_effective_news_items
from news_timestamp_provenance_v1079 import (
    append_provenance_tag,
    assess_timestamp_provenance,
    guarded_score,
    timestamp_is_model_eligible,
)


class NewsTimestampProvenanceV1079Tests(unittest.TestCase):
    def test_force_times_old_news_is_stale_reindexed(self):
        html = """
        <script type="application/ld+json">
        {"@type":"NewsArticle","datePublished":"2026-04-22T05:30:00+08:00",
         "dateModified":"2026-04-22T06:00:00+08:00"}
        </script>
        """
        row = assess_timestamp_provenance(
            "Tue, 28 Jul 2026 21:33:00 GMT",
            "https://ec.ltn.com.tw/article/paper/1751888",
            html,
        )
        self.assertEqual(row.status, "stale_reindexed")
        self.assertFalse(row.model_eligible)
        self.assertTrue(row.publisher_published_at.startswith("2026-04-22T05:30"))
        self.assertEqual(guarded_score(0.18, row), 0.0)

    def test_same_day_publisher_date_is_verified(self):
        html = '<meta property="article:published_time" content="2026-07-28T05:30:00+08:00">'
        row = assess_timestamp_provenance(
            "Mon, 27 Jul 2026 21:33:00 GMT",
            "https://publisher.example/news/1",
            html,
        )
        self.assertEqual(row.status, "verified")
        self.assertTrue(row.model_eligible)
        self.assertEqual(guarded_score(-0.2, row), -0.2)

    def test_modified_date_alone_is_not_original_publication(self):
        html = '<meta property="article:modified_time" content="2026-07-28T05:30:00+08:00">'
        row = assess_timestamp_provenance(
            "Mon, 27 Jul 2026 21:33:00 GMT",
            "https://publisher.example/news/2",
            html,
        )
        self.assertEqual(row.status, "unverified")
        self.assertFalse(row.model_eligible)

    def test_unverified_google_news_is_blocked_everywhere(self):
        provenance = assess_timestamp_provenance(
            "Tue, 28 Jul 2026 21:33:00 GMT",
            "https://news.google.com/rss/articles/unresolved",
            "",
        )
        item = NewsItem(
            "GoogleNewsTW/自由時報",
            provenance.display_time,
            guarded_score(0.18, provenance),
            append_provenance_tag("tw_company_6770_earnings", provenance),
            "代工價格大漲 力積電Q2獲利估大成長",
            provenance.publisher_url,
        )
        self.assertFalse(timestamp_is_model_eligible(item))
        self.assertEqual(select_effective_news_items([item], {}), [])
        result = assess_event_delta(
            [], [item], now=datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Taipei"))
        )
        self.assertFalse(result["needs_reassessment"])
        self.assertEqual(result["new_event_count"], 0)


if __name__ == "__main__":
    unittest.main()
