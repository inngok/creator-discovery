import copy
import threading
import time
from datetime import UTC, datetime
from typing import Any, Optional


class PostMetricsCache:
    """In-memory cache có TTL và thread-safe cho số liệu bài viết."""

    def __init__(self, default_ttl_seconds: int = 300):
        self.default_ttl = default_ttl_seconds
        self._cache: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get(self, cache_key: str, input_post_url: str) -> Optional[dict[str, Any]]:
        with self._lock:
            entry = self._cache.get(cache_key)
            if not entry:
                return None
            if time.time() > entry["expires_at"]:
                del self._cache[cache_key]
                return None

            cached_data = copy.deepcopy(entry["data"])
            # Giữ nguyên trackedAt gốc, cập nhật servedAt thời điểm phục vụ và đánh dấu cache hit
            cached_data["servedAt"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            cached_data["postUrl"] = input_post_url
            if "collection" in cached_data:
                cached_data["collection"]["cacheHit"] = True
            return cached_data

    def set(self, cache_key: str, data: dict[str, Any], ttl_seconds: Optional[int] = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        with self._lock:
            self._cache[cache_key] = {
                "data": copy.deepcopy(data),
                "expires_at": time.time() + ttl,
            }

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
