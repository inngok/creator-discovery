import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.ai.rules.rule_config import Decision, ProcessingStatus
from app.core.candidate_staging import CandidateStagingRecord, staging_repo
from app.api.routers.monitor_router import get_review_candidates


class ReviewCandidateFeedTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        location = patch("app.core.candidate_staging.SQLITE_STAGING_PATH", str(Path(directory.name) / "staging.db"))
        location.start()
        self.addCleanup(location.stop)
        staging_repo._init_sqlite()

    def add(self, username, decision, status=ProcessingStatus.PREFLIGHT_DONE):
        staging_repo.upsert_candidate(CandidateStagingRecord(
            username=username, decision=decision, processing_status=status,
            evidence_summary={"followers": 12345, "audit_posts": [{"platformPostId": "1", "caption": "Stored evidence"}]}))

    def test_unresolved_profiles_are_selected_before_activity_limit(self):
        self.add("old_review", Decision.NEEDS_MANAGER_REVIEW)
        self.add("missing_posts", Decision.SKIP_INSUFFICIENT)
        self.add("retry_fetch", Decision.RETRY_PREFLIGHT)
        self.add("already_ingested", Decision.SKIP_INSUFFICIENT, ProcessingStatus.INGESTED)
        for i in range(105):
            self.add(f"new_reject_{i}", Decision.AUTO_REJECT)
        activities, _ = staging_repo.list_candidates(limit=100)
        self.assertNotIn("old_review", [row.username for row in activities])
        result = asyncio.run(get_review_candidates(limit=200, offset=0))
        self.assertEqual(result["total"], 3)
        self.assertEqual({row["username"] for row in result["items"]},
                         {"old_review", "missing_posts", "retry_fetch"})
        self.assertEqual(result["items"][0]["followers"], 12345)
        self.assertEqual(result["items"][0]["recent_posts"][0]["caption"], "Stored evidence")

    def test_pagination_uses_total_unresolved_count(self):
        for i in range(5):
            self.add(f"review_{i}", Decision.NEEDS_MANAGER_REVIEW)
        first = asyncio.run(get_review_candidates(limit=3, offset=0))
        second = asyncio.run(get_review_candidates(limit=3, offset=3))
        self.assertEqual(first["total"], 5)
        self.assertEqual(len(first["items"]), 3)
        self.assertEqual(len(second["items"]), 2)
        self.assertEqual(len({r["username"] for r in first["items"] + second["items"]}), 5)
