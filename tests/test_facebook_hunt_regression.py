import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from app.crawlers.facebook_crawler import parse_stat_number, extract_facebook_followers
from app.services.category_discovery_service import CategoryDiscoveryService

class FacebookHuntRegressionTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.history_path = Path(directory.name) / "results.jsonl"
        history = patch("app.services.crawled_result_service.RESULTS_FILE_PATH", str(self.history_path))
        history.start()
        self.addCleanup(history.stop)

    @patch("app.crawlers.discovery_crawler.sync_playwright")
    @patch("app.crawlers.facebook_crawler._init_facebook_playwright_context")
    def test_previous_hunt_profile_does_not_fill_new_hunt_quota(self, initialize, playwright):
        from unittest.mock import MagicMock
        from app.crawlers.discovery_crawler import search_new_facebook_candidates
        self.history_path.write_text(json.dumps({"creator": {
            "platform": "FACEBOOK", "username": "thiphuong.hoang.5",
            "externalId": "123456", "profileUrl": "https://www.facebook.com/thiphuong.hoang.5"}}), encoding="utf-8")
        context = MagicMock()
        initialize.return_value = (context, "unused")
        page = context.new_page.return_value
        page.url = "https://www.facebook.com/hashtag/food"
        page.goto.return_value.status = 200
        page.content.return_value = '''<div role="article"><h2><a href="/thiphuong.hoang.5">Old</a></h2></div>
            <div role="article"><h2><a href="/profile.php?id=123456">Same ID</a></h2></div>
            <div role="article"><h2><a href="/new.food.creator">New</a></h2></div>'''
        with patch("app.crawlers.discovery_crawler.Stealth"), patch("app.crawlers.facebook_crawler.dismiss_facebook_dialogs"):
            rows = search_new_facebook_candidates(["food"], max_total=1)
        self.assertEqual([row["username"] for row in rows], ["new.food.creator"])
        page.goto.assert_called_once()

    @patch("app.crawlers.discovery_crawler.sync_playwright")
    @patch("app.crawlers.facebook_crawler._init_facebook_playwright_context")
    def test_closed_browser_retries_same_hashtag_with_fresh_context(self, initialize, playwright):
        from unittest.mock import MagicMock
        from app.crawlers.discovery_crawler import search_new_facebook_candidates
        broken, recovered = MagicMock(), MagicMock()
        initialize.side_effect = [(broken, "unused"), (recovered, "unused")]
        broken.new_page.return_value.goto.side_effect = RuntimeError(
            "Page.goto: Target page, context or browser has been closed")
        page = recovered.new_page.return_value
        page.url = "https://www.facebook.com/hashtag/food"
        page.goto.return_value.status = 200
        page.content.return_value = '<div role="article"><h2><a href="/food.creator">Food Creator</a></h2></div>'
        with patch("app.crawlers.discovery_crawler.Stealth"), patch("app.crawlers.facebook_crawler.dismiss_facebook_dialogs"):
            rows = search_new_facebook_candidates(["food"], max_total=1)
        self.assertEqual(rows[0]["username"], "food.creator")
        self.assertEqual(initialize.call_count, 2)
        self.assertEqual(broken.new_page.return_value.goto.call_args, page.goto.call_args)
        broken.browser.close.assert_called_once()
        recovered.browser.close.assert_called_once()

    @patch("app.crawlers.discovery_crawler.sync_playwright")
    @patch("app.crawlers.facebook_crawler._init_facebook_playwright_context")
    def test_closed_browser_retries_are_bounded(self, initialize, playwright):
        from unittest.mock import MagicMock
        from app.crawlers.discovery_crawler import search_new_facebook_candidates, FacebookDiscoveryError
        context = MagicMock()
        initialize.return_value = (context, "unused")
        context.new_page.return_value.goto.side_effect = RuntimeError(
            "Target page, context or browser has been closed")
        with patch("app.crawlers.discovery_crawler.Stealth"):
            with self.assertRaises(FacebookDiscoveryError):
                search_new_facebook_candidates(["food"])
        self.assertEqual(initialize.call_count, 3)

    def test_hashtag_extracts_authors_not_commenters_or_post_links(self):
        from app.crawlers.search_extractor import extract_facebook_hashtag_candidates
        html = '''<div role="article"><h2><a href="/food.creator?ref=feed">Food Creator</a></h2>
            <a href="/commenter">Commenter</a><a href="/food.creator/posts/123">Post</a></div>
            <div role="article"><h3><a href="/profile.php?id=123456">Another creator</a></h3></div>
            <div role="article"><h2><a href="https://evil.example/facebook.com/fake">Fake</a></h2></div>'''
        rows = extract_facebook_hashtag_candidates(html)
        self.assertEqual([row["username"] for row in rows], ["food.creator", "123456"])
        self.assertEqual(rows[1]["profile_url"], "https://www.facebook.com/profile.php?id=123456")
        self.assertTrue(all(row["followers"] == 0 for row in rows))

    @patch("app.crawlers.discovery_crawler.sync_playwright")
    @patch("app.crawlers.facebook_crawler._init_facebook_playwright_context")
    def test_public_hashtag_works_without_login_state(self, initialize, playwright):
        from unittest.mock import MagicMock
        from app.crawlers.discovery_crawler import search_new_facebook_candidates
        context = MagicMock()
        initialize.return_value = (context, "unused")
        page = context.new_page.return_value
        page.url = "https://www.facebook.com/hashtag/foodreview"
        page.goto.return_value.status = 200
        page.content.return_value = '<div role="article"><h2><a href="/food.creator">Food Creator</a></h2></div>'
        with patch("app.crawlers.discovery_crawler.Stealth"), patch("app.crawlers.facebook_crawler.dismiss_facebook_dialogs"):
            rows = search_new_facebook_candidates(["#food review"], max_total=1)
        self.assertEqual(rows[0]["username"], "food.creator")
        self.assertEqual(page.goto.call_args.args[0], "https://www.facebook.com/hashtag/foodreview")
        page.goto.assert_called_once()
        context.browser.close.assert_called_once()

    def test_stat_units_and_grouping(self):
        for text, expected in [("914", 914), ("914.000", 914000), ("914,000", 914000),
                               ("1.2M", 1200000), ("1,2 triệu", 1200000), ("12 nghìn", 12000),
                               ("8,5 K", 8500), ("2 tỷ", 2000000000), ("120 comments", 120)]:
            with self.subTest(text=text):
                self.assertEqual(parse_stat_number(text), expected)

    def test_followers_take_priority_over_likes(self):
        self.assertEqual(extract_facebook_followers("914 likes", "914 likes · 250K followers"), 250000)

    def test_vietnamese_followers_with_spaces_and_units(self):
        self.assertEqual(extract_facebook_followers("1,2 triệu người theo dõi", ""), 1200000)

    def test_likes_alone_do_not_become_followers(self):
        self.assertEqual(extract_facebook_followers("914 likes", "20 comments"), 0)

    @patch("app.crawlers.discovery_crawler.search_new_facebook_candidates")
    def test_facebook_uses_live_keyword_search_not_staging(self, search):
        search.return_value = [{"username": str(i), "platform": "FACEBOOK"} for i in range(3)]
        with patch.object(CategoryDiscoveryService, "_load_directory"):
            service = CategoryDiscoveryService()
        result = service.get_candidates_by_category("FOOD", limit=3, platform="FACEBOOK",
                                                     keywords=["#review đồ ăn", "food tour"])
        self.assertEqual(len(result), 3)
        search.assert_called_once_with(keywords=["review đồ ăn", "food tour"], max_per_keyword=3, max_total=3)

    @patch("app.crawlers.discovery_crawler.search_new_facebook_candidates", return_value=[])
    def test_empty_facebook_search_does_not_reuse_unrelated_staging(self, search):
        with patch.object(CategoryDiscoveryService, "_load_directory"):
            service = CategoryDiscoveryService()
        self.assertEqual(service.get_candidates_by_category("FOOD", platform="FACEBOOK"), [])

    @patch("app.crawlers.discovery_crawler.sync_playwright")
    @patch("app.crawlers.facebook_crawler._init_facebook_playwright_context")
    def test_search_http_error_is_not_reported_as_no_candidates(self, initialize, playwright):
        from unittest.mock import MagicMock
        from app.crawlers.discovery_crawler import search_new_facebook_candidates, FacebookDiscoveryError
        context = MagicMock()
        initialize.return_value = (context, "unused-state-path")
        context.new_page.return_value.goto.return_value.status = 404
        with patch("app.crawlers.discovery_crawler.Stealth"):
            with self.assertRaisesRegex(FacebookDiscoveryError, "HTTP 404"):
                search_new_facebook_candidates(["food"], max_total=3)
        context.browser.close.assert_called_once()
