import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from main import app


class TestLiveCrawlAndMetrics(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_get_metrics(self):
        res = self.client.get("/api/v1/monitor/metrics")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("total_requests", data)
        self.assertIn("rate_limit_429_count", data)
        self.assertIn("avg_latency_ms", data)

    def test_get_overview_has_metrics_and_stats(self):
        res = self.client.get("/api/v1/monitor/overview")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("stats", data)
        self.assertIn("metrics", data)
        self.assertIn("activities", data)
        self.assertIn("crawled_data", data)

    @patch("app.services.live_crawl_service.fetch_raw_creator_profile")
    def test_test_scrape_success(self, mock_fetch):
        mock_fetch.return_value = (
            {
                "author_id": "11223344",
                "unique_id": "test_beauty_koc",
                "display_name": "Test Beauty KOC",
                "followers": 150000,
                "following": 200,
                "heart_count": 1200000,
                "video_count": 80,
                "bio": "Review mỹ phẩm xịn",
                "avatar_url": "",
                "is_verified": True,
                "profile_url": "https://www.tiktok.com/@test_beauty_koc",
                "latency_ms": 100.0,
            },
            [
                {
                    "platformPostId": "v123",
                    "caption": "Top 5 son dưỡng môi tốt nhất",
                    "views": 50000,
                    "likes": 3500,
                    "comments": 120,
                    "shares": 45,
                    "postedAt": "2024-01-01T00:00:00Z",
                }
            ],
            None,
        )

        res = self.client.post("/api/v1/crawl/test-scrape", json={"profile_url": "test_beauty_koc", "platform": "TIKTOK"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["username"], "test_beauty_koc")
        self.assertEqual(data["followers"], 150000)
        self.assertEqual(len(data["recent_posts"]), 1)
        self.assertEqual(data["recent_posts"][0]["caption"], "Top 5 son dưỡng môi tốt nhất")

    @patch("app.api.routers.crawl_router.crawl_single_koc_live")
    @patch("app.api.routers.crawl_router.category_discovery_service.get_candidates_by_category")
    def test_partial_discovery_preserves_requested_count(self, discover, crawl):
        discover.return_value = [{"username": "food.creator", "platform": "FACEBOOK"}]
        crawl.return_value = {"status": "success", "username": "food.creator", "latency_ms": 1}
        response = self.client.post("/api/v1/crawl/batch-crawl", json={
            "platform": "FACEBOOK", "category": "FOOD", "max_channels": 3})
        self.assertEqual(response.status_code, 200)
        summary = response.json()["summary"]
        self.assertEqual(summary["total_requested"], 3)
        self.assertEqual(summary["attempted_count"], 1)
        self.assertEqual(summary["discovery_shortfall"], 2)
        self.assertEqual(summary["failed_count"], 0)

    @patch("app.api.routers.crawl_router.category_discovery_service.get_candidates_by_category", return_value=[])
    def test_empty_discovery_is_completed_not_reported_as_interrupted(self, discover):
        response = self.client.post("/api/v1/crawl/batch-crawl", json={
            "platform": "TIKTOK", "category": "LIFESTYLE", "max_channels": 1})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["summary"]["discovery_status"], "NO_MATCHES")
        self.assertEqual(data["summary"]["attempted_count"], 0)
        self.assertEqual(data["summary"]["failed_count"], 0)
        self.assertEqual(data["summary"]["discovery_shortfall"], 1)
        self.assertEqual(data["results"], [])

    @patch("app.api.routers.crawl_router.category_discovery_service.get_candidates_by_category")
    @patch("app.services.live_crawl_service.fetch_raw_creator_profile")
    def test_batch_crawl_by_category(self, mock_fetch, mock_discover):
        mock_discover.return_value = [
            {"username": "sample_creator_1", "platform": "TIKTOK"},
            {"username": "sample_creator_2", "platform": "TIKTOK"},
        ]
        mock_fetch.return_value = (
            {
                "author_id": "99887766",
                "unique_id": "sample_creator",
                "display_name": "Sample Creator",
                "followers": 85000,
                "following": 100,
                "heart_count": 500000,
                "video_count": 50,
                "bio": "Bio",
                "avatar_url": "",
                "is_verified": False,
                "profile_url": "https://www.tiktok.com/@sample_creator",
                "latency_ms": 120.0,
            },
            [],
            None,
        )

        res = self.client.post(
            "/api/v1/crawl/batch-crawl",
            json={"platform": "TIKTOK", "category": "BEAUTY", "max_channels": 2},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["category"], "BEAUTY")
        self.assertIn("summary", data)
        self.assertEqual(data["summary"]["total_requested"], 2)
        self.assertEqual(len(data["results"]), 2)


if __name__ == "__main__":
    unittest.main()

