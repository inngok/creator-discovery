import os
import unittest
from unittest.mock import MagicMock, patch

os.environ["TESTING"] = "1"
os.environ["DISABLE_CRAWL_PACING"] = "1"

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import crawl_router
from app.core.post_metrics_models import MetricStatusEnum
from app.services.single_post_tracker_service import (
    MAX_SAFE_INTEGER,
    PostMetricsException,
    SinglePostTrackerService,
    single_post_tracker,
)

test_app = FastAPI()
test_app.include_router(crawl_router.router)


class TestSinglePostTracker(unittest.TestCase):
    def setUp(self):
        self.service = SinglePostTrackerService()
        self.client = TestClient(test_app)
        self.service.cache.clear()
        single_post_tracker.cache.clear()

    # =========================================================================
    # 1. PLATFORM DETECTION & URL NORMALIZATION
    # =========================================================================

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
            self.service.detect_platform("https://www.youtube.com/shorts/abc12345"),
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

    def test_tracking_url_preserves_post_url_and_normalizes_canonical(self):
        raw_url = "https://www.tiktok.com/@linh/video/74382910291?tracking=summer_campaign&utm_source=tiktok_ads&fbclid=abc"
        plat, canonical, post_id, author = self.service.validate_and_normalize_url(raw_url)
        self.assertEqual(plat, "TIKTOK")
        self.assertEqual(canonical, "https://www.tiktok.com/@linh/video/74382910291")
        self.assertEqual(post_id, "74382910291")
        self.assertEqual(author, "linh")

    # =========================================================================
    # 2. URL SAFETY & SSRF PROTECTION
    # =========================================================================

    def test_ssrf_and_invalid_urls_blocked(self):
        blocked_urls = [
            ("http://localhost/video/123", "INVALID_POST_URL", 400),
            ("http://127.0.0.1:8000/video/123", "INVALID_POST_URL", 400),
            ("http://169.254.169.254/latest/meta-data", "INVALID_POST_URL", 400),
            ("http://10.0.0.1/video/123", "INVALID_POST_URL", 400),
            ("http://192.168.1.1/video/123", "INVALID_POST_URL", 400),
            ("http://admin:secret@tiktok.com/video/123", "INVALID_POST_URL", 400),
            ("https://tiktok.com:8080/video/123", "INVALID_POST_URL", 400),
            ("https://google.com/video/123", "UNSUPPORTED_PLATFORM", 422),
            ("https://twitter.com/i/status/123456", "UNSUPPORTED_PLATFORM", 422),
            ("https://www.tiktok.com/about", "INVALID_POST_URL", 400),
            ("https://www.youtube.com/feed/trending", "INVALID_POST_URL", 400),
        ]

        for url, expected_code, expected_status in blocked_urls:
            res = self.client.post("/api/v1/crawl/post-metrics", json={"post_url": url})
            self.assertEqual(
                res.status_code,
                expected_status,
                f"Failed for URL: {url}, got status {res.status_code}",
            )
            data = res.json()
            self.assertEqual(data["schemaVersion"], "1.1")
            self.assertEqual(data["status"], "error")
            self.assertEqual(data["error"]["code"], expected_code)

    def test_empty_post_url(self):
        res = self.client.post("/api/v1/crawl/post-metrics", json={"post_url": ""})
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertEqual(data["error"]["code"], "INVALID_POST_URL")

    # =========================================================================
    # 3. SCHEMA 1.1 SUCCESS & COUNTER ZERO HANDLING
    # =========================================================================

    @patch("app.services.single_post_tracker_service.SinglePostTrackerService._fetch_tiktok_post")
    def test_fetch_post_metrics_router_success_complete(self, mock_fetch):
        mock_fetch.return_value = {
            "schemaVersion": "1.1",
            "status": "success",
            "platform": "TIKTOK",
            "postUrl": "https://www.tiktok.com/@linh/video/74382910291?tracking=ad",
            "canonicalPostUrl": "https://www.tiktok.com/@linh/video/74382910291",
            "postId": "74382910291",
            "author": {
                "externalId": "6631682124809076737",
                "username": "linh",
                "displayName": "Linh Review",
            },
            "metrics": {
                "views": 150000,
                "likes": 12000,
                "comments": 450,
                "shares": 80,
                "engagementRate": 8.35,
            },
            "metricStatus": {
                "views": "AVAILABLE",
                "likes": "AVAILABLE",
                "comments": "AVAILABLE",
                "shares": "AVAILABLE",
            },
            "metricErrors": {},
            "content": {
                "caption": "Review kem dưỡng ẩm cho da dầu mụn",
                "thumbnailUrl": "https://example.com/thumb.jpg",
                "postedAt": "2026-11-18T10:00:00Z",
            },
            "trackedAt": "2026-11-22T01:00:00Z",
            "servedAt": "2026-11-22T01:02:00Z",
            "collection": {
                "source": "PUBLIC_PAGE",
                "method": "SSR",
                "cacheHit": False,
                "scope": "LIFETIME_AT_COLLECTION",
                "engagementRateFormula": "(likes + comments + shares) / views * 100",
                "engagementRateUnit": "PERCENT",
            },
            "warnings": [],
        }

        res = self.client.post(
            "/api/v1/crawl/post-metrics",
            json={"post_url": "https://www.tiktok.com/@linh/video/74382910291?tracking=ad"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["schemaVersion"], "1.1")
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["platform"], "TIKTOK")
        self.assertEqual(data["postUrl"], "https://www.tiktok.com/@linh/video/74382910291?tracking=ad")
        self.assertEqual(data["canonicalPostUrl"], "https://www.tiktok.com/@linh/video/74382910291")
        self.assertEqual(data["postId"], "74382910291")
        self.assertEqual(data["metrics"]["views"], 150000)
        self.assertEqual(data["metrics"]["likes"], 12000)
        self.assertEqual(data["metricStatus"]["views"], "AVAILABLE")
        self.assertEqual(data["metricStatus"]["likes"], "AVAILABLE")

    def test_counter_zero_is_available_and_distinct_from_missing(self):
        """
        Kiểm tra số 0 thực tế là AVAILABLE, khác với null FETCH_FAILED/NOT_SUPPORTED.
        Khi thiếu dữ liệu thành phần, engagementRate phải null.
        """
        payload = self.service.build_success_payload(
            platform="TIKTOK",
            post_url="https://www.tiktok.com/@example/video/123?tracking=test",
            canonical_post_url="https://www.tiktok.com/@example/video/123",
            post_id="123",
            author={"username": "example", "displayName": "Example"},
            views=1000,
            likes=0,  # 0 thực tế từ nguồn
            comments=None,
            shares=None,
            views_status=MetricStatusEnum.AVAILABLE,
            likes_status=MetricStatusEnum.AVAILABLE,
            comments_status=MetricStatusEnum.FETCH_FAILED,
            shares_status=MetricStatusEnum.NOT_SUPPORTED,
            caption="Test video caption",
        )

        self.assertEqual(payload["schemaVersion"], "1.1")
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["metrics"]["views"], 1000)
        self.assertEqual(payload["metrics"]["likes"], 0)
        self.assertIsNone(payload["metrics"]["comments"])
        self.assertIsNone(payload["metrics"]["shares"])
        self.assertEqual(payload["metricStatus"]["views"], "AVAILABLE")
        self.assertEqual(payload["metricStatus"]["likes"], "AVAILABLE")
        self.assertEqual(payload["metricStatus"]["comments"], "FETCH_FAILED")
        self.assertEqual(payload["metricStatus"]["shares"], "NOT_SUPPORTED")
        self.assertIn("comments", payload["metricErrors"])
        self.assertEqual(payload["metricErrors"]["comments"]["code"], "COUNTER_UNAVAILABLE")
        # ER phải là None vì thiếu comments và shares
        self.assertIsNone(payload["metrics"]["engagementRate"])
        self.assertIsNone(payload["collection"]["engagementRateFormula"])

    def test_safe_integer_limit(self):
        """Kiểm tra giới hạn số nguyên an toàn JSON 9007199254740991."""
        payload = self.service.build_success_payload(
            platform="TIKTOK",
            post_url="https://www.tiktok.com/@user/video/123",
            canonical_post_url="https://www.tiktok.com/@user/video/123",
            post_id="123",
            views=999999999999999999,  # Quá giới hạn
            likes=50,
            comments=10,
            shares=5,
            views_status=MetricStatusEnum.AVAILABLE,
            likes_status=MetricStatusEnum.AVAILABLE,
            comments_status=MetricStatusEnum.AVAILABLE,
            shares_status=MetricStatusEnum.AVAILABLE,
        )
        self.assertEqual(payload["metrics"]["views"], MAX_SAFE_INTEGER)

    def test_all_metrics_missing_raises_no_metrics_available(self):
        """Nếu tất cả metric đều không AVAILABLE -> trả lỗi NO_METRICS_AVAILABLE (HTTP 502)."""
        with self.assertRaises(PostMetricsException) as ctx:
            self.service.build_success_payload(
                platform="TIKTOK",
                post_url="https://www.tiktok.com/@user/video/123",
                canonical_post_url="https://www.tiktok.com/@user/video/123",
                post_id="123",
                views=None,
                likes=None,
                comments=None,
                shares=None,
                views_status=MetricStatusEnum.FETCH_FAILED,
                likes_status=MetricStatusEnum.FETCH_FAILED,
                comments_status=MetricStatusEnum.FETCH_FAILED,
                shares_status=MetricStatusEnum.NOT_SUPPORTED,
            )
        self.assertEqual(ctx.exception.code, "NO_METRICS_AVAILABLE")
        self.assertEqual(ctx.exception.status_code, 502)

    # =========================================================================
    # 4. CACHING BEHAVIOR
    # =========================================================================

    def test_cache_preserves_tracked_at_and_updates_served_at(self):
        """
        Dữ liệu cache phải giữ nguyên trackedAt gốc, đổi servedAt thành thời điểm phục vụ
        và đánh dấu cacheHit=true.
        """
        raw_url = "https://www.tiktok.com/@creator/video/99887766"
        tracked_t1 = "2026-11-22T01:00:00Z"

        initial_payload = self.service.build_success_payload(
            platform="TIKTOK",
            post_url=raw_url,
            canonical_post_url=raw_url,
            post_id="99887766",
            author={"username": "creator", "displayName": "Creator"},
            views=50000,
            likes=1000,
            comments=50,
            shares=20,
            views_status=MetricStatusEnum.AVAILABLE,
            likes_status=MetricStatusEnum.AVAILABLE,
            comments_status=MetricStatusEnum.AVAILABLE,
            shares_status=MetricStatusEnum.AVAILABLE,
            tracked_at=tracked_t1,
        )

        cache_key = f"TIKTOK:{raw_url}"
        self.service.cache.set(cache_key, initial_payload)

        cached_res = self.service.cache.get(cache_key, raw_url + "?new_tracking=123")
        self.assertIsNotNone(cached_res)
        self.assertEqual(cached_res["trackedAt"], tracked_t1)
        self.assertEqual(cached_res["postUrl"], raw_url + "?new_tracking=123")
        self.assertTrue(cached_res["collection"]["cacheHit"])
        self.assertIsNotNone(cached_res["servedAt"])

    # =========================================================================
    # 5. ERROR STATUS & RETRYABLE FLAGS
    # =========================================================================

    def test_error_status_and_retryable_format(self):
        exc_cases = [
            ("INVALID_POST_URL", "URL không hợp lệ", 400, False, None),
            ("UNSUPPORTED_PLATFORM", "Nền tảng không hỗ trợ", 422, False, None),
            ("POST_NOT_FOUND", "Bài không tồn tại", 404, False, None),
            ("POST_PRIVATE_OR_UNAVAILABLE", "Bài riêng tư", 404, False, None),
            ("RATE_LIMITED", "Bị chặn rate limit", 429, True, 30),
            ("UPSTREAM_UNAVAILABLE", "Upstream lỗi", 503, True, 60),
            ("UPSTREAM_TIMEOUT", "Timeout upstream", 504, True, 10),
            ("NO_METRICS_AVAILABLE", "Không có metrics", 502, False, None),
        ]

        for code, msg, status, retryable, retry_sec in exc_cases:
            exc = PostMetricsException(
                code=code,
                message=msg,
                status_code=status,
                retryable=retryable,
                retryAfterSeconds=retry_sec,
            )
            d = exc.to_response_dict()
            self.assertEqual(d["schemaVersion"], "1.1")
            self.assertEqual(d["status"], "error")
            self.assertEqual(d["error"]["code"], code)
            self.assertEqual(d["error"]["message"], msg)
            self.assertEqual(d["error"]["retryable"], retryable)
            self.assertEqual(d["error"]["retryAfterSeconds"], retry_sec)
            self.assertIsNone(d["trackedAt"])
            self.assertEqual(d["_http_status"], status)

    # =========================================================================
    # 6. REQUEST BODY COMPATIBILITY
    # =========================================================================

    @patch("app.services.single_post_tracker_service.SinglePostTrackerService.fetch_post_metrics")
    def test_request_aliases(self, mock_fetch):
        mock_fetch.return_value = {
            "schemaVersion": "1.1",
            "status": "success",
            "platform": "TIKTOK",
            "postUrl": "https://www.tiktok.com/@user/video/1",
            "canonicalPostUrl": "https://www.tiktok.com/@user/video/1",
            "postId": "1",
            "author": {},
            "metrics": {"views": 10},
            "metricStatus": {"views": "AVAILABLE"},
            "content": {},
            "trackedAt": "2026-11-22T00:00:00Z",
            "servedAt": "2026-11-22T00:00:00Z",
            "collection": {"cacheHit": False},
            "warnings": [],
        }

        # Case 1: post_url snake_case
        res1 = self.client.post("/api/v1/crawl/post-metrics", json={"post_url": "https://www.tiktok.com/@user/video/1"})
        self.assertEqual(res1.status_code, 200)

        # Case 2: postUrl camelCase
        res2 = self.client.post("/api/v1/crawl/post-metrics", json={"postUrl": "https://www.tiktok.com/@user/video/1"})
        self.assertEqual(res2.status_code, 200)


if __name__ == "__main__":
    unittest.main()
