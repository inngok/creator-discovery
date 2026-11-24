import os
import unittest
from unittest.mock import MagicMock, patch

os.environ["TESTING"] = "1"
os.environ["DISABLE_CRAWL_PACING"] = "1"

from app.crawlers.instagram_discovery import (
    _clean_instagram_username,
    _extract_candidates_from_search_html,
    search_new_instagram_candidates,
)
from app.workers.tasks import weekly_discover_new_creators_task


class TestInstagramDiscovery(unittest.TestCase):
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
