import unittest
from unittest.mock import patch
from app.services.category_discovery_service import CategoryDiscoveryService


class ManagedCrawlCategoriesTest(unittest.TestCase):
    def setUp(self):
        self.service = CategoryDiscoveryService()

    @patch("app.crawlers.discovery_crawler.search_new_facebook_candidates")
    @patch("app.crawlers.discovery_crawler.search_new_tiktok_candidates")
    def test_legacy_category_alias_mapping(self, mock_tiktok, mock_fb):
        mock_tiktok.return_value = [{"username": "beauty_koc", "platform": "TIKTOK"}]
        mock_fb.return_value = []
        res = self.service.get_candidates_by_category("làm đẹp & mỹ phẩm", limit=1)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["username"], "beauty_koc")
        mock_tiktok.assert_called_once()
        self.assertIn("Làm đẹp", mock_tiktok.call_args.kwargs["keywords"])
        self.assertEqual(mock_tiktok.call_args.kwargs["requested_category"], "BEAUTY")

    @patch("app.crawlers.discovery_crawler.search_new_facebook_candidates")
    @patch("app.crawlers.discovery_crawler.search_new_tiktok_candidates")
    def test_category_code_lookup(self, mock_tiktok, mock_fb):
        mock_tiktok.return_value = [{"username": "tech_koc", "platform": "TIKTOK"}]
        mock_fb.return_value = []
        res = self.service.get_candidates_by_category("TECH", limit=1)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["username"], "tech_koc")

    @patch("app.crawlers.discovery_crawler.search_new_facebook_candidates")
    @patch("app.crawlers.discovery_crawler.search_new_tiktok_candidates")
    def test_custom_keywords_override(self, mock_tiktok, mock_fb):
        mock_tiktok.return_value = [{"username": "gaming_koc", "platform": "TIKTOK"}]
        mock_fb.return_value = []
        res = self.service.get_candidates_by_category("Custom", keywords=["gaming"], limit=1)
        self.assertEqual(len(res), 1)
        mock_tiktok.assert_called_once_with(
            keywords=["gaming"], max_per_keyword=1, max_total=1, requested_category="Custom"
        )

    def test_unsupported_platform_raises_error(self):
        with self.assertRaises(ValueError):
            self.service.get_candidates_by_category("TECH", platform="TWITTER")

    def test_limit_zero_returns_empty(self):
        self.assertEqual(self.service.get_candidates_by_category("TECH", limit=0), [])


if __name__ == "__main__":
    unittest.main()
