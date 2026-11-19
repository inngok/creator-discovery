import os
import unittest
from unittest.mock import MagicMock, patch

os.environ["TESTING"] = "1"
os.environ["DISABLE_CRAWL_PACING"] = "1"

from app.services.single_post_tracker_service import SinglePostTrackerService
from fastapi.testclient import TestClient
from main import app


class TestSinglePostTracker(unittest.TestCase):
    def setUp(self):
        self.service = SinglePostTrackerService()
        self.client = TestClient(app)

    def test_detect_platform(self):
        self.assertEqual(
            self.service.detect_platform("https://www.tiktok.com/@linh/video/12345"),
            "TIKTOK",
        )
        self.assertEqual(
            self.service.detect_platform("https://vt.tiktok.com/ZS2345/"),
            "TIKTOK",
        )
        self.assertEqual(
            self.service.detect_platform("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            "YOUTUBE",
        )
        self.assertEqual(
            self.service.detect_platform("https://youtu.be/dQw4w9WgXcQ"),
            "YOUTUBE",
        )
        self.assertEqual(
            self.service.detect_platform("https://www.instagram.com/reel/C312345/"),
            "INSTAGRAM",
        )
        self.assertEqual(
            self.service.detect_platform("https://www.facebook.com/watch/?v=12345"),
            "FACEBOOK",
        )
        self.assertEqual(
            self.service.detect_platform("https://twitter.com/post/123"),
            "UNKNOWN",
        )

    @patch("app.services.single_post_tracker_service.SinglePostTrackerService._fetch_tiktok_post")
    def test_fetch_post_metrics_router(self, mock_fetch_tiktok):
        mock_fetch_tiktok.return_value = {
            "status": "success",
            "platform": "TIKTOK",
            "postUrl": "https://www.tiktok.com/@linh/video/74382910291",
            "postId": "74382910291",
            "author": {"username": "linh", "displayName": "Linh Review"},
            "metrics": {
                "views": 150000,
                "likes": 12000,
                "comments": 450,
                "shares": 80,
                "engagementRate": 8.35,
            },
            "content": {
                "caption": "Review kem dưỡng ẩm cho da dầu mụn",
                "thumbnailUrl": "https://example.com/thumb.jpg",
                "postedAt": "2026-11-18T10:00:00Z",
            },
            "trackedAt": "2026-11-19T18:00:00Z",
        }

        res = self.client.post(
            "/api/v1/crawl/post-metrics",
            json={"post_url": "https://www.tiktok.com/@linh/video/74382910291"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["metrics"]["views"], 150000)
        self.assertEqual(data["metrics"]["likes"], 12000)
        self.assertEqual(data["author"]["username"], "linh")

    def test_unknown_platform_error(self):
        res = self.client.post(
            "/api/v1/crawl/post-metrics",
            json={"post_url": "https://unknown-site.com/video/123"},
        )
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
