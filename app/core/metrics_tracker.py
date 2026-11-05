import time
from collections import deque
from datetime import UTC, datetime
from threading import Lock
from typing import Any


class CrawlerMetricsTracker:
    """
    Theo dõi hiệu suất Crawler thời gian thực (Realtime Metrics):
    - Số lượt cào thành công.
    - Số kênh bị lỗi Rate Limit (429 / Captcha).
    - Tốc độ phản hồi trung bình (latency ms).
    - Nhật ký lịch sử các lượt cào gần nhất phục vụ vẽ biểu đồ (Chart).
    """

    def __init__(self, max_history: int = 100):
        self._lock = Lock()
        self.total_requests: int = 0
        self.success_count: int = 0
        self.rate_limit_429_count: int = 0
        self.error_count: int = 0
        self.latencies_ms: deque = deque(maxlen=max_history)
        self.history: deque = deque(maxlen=max_history)

    def record_request(
        self,
        platform: str,
        username: str,
        latency_ms: float,
        status_code: int = 200,
        is_success: bool = True,
        is_rate_limited: bool = False,
        error_msg: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self.total_requests += 1
            self.latencies_ms.append(latency_ms)

            if is_rate_limited or status_code == 429:
                self.rate_limit_429_count += 1
                status = "RATE_LIMITED_429"
            elif is_success:
                self.success_count += 1
                status = "SUCCESS"
            else:
                self.error_count += 1
                status = "ERROR"

            entry = {
                "timestamp": datetime.now(UTC).isoformat(),
                "platform": platform.upper(),
                "username": username.removeprefix("@"),
                "latency_ms": round(latency_ms, 2),
                "status_code": 429 if is_rate_limited else status_code,
                "status": status,
                "error": error_msg,
            }
            self.history.append(entry)
            return entry

    def get_metrics_summary(self) -> dict[str, Any]:
        with self._lock:
            avg_latency = (
                round(sum(self.latencies_ms) / len(self.latencies_ms), 2)
                if self.latencies_ms
                else 0.0
            )
            success_rate = (
                round((self.success_count / self.total_requests) * 100, 1)
                if self.total_requests > 0
                else 100.0
            )

            return {
                "total_requests": self.total_requests,
                "success_count": self.success_count,
                "rate_limit_429_count": self.rate_limit_429_count,
                "error_count": self.error_count,
                "success_rate_percent": success_rate,
                "avg_latency_ms": avg_latency,
                "current_throughput_rpm": len(self.history),
                "recent_history": list(self.history)[-20:],
            }


metrics_tracker = CrawlerMetricsTracker()

