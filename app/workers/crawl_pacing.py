import os
import random
import threading
import time

from celery.signals import task_prerun

from app.core.logger import setup_logger

logger = setup_logger("crawl_pacing")
_lock = threading.Lock()
_next_start = 0.0
_tasks = {
    "app.workers.tasks.preflight_candidate_task",
    "app.workers.tasks.process_tiktok_profile",
    "app.workers.tasks.process_facebook_profile",
    "app.workers.tasks.process_instagram_profile",
}


@task_prerun.connect
def pace_crawl_tasks(sender=None, **kwargs):
    """Share one start interval across crawl queues within this worker process."""
    global _next_start
    if getattr(sender, "name", "") not in _tasks:
        return
    minimum = max(0.0, float(os.getenv("CRAWL_TASK_MIN_INTERVAL", "30")))
    maximum = max(minimum, float(os.getenv("CRAWL_TASK_MAX_INTERVAL", "45")))
    with _lock:
        wait = max(0.0, _next_start - time.monotonic())
        if wait:
            logger.info("Nghi %.1f giay truoc task cao tiep theo", wait)
            time.sleep(wait)
        _next_start = time.monotonic() + random.uniform(minimum, maximum)
