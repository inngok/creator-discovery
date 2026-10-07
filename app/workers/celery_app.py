import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

app = Celery(
    'crawler_tasks',
    broker=redis_url,
    backend=redis_url,
    include=['app.workers.tasks']
)

app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Asia/Ho_Chi_Minh',
    enable_utc=True,
    worker_concurrency=1,
    task_acks_late=True,
    worker_prefetch_multiplier=1
)

from celery.schedules import crontab

app.conf.beat_schedule = {
    'auto-retrain-every-sunday': {
        'task': 'app.workers.tasks.run_retrain_pipeline',
        'schedule': crontab(hour=2, minute=0, day_of_week='sun'), # Chạy lúc 2h sáng Chủ Nhật
    },
}
