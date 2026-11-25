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

    def test_tiktok_discovery_keeps_uncertain_leads_for_manual_review(self):
        candidate = {"username": "creator"}
        with patch(
            "app.services.discovery_verification.verify_discovery_profile",
            return_value=True,
        ) as verify:
            result = discovery._verify_tiktok_candidate_followers(candidate)

        self.assertTrue(result)
        verify.assert_called_once_with(candidate, allow_review_candidate=True)

    def test_topic_verified_review_lead_carries_auto_accept_evidence(self):
        candidate = {
            "requires_manual_review": True,
            "creator_check": {"decision": "NEEDS_MANAGER_REVIEW", "reason_codes": []},
            "topic_check": {
                "creator_type": "KOC",
                "confidence": 0.9,
                "evidence": [{"postId": "1", "quote": "A verified creator quote"}],
            },
            "_verified_snapshot": {},
        }

        discovery._mark_topic_auto_accept(candidate)

        self.assertFalse(candidate["requires_manual_review"])
        self.assertEqual(candidate["creator_check"]["decision"], "AUTO_ACCEPT_KOC")
        self.assertEqual(
            candidate["_verified_snapshot"]["topic_auto_accept"]["creator_type"], "KOC"
        )

    def search(self, pages, topic_match=lambda c, k: True, limit=1,
               keywords=None, fallback_side_effect=None):
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
            stack.enter_context(patch.object(
                discovery,
                "_search_indexed_tiktok_candidates",
                side_effect=fallback_side_effect,
                **({"return_value": []} if fallback_side_effect is None else {}),
            ))
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
        page.wait_for_url.assert_called_once_with("**/search**", wait_until="domcontentloaded", timeout=5000)

    def test_collapsed_search_is_opened_before_typing(self):
        page = MagicMock()
        page.locator.return_value.first.is_visible.return_value = False
        discovery._open_tiktok_discovery_page(page, "review tech")
        page.locator.return_value.first.click.assert_called_once_with(timeout=10000)
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

    def test_empty_native_sources_try_indexed_search_fallback(self):
        page = MagicMock()
        page.content.side_effect = [""] * 6
        context = MagicMock()
        context.new_page.return_value = page
        fallback_candidate = {"username": "techcreator", "platform": "TIKTOK"}
        with ExitStack() as stack:
            stack.enter_context(patch.object(discovery, "sync_playwright"))
            stack.enter_context(patch.object(
                discovery, "_init_discovery_context", return_value=(MagicMock(), context)
            ))
            stack.enter_context(patch.object(discovery, "Stealth"))
            stack.enter_context(patch.object(discovery, "_open_tiktok_discovery_page"))
            stack.enter_context(patch(
                "app.services.crawled_result_service.get_crawled_tiktok_identifiers",
                return_value=set(),
            ))
            fallback = stack.enter_context(patch.object(
                discovery, "_search_indexed_tiktok_candidates", return_value=[fallback_candidate]
            ))
            results = discovery.search_new_tiktok_candidates(
                ["review công nghệ"], requested_category="TECH"
            )

        self.assertEqual(results, [fallback_candidate])
        fallback.assert_called_once_with(["review công nghệ"], 10, 30, "TECH")

    def test_indexed_search_extracts_and_verifies_public_tiktok_profile(self):
        html = '<div><a href="https://www.tiktok.com/@techcreator">Tech creator</a></div>'
        playwright = MagicMock()
        browser = MagicMock()
        page = MagicMock()
        playwright.chromium.launch.return_value = browser
        browser.new_page.return_value = page
        page.goto.return_value = None
        page.content.return_value = html

        with ExitStack() as stack:
            sync_playwright = stack.enter_context(patch.object(discovery, "sync_playwright"))
            sync_playwright.return_value.__enter__.return_value = playwright
            stack.enter_context(patch(
                "app.services.crawled_result_service.get_crawled_tiktok_identifiers",
                return_value=set(),
            ))
            stack.enter_context(patch.object(discovery, "_run_profile_check", return_value=True))
            stack.enter_context(patch.object(discovery, "_trending_matches_topic", return_value=True))
            results = discovery._search_indexed_tiktok_candidates(
                ["review công nghệ"], 1, 1, "TECH"
            )

        self.assertEqual(results[0]["username"], "techcreator")
        self.assertEqual(results[0]["source"], "SEARCH_INDEX_LIVE_VERIFIED")
        self.assertEqual(page.goto.call_args_list[0].args[0],
                         "https://www.tiktok.com/tag/reviewcongnghe")

    def test_indexed_search_failure_is_not_reported_as_no_results(self):
        playwright = MagicMock()
        browser = MagicMock()
        page = MagicMock()
        playwright.chromium.launch.return_value = browser
        browser.new_page.return_value = page
        page.goto.side_effect = RuntimeError("network unavailable")

        with ExitStack() as stack:
            sync_playwright = stack.enter_context(patch.object(discovery, "sync_playwright"))
            sync_playwright.return_value.__enter__.return_value = playwright
            stack.enter_context(patch(
                "app.services.crawled_result_service.get_crawled_tiktok_identifiers",
                return_value=set(),
            ))
            with self.assertRaisesRegex(discovery.TikTokDiscoveryError, "providers are unavailable"):
                discovery._search_indexed_tiktok_candidates(["tech"], 1, 1, "TECH")

    def test_topic_verification_unavailable_is_not_reported_as_no_results(self):
        with self.assertRaisesRegex(discovery.TikTokDiscoveryError, "AI unavailable"):
            self.search(
                [""] * 6,
                fallback_side_effect=discovery.TopicVerificationUnavailable("AI unavailable"),
            )

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

    def test_search_strips_leading_hash_before_typing(self):
        page = MagicMock()
        discovery._open_tiktok_discovery_page(page, "#review công nghệ")
        search = page.locator.return_value.first
        search.fill.assert_called_once_with("review công nghệ", timeout=10000)

    def test_explore_no_creator_evidence_lead_is_rejected_and_reported(self):
        from app.core.crawl_progress import (
            reset_discovery_rejection_callback,
            set_discovery_rejection_callback,
        )

        rejections = []
        token = set_discovery_rejection_callback(rejections.append)

        def fake_verify(candidate):
            candidate["creator_check"] = {
                "decision": "SKIP_INSUFFICIENT",
                "primary_type": "UNKNOWN",
                "reason_codes": ["NO_CREATOR_EVIDENCE"],
            }
            candidate["requires_manual_review"] = True
            candidate["_verified_snapshot"] = {
                "platform": "TIKTOK",
                "username": candidate["username"],
                "profile": {"unique_id": candidate["username"], "followers": 55000},
                "posts": [],
            }
            return True

        page = MagicMock()
        page.content.side_effect = [
            "", "", "",
            self.card("random_explore_channel", "Daily unrelated viral video"),
        ]
        context = MagicMock()
        context.new_page.return_value = page
        try:
            with patch.object(discovery, "sync_playwright"), \
                 patch.object(discovery, "_init_discovery_context", return_value=(MagicMock(), context)), \
                 patch.object(discovery, "Stealth"), \
                 patch.object(discovery, "_open_tiktok_discovery_page"), \
                 patch("app.services.crawled_result_service.get_crawled_tiktok_identifiers", return_value=set()), \
                 patch.object(discovery, "_search_indexed_tiktok_candidates", return_value=[]), \
                 patch.object(discovery, "_verify_tiktok_candidate_followers", side_effect=fake_verify):
                results = discovery.search_new_tiktok_candidates(["review công nghệ"], max_total=1, requested_category="TECH")
        finally:
            reset_discovery_rejection_callback(token)

        self.assertEqual(results, [])
        self.assertEqual([r["username"] for r in rejections], ["random_explore_channel"])
        self.assertEqual(rejections[0]["decision"], "AUTO_REJECT")
        self.assertFalse(rejections[0]["database_synced"])
