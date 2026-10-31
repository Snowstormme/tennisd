import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from unittest.mock import Mock, patch

import requests

from tennisd import news_feed


class NewsCacheTests(unittest.TestCase):
    def setUp(self):
        with news_feed._NEWS_LOCK:
            news_feed._NEWS_CACHE.clear()
        self.now = datetime.now(timezone.utc)
        self.story = {"id": "saved-headline", "title": "A tour final", "published_at": self.now}

    def test_upstream_failure_keeps_last_success_and_reports_the_failure(self):
        with patch.object(news_feed, "_source_items", return_value=[self.story]):
            self.assertEqual(news_feed.fetch_news_items("atp", now=self.now), [self.story])
        with patch.object(news_feed, "_source_items", side_effect=requests.Timeout()):
            stories = news_feed.fetch_news_items("atp", now=self.now + timedelta(minutes=6))
        self.assertEqual(stories, [self.story])
        state = news_feed.news_feed_state("atp")
        self.assertEqual(state["failed_sources"], 1)
        self.assertEqual(state["last_success"], self.now)

    def test_background_refresh_returns_without_waiting_and_deduplicates_requests(self):
        started, release = Event(), Event()

        def slow_source(*_args):
            started.set()
            release.wait(2)
            return [self.story]

        with ThreadPoolExecutor(max_workers=1) as executor, patch.object(
            news_feed, "_NEWS_EXECUTOR", executor,
        ), patch.object(news_feed, "_source_items", side_effect=slow_source) as loader:
            try:
                self.assertEqual(news_feed.fetch_news_items("atp", now=self.now, background=True), [])
                self.assertTrue(started.wait(1))
                self.assertEqual(news_feed.fetch_news_items("atp", now=self.now, background=True), [])
                self.assertTrue(news_feed.news_feed_state("atp")["refreshing"])
                self.assertEqual(loader.call_count, 1)
            finally:
                release.set()
        self.assertFalse(news_feed.news_feed_state("atp")["refreshing"])
        self.assertEqual(news_feed.fetch_news_items("atp", now=self.now, background=True), [self.story])

    def test_failed_source_is_not_retried_on_every_page_view(self):
        with patch.object(news_feed, "_source_items", side_effect=requests.Timeout()):
            news_feed.fetch_news_items("atp", now=self.now)
        with patch.object(news_feed, "_source_items") as loader:
            news_feed.fetch_news_items("atp", now=self.now + timedelta(seconds=30), background=True)
            loader.assert_not_called()

    def test_direct_publisher_rss_accepts_only_the_publishers_links(self):
        response = Mock()
        response.content = b'''<rss xmlns:media="http://search.yahoo.com/mrss/"><channel>
          <item><title>A final</title><link>https://www.bbc.co.uk/sport/tennis/articles/example</link><pubDate>Sun, 01 Nov 2026 10:00:00 GMT</pubDate><media:thumbnail url="http://ichef.bbci.co.uk/example.jpg"/></item>
          <item><title>Unrelated</title><link>https://unrelated.example/article</link></item>
        </channel></rss>'''
        with patch.object(news_feed.requests, "get", return_value=response) as get:
            stories = news_feed._source_items("bbc", -1234)
        self.assertEqual(len(stories), 1)
        self.assertEqual(len(stories[0]["id"]), 64)
        self.assertEqual(stories[0]["image"], "https://ichef.bbci.co.uk/example.jpg")
        self.assertEqual(get.call_args.args[0], "https://feeds.bbci.co.uk/sport/tennis/rss.xml")


if __name__ == "__main__":
    unittest.main()
