import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException
from app.core.crawl_progress import (
    report_crawl_progress,
    reset_crawl_progress_callback,
    set_crawl_progress_callback,
)
from app.api.routers.crawl_router import (
    BatchCrawlRequest, start_batch_crawl_job, get_batch_crawl_job,
)


class BatchCrawlJobsTest(unittest.TestCase):
    @patch("app.workers.tasks.batch_crawl_job.update_state")
    def test_worker_progress_keeps_task_id_across_threads(self, update_state):
        from app.workers.tasks import batch_crawl_job

        async def crawl(_):
            await asyncio.to_thread(report_crawl_progress, "Discovery running", stage="search")
            return {"status": "success", "results": []}

        task_id = "12345678-1234-1234-1234-123456789abc"
        batch_crawl_job.push_request(id=task_id)
        try:
            with patch("app.api.routers.crawl_router.batch_crawl_kocs", side_effect=crawl):
                result = batch_crawl_job.run({"keywords": ["review"], "platform": "TIKTOK"})
        finally:
            batch_crawl_job.pop_request()
        self.assertEqual(result["status"], "success")
        self.assertEqual(update_state.call_count, 2)
        for call in update_state.call_args_list:
            self.assertEqual(call.kwargs["task_id"], task_id)

    @patch("app.workers.celery_app.app.AsyncResult")
    def test_unregistered_worker_reports_recovery_action(self, async_result):
        from celery.exceptions import NotRegistered
        async_result.return_value = SimpleNamespace(
            state="FAILURE", info=NotRegistered("app.workers.tasks.batch_crawl_job"))
        result = asyncio.run(get_batch_crawl_job("12345678-1234-1234-1234-123456789abc"))
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("khởi động lại", result["error"])

    @patch("app.api.routers.crawl_router.batch_crawl_kocs", side_effect=RuntimeError("private detail"))
    @patch("app.workers.tasks.batch_crawl_job.update_state")
    def test_unexpected_worker_error_preserves_progress(self, update_state, crawl):
        from app.workers.tasks import batch_crawl_job
        result = batch_crawl_job.run({"usernames": ["creator"]})
        self.assertEqual(result["status"], "error")
        self.assertIn("RuntimeError", result["error"])
        self.assertNotIn("private detail", result["error"])
        self.assertEqual(result["progress"]["stage"], "failed")
        self.assertGreaterEqual(len(result["progress"]["logs"]), 2)

    @patch("app.api.routers.crawl_router.is_redis_available", return_value=False)
    def test_unavailable_queue_is_not_reported_as_queued(self, _):
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(start_batch_crawl_job(BatchCrawlRequest(usernames=["creator"])))
        self.assertEqual(caught.exception.status_code, 503)

    @patch("app.workers.tasks.batch_crawl_job.apply_async")
    @patch("app.api.routers.crawl_router.is_redis_available", return_value=True)
    def test_submission_returns_without_crawling(self, _, enqueue):
        enqueue.return_value = SimpleNamespace(id="job-id")
        result = asyncio.run(start_batch_crawl_job(BatchCrawlRequest(usernames=["creator"])))
        self.assertEqual(result["status"], "QUEUED")
        self.assertEqual(enqueue.call_args.kwargs["queue"], "default")
        self.assertEqual(enqueue.call_args.kwargs["args"][0]["usernames"], ["creator"])

    @patch("app.workers.celery_app.app.AsyncResult")
    def test_poll_returns_completed_batch(self, async_result):
        async_result.return_value = SimpleNamespace(state="SUCCESS", result={"results": ["creator"]})
        result = asyncio.run(get_batch_crawl_job("12345678-1234-1234-1234-123456789abc"))
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["result"]["results"], ["creator"])

    @patch("app.workers.celery_app.app.AsyncResult")
    def test_no_match_search_is_completed_not_failed(self, async_result):
        batch_result = {
            "status": "success",
            "summary": {"discovery_status": "NO_MATCHES", "attempted_count": 0},
            "results": [],
        }
        async_result.return_value = SimpleNamespace(state="SUCCESS", result=batch_result)
        result = asyncio.run(get_batch_crawl_job("12345678-1234-1234-1234-123456789abc"))
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["result"]["summary"]["discovery_status"], "NO_MATCHES")

    @patch("app.workers.celery_app.app.AsyncResult")
    def test_poll_returns_live_progress_logs(self, async_result):
        progress = {
            "logs": ["[17:50:00] TikTok: đang quét từ khóa 'review'."],
            "current_keyword": "review",
            "platform": "TIKTOK",
            "stage": "search",
        }
        async_result.return_value = SimpleNamespace(
            state="PROGRESS", info={"progress": progress}
        )

        result = asyncio.run(get_batch_crawl_job("12345678-1234-1234-1234-123456789abc"))

        self.assertEqual(result["status"], "PROGRESS")
        self.assertEqual(result["progress"], progress)

    def test_progress_context_reaches_worker_thread(self):
        events = []
        token = set_crawl_progress_callback(events.append)
        try:
            asyncio.run(asyncio.to_thread(
                report_crawl_progress,
                "Đang tìm từ khóa.",
                platform="TIKTOK",
                keyword="review",
                stage="search",
            ))
        finally:
            reset_crawl_progress_callback(token)

        self.assertEqual(events[0]["keyword"], "review")
        self.assertEqual(events[0]["platform"], "TIKTOK")

    @patch("app.workers.celery_app.app.AsyncResult")
    def test_source_failure_is_not_reported_as_completed_empty_results(self, async_result):
        async_result.return_value = SimpleNamespace(state="SUCCESS", result={
            "status": "error", "error": "TikTok search unavailable"})
        result = asyncio.run(get_batch_crawl_job("12345678-1234-1234-1234-123456789abc"))
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["error"], "TikTok search unavailable")
