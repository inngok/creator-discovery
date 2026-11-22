import socket
from typing import Any, Tuple

from app.core.logger import setup_logger
from app.services.post_metrics import (
    ALLOWED_DOMAINS,
    MAX_SAFE_INTEGER,
    PostMetricsCache,
    PostMetricsException,
    build_success_payload,
    fetch_facebook_post,
    fetch_instagram_post,
    fetch_tiktok_post,
    fetch_youtube_post,
    validate_and_normalize_url,
)

logger = setup_logger("single_post_tracker")


class SinglePostTrackerService:
    """
    Dịch vụ cào thông số bài đăng đơn lẻ (WF-03 Post Metrics).
    Facade điều phối URL validation, caching và các scraper theo từng nền tảng.
    """

    ALLOWED_DOMAINS = ALLOWED_DOMAINS

    def __init__(self):
        self.cache = PostMetricsCache(default_ttl_seconds=300)

    def validate_and_normalize_url(self, post_url: str) -> Tuple[str, str, str, str]:
        return validate_and_normalize_url(post_url)

    def detect_platform(self, url: str) -> str:
        try:
            plat, _, _, _ = self.validate_and_normalize_url(url)
            return plat
        except Exception:
            return "UNKNOWN"

    def build_success_payload(self, **kwargs) -> dict[str, Any]:
        return build_success_payload(**kwargs)

    def _fetch_tiktok_post(self, url: str, canonical_url: str, post_id: str, author_hint: str) -> dict[str, Any]:
        return fetch_tiktok_post(url, canonical_url, post_id, author_hint)

    def _fetch_youtube_post(self, url: str, canonical_url: str, post_id: str, author_hint: str) -> dict[str, Any]:
        return fetch_youtube_post(url, canonical_url, post_id, author_hint)

    def _fetch_instagram_post(self, url: str, canonical_url: str, post_id: str, author_hint: str) -> dict[str, Any]:
        return fetch_instagram_post(url, canonical_url, post_id, author_hint)

    def _fetch_facebook_post(self, url: str, canonical_url: str, post_id: str, author_hint: str) -> dict[str, Any]:
        return fetch_facebook_post(url, canonical_url, post_id, author_hint)

    def fetch_post_metrics(self, post_url: str) -> dict[str, Any]:
        """
        Bóc tách số liệu bài viết theo yêu cầu WF-03:
        1. Kiểm tra an toàn URL và SSRF.
        2. Kiểm tra cache: nếu trúng, giữ nguyên trackedAt gốc, cập nhật servedAt và cacheHit=True.
        3. Điều phối tới scraper chuyên biệt của từng platform.
        """
        try:
            platform, canonical_url, post_id, author_hint = self.validate_and_normalize_url(post_url)
        except PostMetricsException as exc:
            return exc.to_response_dict()

        # Kiểm tra Cache
        cache_key = f"{platform}:{canonical_url}"
        cached_result = self.cache.get(cache_key, post_url)
        if cached_result:
            return cached_result

        scrapers = {
            "TIKTOK": self._fetch_tiktok_post,
            "YOUTUBE": self._fetch_youtube_post,
            "INSTAGRAM": self._fetch_instagram_post,
            "FACEBOOK": self._fetch_facebook_post,
        }

        try:
            scraper_fn = scrapers[platform]
            res = scraper_fn(post_url, canonical_url, post_id, author_hint)
            self.cache.set(cache_key, res)
            return res
        except PostMetricsException as p_err:
            return p_err.to_response_dict()
        except socket.timeout:
            return PostMetricsException(
                code="UPSTREAM_TIMEOUT",
                message="Quá thời gian chờ phản hồi từ nguồn upstream (vượt quá 15 giây)",
                status_code=504,
                retryable=True,
                retryAfterSeconds=10,
            ).to_response_dict()
        except Exception as e:
            logger.error(f"[TRACKER] Unexpected error fetching post metrics: {e}", exc_info=True)
            return PostMetricsException(
                code="UPSTREAM_UNAVAILABLE",
                message=f"Lỗi khi cào dữ liệu từ upstream: {e}",
                status_code=503,
                retryable=True,
                retryAfterSeconds=60,
            ).to_response_dict()


single_post_tracker = SinglePostTrackerService()
