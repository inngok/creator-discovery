import os
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

os.environ["TESTING"] = "1"
os.environ["DISABLE_CRAWL_PACING"] = "1"

from app.crawlers.instagram_discovery import (
    _clean_instagram_username,
    _extract_candidates_from_search_html,
    search_new_instagram_candidates,
    _extract_instagram_hashtag_authors,
    _collect_instagram_hashtag_candidates,
    _instagram_hashtags,
)
from app.workers.tasks import weekly_discover_new_creators_task


class TestInstagramDiscovery(unittest.TestCase):
    def test_hashtag_login_redirect_is_not_an_empty_success(self):
        from app.crawlers.instagram_discovery import InstagramDiscoveryError
        page = MagicMock()
        page.goto.return_value.status = 200
        page.url = "https://www.instagram.com/accounts/login/"
        with self.assertRaises(InstagramDiscoveryError):
            _collect_instagram_hashtag_candidates(page, MagicMock(),
                                                  "https://www.instagram.com/explore/tags/food/", 1)

    @patch("app.crawlers.instagram_discovery._verify_instagram_candidate", return_value=False)
    @patch("app.crawlers.instagram_discovery._collect_instagram_hashtag_candidates")
    @patch("app.crawlers.instagram_discovery.sync_playwright")
    def test_hashtag_author_still_requires_profile_and_topic_verification(self, playwright, collect, verify):
        collect.return_value = [{"username": "unrelated", "followers": 0, "bio_snippet": ""}]
        page = playwright.return_value.__enter__.return_value.chromium.launch.return_value.new_context.return_value.new_page.return_value
        page.content.return_value = ""
        self.assertEqual(search_new_instagram_candidates(["food"], max_total=1), [])
        verify.assert_called_once()

    @patch("app.crawlers.instagram_discovery._verify_instagram_candidate", return_value=True)
    @patch("app.crawlers.instagram_discovery._collect_instagram_hashtag_candidates")
    @patch("app.crawlers.instagram_discovery.sync_playwright")
    def test_second_keyword_hashtag_precedes_external_fallback(self, playwright, collect, verify):
        collect.side_effect = [[], [{"username": "tech.creator", "followers": 0, "bio_snippet": ""}]]
        rows = search_new_instagram_candidates(["food", "tech"], max_total=1)
        self.assertEqual(rows[0]["username"], "tech.creator")
        self.assertEqual([call.args[2] for call in collect.call_args_list], [
            "https://www.instagram.com/explore/tags/food/",
            "https://www.instagram.com/explore/tags/tech/"])
        page = playwright.return_value.__enter__.return_value.chromium.launch.return_value.new_context.return_value.new_page.return_value
        page.goto.assert_not_called()

    def test_hashtags_keep_unicode_and_unaccented_variants(self):
        self.assertEqual(_instagram_hashtags("#đồ ăn"), ["đồăn", "doan"])

    def test_hashtag_reads_post_owners_and_headers_not_commenters(self):
        html = '''<article><header><a href="/food.creator/">Food creator</a></header>
            <a href="/p/ABC/">Post</a><a href="/commenter/">Commenter</a></article>
            <a href="/navigation/">Suggested</a>
            <script type="application/json">{"items":[{"code":"XYZ","user":{"username":"reel.creator"},
            "comments":[{"user":{"username":"commenter"}}]}]}</script>'''
        rows = _extract_instagram_hashtag_authors(html)
        self.assertEqual({r["username"] for r in rows}, {"food.creator", "reel.creator"})
        self.assertTrue(all(r["source"] == "INSTAGRAM_HASHTAG" for r in rows))

    def test_hashtag_grid_opens_post_to_resolve_owner(self):
        page, detail, context = MagicMock(), MagicMock(), MagicMock()
        page.url = "https://www.instagram.com/explore/tags/food/"
        page.goto.return_value.status = 200
        page.content.return_value = '<a href="/reel/ABC/">Video</a>'
        detail.goto.return_value.status = 200
        detail.content.return_value = '''<script type="application/json">{"code":"ABC",
            "owner":{"username":"food.creator"},"recommendation":{"code":"XYZ",
            "owner":{"username":"unrelated"}}}</script>'''
        context.new_page.return_value = detail
        rows = _collect_instagram_hashtag_candidates(page, context, page.url, 1)
        self.assertEqual([r["username"] for r in rows], ["food.creator"])
        self.assertEqual(detail.goto.call_args.args[0], "https://www.instagram.com/reel/ABC/")
        detail.close.assert_called_once()

    @patch("app.crawlers.instagram_discovery._verify_instagram_candidate", return_value=True)
    @patch("app.crawlers.instagram_discovery.sync_playwright")
    def test_native_hashtag_fills_quota_without_external_search(self, playwright, verify):
        page = playwright.return_value.__enter__.return_value.chromium.launch.return_value.new_context.return_value.new_page.return_value
        page.goto.return_value.status = 200
        page.url = "https://www.instagram.com/explore/tags/food/"
        page.content.return_value = '<article><header><a href="/food.creator/">Creator</a></header><a href="/p/ABC/">Post</a></article>'
        rows = search_new_instagram_candidates(["#food"], max_total=1)
        self.assertEqual(rows[0]["source"], "INSTAGRAM_HASHTAG")
        self.assertEqual(page.goto.call_args.args[0], page.url)
        page.goto.assert_called_once()
        verify.assert_called_once()

    def test_clean_instagram_username(self):
        self.assertEqual(_clean_instagram_username("linh_review"), "linh_review")
        self.assertEqual(_clean_instagram_username("@beauty.vietnam"), "beauty.vietnam")
        self.assertEqual(_clean_instagram_username("user_123/p/123"), "user_123")
        # Excluded / Reserved
        self.assertIsNone(_clean_instagram_username("explore"))
        self.assertIsNone(_clean_instagram_username("reels"))
        self.assertIsNone(_clean_instagram_username("p"))
        self.assertIsNone(_clean_instagram_username("stories"))
        self.assertIsNone(_clean_instagram_username("login"))
        # Invalid length
        self.assertIsNone(_clean_instagram_username("a"))
        self.assertIsNone(_clean_instagram_username("a" * 35))

    def test_extract_candidates_from_search_html(self):
        sample_html = """
        <html>
            <div>
                <h3><a href="https://www.instagram.com/linh_beauty_vn/">Linh Trải Nghiệm Mỹ Phẩm (@linh_beauty_vn) on Instagram</a></h3>
                <span>120K Followers, 450 Following, 320 Posts - Kênh review mỹ phẩm và chăm sóc da cá nhân chính hãng.</span>
            </div>
            <div>
                <h3><a href="/url?q=https://www.instagram.com/kho_si_gia_goc/">Tổng Kho Sỉ Giá Gốc (@kho_si_gia_goc)</a></h3>
                <span>3,500 người theo dõi. Chuyên sỉ lẻ quần áo hot trend xả kho giá rẻ toàn quốc ship COD.</span>
            </div>
            <div>
                <!-- Invalid link to explore/p -->
                <a href="https://www.instagram.com/explore/tags/beauty/">Explore Beauty</a>
                <a href="https://www.instagram.com/p/C312345678/">Post</a>
            </div>
        </html>
        """
        candidates = _extract_candidates_from_search_html(sample_html)
        self.assertEqual(len(candidates), 2)

        # Check candidate 1
        c1 = next(c for c in candidates if c["username"] == "linh_beauty_vn")
        self.assertEqual(c1["display_name"], "Linh Trải Nghiệm Mỹ Phẩm")
        self.assertEqual(c1["followers"], 120000)
        self.assertEqual(c1["platform"], "INSTAGRAM")
        self.assertIn("linh_beauty_vn", c1["profile_url"])

        # Check candidate 2
        c2 = next(c for c in candidates if c["username"] == "kho_si_gia_goc")
        self.assertEqual(c2["followers"], 3500)
        self.assertEqual(c2["platform"], "INSTAGRAM")

    @patch("app.crawlers.instagram_discovery._verify_instagram_candidate", return_value=True)
    @patch("app.crawlers.instagram_discovery.sync_playwright")
    def test_search_new_instagram_candidates(self, mock_playwright, mock_verify):
        mock_p = MagicMock()
        mock_playwright.return_value.__enter__.return_value = mock_p
        mock_browser = MagicMock()
        mock_p.chromium.launch.return_value = mock_browser
        mock_context = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_page = MagicMock()
        mock_context.new_page.return_value = mock_page

        mock_page.content.return_value = """
        <div>
            <a href="https://www.instagram.com/foodie_saigon/">Saigon Food Review (@foodie_saigon) on Instagram</a>
            <span>85K người theo dõi. Trải nghiệm ẩm thực đường phố Sài Gòn.</span>
        </div>
        <div>
            <!-- Commercial seller snippet that tier-0 should drop -->
            <a href="https://www.instagram.com/tong_kho_my_pham/">Tổng Kho Mỹ Phẩm (@tong_kho_my_pham)</a>
            <span>50K Followers. Chuyên sỉ lẻ mỹ phẩm giá xưởng ship cod toàn quốc.</span>
        </div>
        """

        candidates = search_new_instagram_candidates(
            keywords=["ẩm thực"], max_per_keyword=5, max_total=10
        )

        # Only foodie_saigon passes tier-0 filter (commercial seller dropped)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["username"], "foodie_saigon")
        self.assertEqual(candidates[0]["platform"], "INSTAGRAM")
        self.assertEqual(candidates[0]["followers"], 85000)

    @patch("app.crawlers.instagram_discovery._verify_instagram_candidate", return_value=True)
    @patch("app.crawlers.instagram_discovery.sync_playwright")
    def test_bing_fallback_recovers_when_google_and_duckduckgo_are_blocked(self, mock_playwright, mock_verify):
        playwright = mock_playwright.return_value.__enter__.return_value
        page = playwright.chromium.launch.return_value.new_context.return_value.new_page.return_value
        page.goto.side_effect = [
            SimpleNamespace(status=403),  # Native hashtag
            SimpleNamespace(status=403),
            SimpleNamespace(status=403),
            SimpleNamespace(status=200),
        ]
        page.content.return_value = """
        <div><a href="https://www.instagram.com/foodie_saigon/">Foodie Saigon</a>
        <span>85K followers. Vietnam food reviews.</span></div>
        """

        candidates = search_new_instagram_candidates(["food"], max_total=1)

        self.assertEqual([candidate["username"] for candidate in candidates], ["foodie_saigon"])
        self.assertIn("bing.com/search", page.goto.call_args_list[-1].args[0])
        mock_verify.assert_called_once()

    @patch("app.crawlers.discovery_crawler.search_new_instagram_candidates")
    @patch("app.workers.tasks.staging_repo.get_candidate")
    @patch("app.workers.tasks.preflight_candidate_task.delay")
    def test_weekly_discover_instagram_platform(self, mock_delay, mock_get_cand, mock_search_ig):
        mock_get_cand.return_value = None
        mock_search_ig.return_value = [
            {"username": "ig_koc_1", "followers": 25000, "platform": "INSTAGRAM"},
            {"username": "ig_koc_2", "followers": 60000, "platform": "INSTAGRAM"},
        ]

        summary = weekly_discover_new_creators_task(
            max_new_kocs=10, keywords=["review mỹ phẩm"], platform="INSTAGRAM"
        )

        self.assertEqual(summary["status"], "COMPLETED")
        self.assertEqual(summary["total_candidates_scanned"], 2)
        self.assertEqual(summary["new_kocs_enqueued"], 2)
        self.assertEqual(mock_delay.call_count, 2)
        mock_delay.assert_any_call("INSTAGRAM", "ig_koc_1")
        mock_delay.assert_any_call("INSTAGRAM", "ig_koc_2")


if __name__ == "__main__":
    unittest.main()
