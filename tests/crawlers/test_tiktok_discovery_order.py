import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.crawlers import tiktok_discovery as discovery
from app.core.crawl_progress import (
    report_crawl_progress,
    reset_crawl_progress_callback,
    set_crawl_progress_callback,
)


class TikTokDiscoveryOrderTest(unittest.TestCase):
    def test_tiktok_server_error_is_not_an_empty_successful_search(self):
        page = MagicMock()
        page.locator.return_value.inner_text.return_value = "Something went wrong. Try again"
        with self.assertRaises(discovery.TikTokDiscoveryError):
            discovery._ensure_search_available(page, SimpleNamespace(status=200))

    def search(self, pages, topic_match=lambda c, k: True, limit=1,
               keywords=None):
        page = MagicMock()
        page.content.side_effect = pages
        context = MagicMock()
        context.new_page.return_value = page
        with ExitStack() as stack:
            stack.enter_context(patch.object(discovery, "sync_playwright"))
            stack.enter_context(patch.object(discovery, "_init_discovery_context", return_value=(MagicMock(), context)))
            stack.enter_context(patch.object(discovery, "Stealth"))
            stack.enter_context(patch.object(discovery, "_open_tiktok_discovery_page",
                                             side_effect=lambda page, keyword: page.goto(
                                                 "https://www.tiktok.com/explore" if keyword is None else "UI_SEARCH:" + keyword)))
            stack.enter_context(patch("app.services.crawled_result_service.get_crawled_tiktok_identifiers", return_value=set()))
            stack.enter_context(patch.object(discovery, "_trending_matches_topic", side_effect=topic_match))
            stack.enter_context(patch.object(discovery, "_run_profile_check", return_value=True))
            result = discovery.search_new_tiktok_candidates(
                keywords if keywords is not None else ["travel"], max_total=limit)
        return result, page

    @staticmethod
    def card(user, caption):
        return f'<div><a href="/@{user}/video/123">{caption}</a></div>'

    def test_top_creator_is_read_before_any_scroll(self):
        results, page = self.search([self.card("topcreator", "Travel guide to the mountains")])
        self.assertEqual([c["username"] for c in results], ["topcreator"])
        page.mouse.wheel.assert_not_called()

    def test_search_uses_the_homepage_search_input(self):
        page = MagicMock()
        discovery._open_tiktok_discovery_page(page, "review tech")
        page.goto.assert_called_once_with("https://www.tiktok.com/", wait_until="domcontentloaded", timeout=20000)
        search = page.locator.return_value.first
        search.fill.assert_called_once_with("review tech", timeout=10000)
        search.press.assert_called_once_with("Enter", timeout=10000)
        page.wait_for_url.assert_called_once_with("**/search**", wait_until="domcontentloaded", timeout=20000)

    def test_collapsed_search_is_opened_before_typing(self):
        page = MagicMock()
        page.locator.return_value.first.is_visible.return_value = False
        discovery._open_tiktok_discovery_page(page, "review tech")
        page.locator.return_value.click.assert_called_once_with(timeout=10000)
        page.locator.return_value.first.fill.assert_called_once_with("review tech", timeout=10000)

    def test_trending_opens_tiktok_explore(self):
        page = MagicMock()
        discovery._open_tiktok_discovery_page(page, None)
        page.goto.assert_called_once_with("https://www.tiktok.com/explore", wait_until="domcontentloaded", timeout=20000)
        page.locator.return_value.first.fill.assert_not_called()

    def test_keyword_search_reports_live_progress(self):
        events = []
        token = set_crawl_progress_callback(events.append)
        try:
            self.search([self.card("topcreator", "Travel guide to the mountains")])
        finally:
            reset_crawl_progress_callback(token)

        self.assertTrue(any(event["keyword"] == "travel" for event in events))
        self.assertTrue(any(event["stage"] == "search_complete" for event in events))

    def test_profile_check_thread_receives_live_progress_callback(self):
        events = []
        token = set_crawl_progress_callback(events.append)
        try:
            discovery._run_profile_check(report_crawl_progress, "profile checked")
        finally:
            reset_crawl_progress_callback(token)

        self.assertEqual([event["message"] for event in events], ["profile checked"])

    def test_top_user_card_is_not_skipped_for_video_results(self):
        html = ('<div data-e2e="search-user-card">'
                '<a href="/@toptraveler">Travel creator mountain guides</a></div>'
                + self.card("videoauthor", "Travel guide to the mountains"))
        results, page = self.search([html])
        self.assertEqual(results[0]["username"], "toptraveler")
        page.mouse.wheel.assert_not_called()

    def test_neighbor_caption_is_not_attributed_to_unrelated_author(self):
        html = ('<section>' + self.card("unrelated", "Hi")
                + self.card("traveler", "Travel guide to the mountains") + '</section>')
        candidates = discovery._extract_trending_authors(html)
        unrelated = next(c for c in candidates if c["username"] == "unrelated")
        self.assertEqual(unrelated["topic_evidence_text"], "Hi")

    def test_unrelated_card_does_not_consume_requested_count(self):
        results, _ = self.search([
            self.card("shop", "Unrelated commercial store video") +
            self.card("traveler", "Travel guide to the mountains")
        ], topic_match=lambda c, k: c["username"] == "traveler")
        self.assertEqual([c["username"] for c in results], ["traveler"])

    def test_empty_search_uses_tiktok_trending(self):
        results, page = self.search(["", "", "", self.card("trendingcreator", "Travel guide")])
        self.assertEqual(results[0]["username"], "trendingcreator")
        self.assertEqual(results[0]["source"], "TIKTOK_EXPLORE")
        self.assertEqual([c.args[0] for c in page.goto.call_args_list],
                         ["UI_SEARCH:travel", "https://www.tiktok.com/explore"])

    def test_no_keywords_reads_trending_without_searching(self):
        results, page = self.search([self.card("creator", "Daily creator video")], keywords=[])
        self.assertEqual(results[0]["source"], "TIKTOK_EXPLORE")
        self.assertEqual([call.args[0] for call in page.goto.call_args_list],
                         ["https://www.tiktok.com/explore"])

    def test_empty_native_sources_return_no_candidates(self):
        results, _ = self.search([""] * 6)
        self.assertEqual(results, [])

    def test_filtered_native_sources_return_no_candidates(self):
        html = self.card("unrelated", "Unrelated video about a different topic")
        results, _ = self.search([html] * 6, topic_match=lambda c, k: False)
        self.assertEqual(results, [])

    def test_single_output_quota_still_checks_later_keywords(self):
        pages = ["".join(self.card(f"unrelated{kw}{source}{i}", "Unrelated video caption")
                         for i in range(3)) for kw in range(3) for source in range(2)]
        pages.append(self.card("matching", "Matching creator daily family vlog"))
        results, page = self.search(pages, keywords=["home", "health", "decor", "family"],
                                    topic_match=lambda c, k: c["username"] == "matching")
        self.assertEqual([c["username"] for c in results], ["matching"])
        self.assertEqual(page.goto.call_count, 7)

    def test_topic_rejection_cannot_be_overridden_by_keyword_overlap(self):
        fake = SimpleNamespace(get_llm_enrichment=lambda _: {
            "matches": False, "creatorType": "USER", "topicEvidence": []})
        with patch.dict("sys.modules", {"app.ai.analyzer": fake}):
            self.assertFalse(discovery._trending_matches_topic(
                {"topic_evidence_text": "travel sale unrelated store"}, "travel"))

    def test_topic_match_requires_source_quote(self):
        fake = SimpleNamespace(get_llm_enrichment=lambda _: {
            "matches": True, "creatorType": "KOC", "topicEvidence": ["invented quote"]})
        with patch.dict("sys.modules", {"app.ai.analyzer": fake}):
            self.assertFalse(discovery._trending_matches_topic(
                {"topic_evidence_text": "Travel guide to the mountains"}, "travel"))
