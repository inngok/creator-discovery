import asyncio
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.core.models import CreatorInfo, IngestPayload, Platform, Post
from app.core.profile_identifiers import normalize_profile_identifier
from app.services import collection_service as service


class CollectionReliabilityTest(unittest.TestCase):
    def payload(self, platform=Platform.TIKTOK):
        return IngestPayload(creator=CreatorInfo(platform=platform, externalId="123",
            username="@creator", displayName="Creator", profileUrl="https://www.tiktok.com/@creator",
            followers=15000), recentPosts=[Post(platformPostId="456", postUrl="https://www.tiktok.com/@creator/video/456", caption="Review")], source="LIVE_TEST")

    def test_profile_links_and_wrong_domains(self):
        self.assertEqual(normalize_profile_identifier("FACEBOOK", "https://www.facebook.com/profile.php?id=123&foo=bar"), "123")
        self.assertEqual(normalize_profile_identifier("INSTAGRAM", "https://www.instagram.com/creator/?hl=vi"), "creator")
        for value in ["https://instagram.com.evil.test/creator", "https://instagram.com/p/123", ""]:
            with self.assertRaises(ValueError):
                normalize_profile_identifier("INSTAGRAM", value)

    def test_instagram_network_metrics_belong_to_their_own_author(self):
        from app.crawlers.instagram_timeline import extract_owned_media
        payload = {"data": {"user": {"username": "creator", "edge_owner_to_timeline_media": {"edges": [
            {"node": {"shortcode": "first", "taken_at_timestamp": 1700000000, "edge_media_preview_like": {"count": 17}, "edge_media_to_caption": {"edges": [{"node": {"text": "My review"}}]}}},
            {"node": {"shortcode": "second", "taken_at_timestamp": 1700000010, "edge_media_preview_like": {"count": 99}}},
            {"node": {"shortcode": "other", "taken_at_timestamp": 1700000010, "owner": {"username": "someone_else"}, "like_count": 999999}}
        ]}}}}
        posts = extract_owned_media(payload, "creator")
        self.assertEqual([(p.platformPostId, p.likes) for p in posts], [("first", 17), ("second", 99)])
        self.assertEqual(posts[0].caption, "My review")
        self.assertIsNone(posts[0].views)

    def test_saved_session_does_not_change_public_collection_mode(self):
        with tempfile.TemporaryDirectory() as root, patch.object(service, "DATA_DIR", Path(root)):
            (Path(root) / "playwright_fb_state.json").write_text(json.dumps({"cookies": [
                {"name": "datr", "domain": ".facebook.com", "expires": -1}]}))
            status = service.session_status("FACEBOOK")
            self.assertEqual(status["status"], "PUBLIC_ACCESS_UNVERIFIED")
            self.assertFalse(status["requires_login"])
            self.assertFalse(status["uses_saved_session"])

    def test_all_crawlers_ignore_saved_sessions_and_never_persist_them(self):
        from unittest.mock import MagicMock
        from app.crawlers.facebook_crawler import _init_facebook_playwright_context
        from app.crawlers.instagram_crawler import _init_instagram_playwright_context
        for initializer in (_init_facebook_playwright_context, _init_instagram_playwright_context):
            with self.subTest(initializer=initializer.__name__), patch("builtins.open", side_effect=AssertionError("Must not read cookie files")):
                playwright = MagicMock()
                context, state_path = initializer(playwright)
                self.assertIsNone(state_path)
                self.assertNotIn("storage_state", playwright.chromium.launch.return_value.new_context.call_args.kwargs)
                context.add_cookies.assert_not_called()
                context.storage_state.assert_not_called()

    def test_public_context_failure_closes_browser(self):
        from app.crawlers.public_browser import create_public_context
        from unittest.mock import MagicMock
        playwright = MagicMock()
        browser = playwright.chromium.launch.return_value
        browser.new_context.side_effect = RuntimeError("cannot create context")
        with self.assertRaises(RuntimeError):
            create_public_context(playwright, "FACEBOOK")
        browser.close.assert_called_once()

    def test_instagram_empty_public_page_is_not_a_profile(self):
        from app.crawlers import instagram_crawler as crawler
        from unittest.mock import MagicMock
        context = MagicMock()
        page = context.new_page.return_value
        page.url = "https://www.instagram.com/creator/"
        page.title.return_value = "Instagram"
        page.content.return_value = "<html><body>Public content unavailable</body></html>"
        with patch.object(crawler, "sync_playwright"), patch.object(crawler, "_init_instagram_playwright_context", return_value=(context, None)), patch.object(crawler, "Stealth"):
            self.assertIsNone(crawler._crawl_instagram_profile_sync("creator"))
        context.browser.close.assert_called_once()

    def test_snapshots_preserve_time_and_handle_prefix(self):
        with tempfile.TemporaryDirectory() as root, patch.object(service, "DATA_DIR", Path(root)):
            payload = self.payload()
            service.save_snapshot(payload)
            cached = service.load_snapshot("TIKTOK", "creator", 24)
            self.assertEqual(cached.collectedAt, payload.collectedAt)
            payload.collectedAt = datetime.now(UTC) - timedelta(days=2)
            service.save_snapshot(payload)
            self.assertIsNone(service.load_snapshot("TIKTOK", "creator", 24))

    def test_cache_is_explicit_and_never_claims_live(self):
        with tempfile.TemporaryDirectory() as root, patch.object(service, "DATA_DIR", Path(root)), patch(
                "app.crawlers.tiktok_crawler.crawl_tiktok_profile", new=AsyncMock(side_effect=RuntimeError("blocked"))):
            service.save_snapshot(self.payload())
            result = asyncio.run(service.collect_profile("TIKTOK", "creator"))
            self.assertEqual(result["status"], "error")
            result = asyncio.run(service.collect_profile("TIKTOK", "creator", allow_cached=True))
            self.assertEqual(result["status"], "cached")
            self.assertFalse(result["is_live"])

    def test_partial_profile_is_not_cached_as_success(self):
        payload = self.payload()
        payload.recentPosts = []
        with patch("app.crawlers.tiktok_crawler.crawl_tiktok_profile", new=AsyncMock(return_value=payload)), patch.object(service, "save_snapshot") as save:
            result = asyncio.run(service.collect_profile("TIKTOK", "creator"))
            self.assertEqual(result["status"], "partial")
            save.assert_not_called()

    def test_all_platforms_continue_after_tiktok_failure(self):
        from app.services.category_discovery_service import CategoryDiscoveryService
        with patch("app.crawlers.discovery_crawler.search_new_tiktok_candidates", side_effect=RuntimeError("blocked")), patch(
                "app.crawlers.discovery_crawler.search_new_facebook_candidates", return_value=[{"username": "creator", "platform": "FACEBOOK"}]):
            self.assertEqual(CategoryDiscoveryService().get_candidates_by_category("TECH", limit=1)[0]["platform"], "FACEBOOK")

    def test_tiktok_blocked_search_uses_index_fallback(self):
        from app.crawlers import tiktok_discovery as discovery
        from unittest.mock import MagicMock
        context = MagicMock()
        context.new_page.return_value.goto.side_effect = discovery.TikTokDiscoveryError("blocked")
        with patch.object(discovery, "sync_playwright"), patch.object(discovery, "_init_discovery_context", return_value=(MagicMock(), context)), patch.object(
                discovery, "_search_indexed_tiktok_candidates", return_value=[{"username": "verified"}]) as fallback:
            result = discovery.search_new_tiktok_candidates(["review"], max_total=1)
            self.assertEqual(result[0]["username"], "verified")
            fallback.assert_called_once()

    def test_instagram_rejects_unverified_index_result(self):
        from app.crawlers import instagram_discovery as discovery
        from unittest.mock import MagicMock
        playwright = MagicMock()
        page = playwright.return_value.__enter__.return_value.chromium.launch.return_value.new_context.return_value.new_page.return_value
        page.content.return_value = '<div><a href="https://www.instagram.com/reviewer/">Reviewer</a><span>50K Followers</span></div>'
        with patch.object(discovery, "sync_playwright", playwright), patch.object(discovery, "_verify_instagram_candidate", return_value=False):
            self.assertEqual(discovery.search_new_instagram_candidates(["review"]), [])

    def test_keyword_only_batch_and_profile_error_isolation(self):
        from app.api.routers.crawl_router import BatchCrawlRequest, batch_crawl_kocs
        with patch("app.api.routers.crawl_router.category_discovery_service.get_candidates_by_category", return_value=[
                {"username": "bad", "platform": "INSTAGRAM"}, {"username": "good", "platform": "INSTAGRAM"}]) as search, patch(
                "app.api.routers.crawl_router.crawl_single_koc_live", side_effect=[RuntimeError("login"), {"status": "success"}]):
            result = asyncio.run(batch_crawl_kocs(BatchCrawlRequest(platform="INSTAGRAM", keywords=["review"], max_channels=2)))
            self.assertEqual(search.call_args.kwargs["keywords"], ["review"])
            self.assertEqual(result["summary"]["success_count"], 1)
            self.assertEqual(result["summary"]["failed_count"], 1)


if __name__ == "__main__":
    unittest.main()
