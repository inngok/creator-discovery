import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.crawlers import tiktok_discovery as discovery


class TikTokDiscoveryOrderTest(unittest.TestCase):
    def test_tiktok_server_error_is_not_an_empty_successful_search(self):
        page = MagicMock()
        page.locator.return_value.inner_text.return_value = "Something went wrong. Try again"
        with self.assertRaises(discovery.TikTokDiscoveryError):
            discovery._ensure_search_available(page, SimpleNamespace(status=200))

    def search(self, pages, topic_match=lambda c, k: True, limit=1,
               keywords=None, indexed_results=None):
        page = MagicMock()
        page.content.side_effect = pages
        context = MagicMock()
        context.new_page.return_value = page
        with ExitStack() as stack:
            stack.enter_context(patch.object(discovery, "sync_playwright"))
            stack.enter_context(patch.object(discovery, "_init_discovery_context", return_value=(MagicMock(), context)))
            stack.enter_context(patch.object(discovery, "Stealth"))
            stack.enter_context(patch("app.services.crawled_result_service.get_crawled_tiktok_identifiers", return_value=set()))
            stack.enter_context(patch.object(discovery, "_trending_matches_topic", side_effect=topic_match))
            stack.enter_context(patch.object(discovery, "_run_profile_check", return_value=True))
            stack.enter_context(patch.object(discovery, "_search_indexed_tiktok_candidates",
                                            return_value=indexed_results or []))
            result = discovery.search_new_tiktok_candidates(keywords or ["travel"], max_total=limit)
        return result, page

    @staticmethod
    def card(user, caption):
        return f'<div><a href="/@{user}/video/123">{caption}</a></div>'

    def test_top_creator_is_read_before_any_scroll(self):
        results, page = self.search([self.card("topcreator", "Travel guide to the mountains")])
        self.assertEqual([c["username"] for c in results], ["topcreator"])
        page.mouse.wheel.assert_not_called()

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

    def test_empty_search_never_uses_explore(self):
        results, page = self.search(["", "", ""])
        self.assertEqual(results, [])
        self.assertEqual(page.goto.call_count, 1)
        self.assertIn("/search?q=", page.goto.call_args.args[0])

    def test_empty_search_uses_verified_index_results(self):
        results, _ = self.search(["", "", ""], indexed_results=[{"username": "verified"}])
        self.assertEqual(results, [{"username": "verified"}])

    def test_filtered_search_uses_verified_index_results(self):
        html = self.card("unrelated", "Unrelated video about a different topic")
        results, _ = self.search([html] * 3, topic_match=lambda c, k: False,
                                 indexed_results=[{"username": "verified"}])
        self.assertEqual(results, [{"username": "verified"}])

    def test_single_output_quota_still_checks_later_keywords(self):
        pages = ["".join(self.card(f"unrelated{kw}{i}", "Unrelated video caption")
                         for i in range(3)) for kw in range(3)]
        pages.append(self.card("matching", "Matching creator daily family vlog"))
        results, page = self.search(pages, keywords=["home", "health", "decor", "family"],
                                    topic_match=lambda c, k: c["username"] == "matching")
        self.assertEqual([c["username"] for c in results], ["matching"])
        self.assertEqual(page.goto.call_count, 4)

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
