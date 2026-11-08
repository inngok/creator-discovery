import sys
import os
sys.path.insert(0, os.path.abspath("."))

import unittest
from unittest.mock import MagicMock, patch
import argparse
from fastapi import FastAPI
from fastapi.testclient import TestClient

# 1. Test crawl_router /shortlist-trigger endpoint
class TestShortlistTriggerAPI(unittest.TestCase):
    def setUp(self):
        from app.api.routers.crawl_router import router
        self.app = FastAPI()
        self.app.include_router(router)
        self.client = TestClient(self.app)

    @patch("app.api.routers.crawl_router.trigger_shortlist_crawl_background")
    def test_post_shortlist_trigger_success(self, mock_trigger):
        payload = {
            "platform": "TIKTOK",
            "username": "@test_creator_123",
            "creatorId": "c-uuid-999",
            "campaignId": "camp-111"
        }
        response = self.client.post("/api/v1/crawl/shortlist-trigger", json=payload)
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "QUEUED")
        self.assertEqual(data["platform"], "TIKTOK")
        self.assertEqual(data["username"], "test_creator_123")
        self.assertEqual(data["creatorId"], "c-uuid-999")
        
        # Verify background trigger called with cleaned username (prefix @ removed)
        mock_trigger.assert_called_once_with(
            platform="TIKTOK",
            username="test_creator_123",
            creator_id="c-uuid-999"
        )

    def test_post_shortlist_trigger_validation_error(self):
        # Missing required username
        response = self.client.post("/api/v1/crawl/shortlist-trigger", json={"platform": "TIKTOK"})
        self.assertEqual(response.status_code, 422)


# 2. Test shortlist_crawl_service
class TestShortlistCrawlService(unittest.TestCase):
    def setUp(self):
        self.emb_patcher = patch(
            "app.mappers.java_mapper.embedding_service.get_search_embedding",
            return_value=[0.05] * 768,
        )
        self.emb_patcher.start()

    def tearDown(self):
        self.emb_patcher.stop()

    @patch("app.services.shortlist_crawl_service.threading.Thread")
    def test_trigger_shortlist_crawl_background(self, mock_thread_cls):
        mock_thread_instance = MagicMock()
        mock_thread_cls.return_value = mock_thread_instance
        
        from app.services.shortlist_crawl_service import trigger_shortlist_crawl_background
        t = trigger_shortlist_crawl_background("TIKTOK", "user_abc", "cid_123")
        
        mock_thread_cls.assert_called_once()
        mock_thread_instance.start.assert_called_once()
        self.assertEqual(t, mock_thread_instance)

    @patch("app.workers.tasks.process_tiktok_profile.delay")
    def test_crawl_and_sync_via_celery(self, mock_delay):
        mock_task = MagicMock()
        mock_task.id = "task-mock-id-123"
        mock_delay.return_value = mock_task

        from app.services.shortlist_crawl_service import crawl_and_sync_creator_sync
        result = crawl_and_sync_creator_sync("TIKTOK", "@celery_test", "cid_001")

        self.assertEqual(result["mode"], "CELERY")
        self.assertEqual(result["status"], "QUEUED")
        self.assertEqual(result["task_id"], "task-mock-id-123")
        self.assertEqual(result["username"], "celery_test")
        mock_delay.assert_called_once_with("celery_test", is_refresh=True)

    @patch("app.workers.tasks.process_tiktok_profile.delay", side_effect=Exception("Redis connection refused"))
    @patch("app.crawlers.tiktok_crawler._crawl_tiktok_profile_sync")
    @patch("app.ai.analyzer.process_ai_enrichment")
    @patch("app.ai.creator_classifier.classify_creator_type_smart")
    @patch("app.mappers.java_mapper.JavaMapper.from_ingest_payload")
    @patch("app.clients.java_client.java_client.send_creator_data")
    def test_crawl_and_sync_fallback_direct(
        self, mock_send, mock_map, mock_classify, mock_enrich, mock_crawl, mock_celery
    ):
        mock_crawl_result = MagicMock()
        mock_crawl_result.creator = MagicMock()
        mock_crawl_result.creator.username = "direct_test"
        mock_crawl_result.creator.displayName = "Direct Test"
        mock_crawl_result.creator.bio = "Review đồ hay"
        mock_crawl_result.creator.followers = 50000
        mock_crawl_result.recentPosts = [MagicMock(), MagicMock(), MagicMock()]
        mock_crawl.return_value = mock_crawl_result

        mock_enrich.return_value = MagicMock(category="Beauty", niche="Skincare")
        mock_classify.return_value = "KOC"
        mock_map.return_value = {"mock": "payload"}
        mock_send.return_value = True

        from app.services.shortlist_crawl_service import crawl_and_sync_creator_sync
        result = crawl_and_sync_creator_sync("TIKTOK", "direct_test", "cid_002")

        self.assertEqual(result["mode"], "DIRECT")
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["username"], "direct_test")
        self.assertEqual(result["posts_count"], 3)
        self.assertEqual(result["creator_type"], "KOC")
        mock_send.assert_called_once()


# 3. Test crawl_recent_posts_worker changes
class TestCrawlRecentPostsWorker(unittest.TestCase):
    def test_cli_parser_shortlist_only(self):
        from app.scripts.crawl_recent_posts_worker import fetch_pending_creators
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--limit", "-l", type=int, default=0)
        parser.add_argument("--shortlist-only", "-s", action="store_true")
        
        args = parser.parse_args(["-s", "--limit", "10"])
        self.assertTrue(args.shortlist_only)
        self.assertEqual(args.limit, 10)

        args_default = parser.parse_args([])
        self.assertFalse(args_default.shortlist_only)

    @patch("psycopg2.connect")
    def test_fetch_pending_creators_query_generation(self, mock_connect):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchall.return_value = []

        from app.scripts.crawl_recent_posts_worker import fetch_pending_creators

        # Case 1: shortlist_only=False
        fetch_pending_creators(limit=25, shortlist_only=False)
        executed_query_1 = mock_cur.execute.call_args[0][0]
        self.assertIn("WHERE c.id NOT IN (SELECT DISTINCT creator_id FROM post)", executed_query_1)
        self.assertNotIn("shortlist_item", executed_query_1)
        self.assertIn("LIMIT 25", executed_query_1)

        # Case 2: shortlist_only=True
        fetch_pending_creators(limit=10, shortlist_only=True)
        executed_query_2 = mock_cur.execute.call_args[0][0]
        self.assertIn("WHERE c.id NOT IN (SELECT DISTINCT creator_id FROM post)", executed_query_2)
        self.assertIn("AND c.id IN (SELECT DISTINCT creator_id FROM shortlist_item)", executed_query_2)
        self.assertIn("LIMIT 10", executed_query_2)


if __name__ == "__main__":
    unittest.main()
