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

    @patch("app.services.live_crawl_service.fetch_tiktok_ssr_raw_data")
    def test_test_scrape_success(self, mock_fetch):
        mock_fetch.return_value = {
            "__DEFAULT_SCOPE__": {
                "webapp.user-detail": {
                    "userInfo": {
                        "user": {
                            "id": "11223344",
                            "uniqueId": "test_beauty_koc",
                            "nickname": "Test Beauty KOC",
                            "signature": "Review mỹ phẩm xịn",
                            "verified": True,
                        },
                        "stats": {
                            "followerCount": 150000,
                            "followingCount": 200,
                            "heartCount": 1200000,
                            "videoCount": 80,
                        },
                    },
                    "itemList": [
                        {
                            "id": "v123",
                            "desc": "Top 5 son dưỡng môi tốt nhất",
                            "stats": {"playCount": 50000, "diggCount": 3500, "commentCount": 120, "shareCount": 45},
                            "createTime": 1700000000,
                        }
                    ],
                }
            }
        }

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

    @patch("app.services.live_crawl_service.fetch_tiktok_ssr_raw_data")
    def test_batch_crawl_by_category(self, mock_fetch):
        mock_fetch.return_value = {
            "__DEFAULT_SCOPE__": {
                "webapp.user-detail": {
                    "userInfo": {
                        "user": {
                            "id": "99887766",
                            "uniqueId": "sample_creator",
                            "nickname": "Sample Creator",
                            "signature": "Bio",
                            "verified": False,
                        },
                        "stats": {
                            "followerCount": 85000,
                            "followingCount": 100,
                            "heartCount": 500000,
                            "videoCount": 50,
                        },
                    },
                    "itemList": [],
                }
            }
        }

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

