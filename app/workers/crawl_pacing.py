import os
import random
import threading
import time

from celery.signals import task_prerun

from app.core.logger import setup_logger

logger = setup_logger("crawl_pacing")
_lock = threading.Lock()
_next_start = 0.0

# Bảng cấu hình nhịp nghỉ (Declarative Pacing Table - 0 if-else):
# - Preflight chỉ tải SSR nhẹ (~1.5s) nên dùng nhịp nghỉ ngắn (5-10s) tránh nghẽn hàng đợi.
# - Deep Crawl mở Playwright nặng nên giữ nhịp nghỉ an toàn (30-45s) chống chặn IP.
_TASK_INTERVAL_ENV = {
    "app.workers.tasks.preflight_candidate_task": (
        "PREFLIGHT_TASK_MIN_INTERVAL",
        "PREFLIGHT_TASK_MAX_INTERVAL",
        5.0,
        10.0,
    ),
    "app.workers.tasks.process_tiktok_profile": (
        "CRAWL_TASK_MIN_INTERVAL",
        "CRAWL_TASK_MAX_INTERVAL",
        30.0,
        45.0,
    ),
    "app.workers.tasks.process_facebook_profile": (
        "CRAWL_TASK_MIN_INTERVAL",
        "CRAWL_TASK_MAX_INTERVAL",
        30.0,
        45.0,
    ),
    "app.workers.tasks.process_instagram_profile": (
        "CRAWL_TASK_MIN_INTERVAL",
        "CRAWL_TASK_MAX_INTERVAL",
        30.0,
        45.0,
    ),
}


@task_prerun.connect
def pace_crawl_tasks(sender=None, **kwargs):
    """Share one start interval across crawl queues within this worker process."""
    global _next_start
    if os.getenv("TESTING") in ("1", "true", "True") or os.getenv("DISABLE_CRAWL_PACING") == "1":
        return
    config = _TASK_INTERVAL_ENV.get(getattr(sender, "name", ""))
    if not config:
        return
    min_env, max_env, default_min, default_max = config
    minimum = max(0.0, float(os.getenv(min_env, str(default_min))))
    maximum = max(minimum, float(os.getenv(max_env, str(default_max))))
    with _lock:
        wait = max(0.0, _next_start - time.monotonic())
        if wait:
            logger.info("Nghi %.1f giay truoc task cao tiep theo", wait)
            time.sleep(wait)
        _next_start = time.monotonic() + random.uniform(minimum, maximum)
