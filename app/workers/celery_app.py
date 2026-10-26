import os

from celery import Celery
from dotenv import load_dotenv

load_dotenv()

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

app = Celery(
    "crawler_tasks", broker=redis_url, backend=redis_url, include=["app.workers.tasks"]
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Ho_Chi_Minh",
    enable_utc=True,
    worker_concurrency=1,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "app.workers.tasks.preflight_candidate_task": {"queue": "preflight_queue"},
        "app.workers.tasks.process_tiktok_profile": {"queue": "deep_crawl_queue"},
        "app.workers.tasks.process_facebook_profile": {"queue": "deep_crawl_queue"},
        "app.workers.tasks.process_instagram_profile": {"queue": "deep_crawl_queue"},
        "app.workers.tasks.run_retrain_pipeline": {"queue": "default"},
        "app.workers.tasks.weekly_discover_new_creators_task": {"queue": "default"},
    },
)

from celery.schedules import crontab

app.conf.beat_schedule = {
    # 1. Tự động retrain AI lúc 2h sáng Chủ Nhật hàng tuần
    "auto-retrain-every-sunday": {
        "task": "app.workers.tasks.run_retrain_pipeline",
        "schedule": crontab(
            hour=2, minute=0, day_of_week="sun"
        ),
    },
    # 2. Tự động cào KOC MỚI lúc 3h sáng Thứ Hai hàng tuần (loại bỏ 100% KOC cũ)
    "weekly-auto-discover-new-kocs": {
        "task": "app.workers.tasks.weekly_discover_new_creators_task",
        "schedule": crontab(
            hour=3, minute=0, day_of_week="mon"
        ),
        "args": (30,),  # Tìm tối đa 30 KOC mới mỗi tuần
    },
}

