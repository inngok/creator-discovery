import unittest
from unittest.mock import MagicMock, patch

from app.ai.rules.rule_config import Decision, ProcessingStatus
from app.ai.rules.rule_engine import RuleDecisionResult
from app.core.candidate_staging import CandidateStagingRecord, staging_repo
from app.workers.tasks import (
    preflight_candidate_task,
    process_facebook_profile,
    process_instagram_profile,
    process_tiktok_profile,
)


class TestCeleryGatesAndPipeline(unittest.TestCase):
    """
    Bộ kiểm thử Tích hợp Phase 3:
    - TC14: Candidate AUTO_REJECT -> Không tạo Deep Crawl Task nào.
    - TC15: Candidate NEEDS_MANAGER_REVIEW chưa duyệt -> Không kích hoạt Deep Crawl.
    - TC16: Data Manager APPROVE -> Kích hoạt duy nhất 1 Deep Crawl Task và Worker Gate mở.
    - TC20: Direct call trái phép vào Deep Crawl Worker -> Bị chặn ngay dòng đầu với mã BLOCKED.
    """

    def setUp(self):
        pass

    @patch("app.workers.tasks.process_tiktok_profile.apply_async")
    @patch("app.services.preflight_service.preflight_service.run_preflight")
    def test_tc14_auto_reject_does_not_enqueue_deep_crawl(
        self, mock_run_preflight, mock_deep_crawl_apply
    ):
        """TC14: Candidate AUTO_REJECT -> Xác nhận không có Deep Crawl Task nào được tạo."""
        username = "seller_shop_wholesale_tc14"
        mock_run_preflight.return_value = RuleDecisionResult(
            decision=Decision.AUTO_REJECT,
            primary_type="BRAND",
            reason_codes=["BR01_COMMERCIAL_SELLER_BIO"],
            matched_signals=["sỉ sll"],
            evidence_summary={"followers": 8000, "opinion_count": 0},
        )

        result = preflight_candidate_task.run(
            platform="TIKTOK", username=username
        )

        # 1. Kết quả trả về phải là AUTO_REJECT
        self.assertEqual(result["decision"], Decision.AUTO_REJECT.value)
        self.assertEqual(result["primary_type"], "BRAND")

        # 2. Deep Crawl Task KHÔNG được phép tạo ra
        mock_deep_crawl_apply.assert_not_called()

    @patch("app.workers.tasks.process_tiktok_profile.apply_async")
    @patch("app.services.preflight_service.preflight_service.run_preflight")
    def test_tc15_needs_manager_review_does_not_enqueue_deep_crawl(
        self, mock_run_preflight, mock_deep_crawl_apply
    ):
        """TC15: Candidate NEEDS_MANAGER_REVIEW chưa duyệt -> Không kích hoạt Deep Crawl."""
        username = "ambiguous_mixed_tc15"
        mock_run_preflight.return_value = RuleDecisionResult(
            decision=Decision.NEEDS_MANAGER_REVIEW,
            primary_type="MIXED",
            reason_codes=["BR06_SELECTIVE_REVIEW_CREATOR_WITH_SHOP"],
            matched_signals=["bio_review", "shop_link"],
            evidence_summary={"followers": 25000, "opinion_count": 2},
        )

        result = preflight_candidate_task.run(
            platform="TIKTOK", username=username
        )

        # 1. Trả về đúng quyết định chờ duyệt
        self.assertEqual(result["decision"], Decision.NEEDS_MANAGER_REVIEW.value)

        # 2. Tuyệt đối không kích hoạt Deep Crawl
        mock_deep_crawl_apply.assert_not_called()

        # 3. Trạng thái trong Candidate Staging không được phép là DEEP_CRAWL_QUEUED
        rec = staging_repo.get_candidate("TIKTOK", username)
        if rec:
            self.assertNotEqual(rec.processing_status, ProcessingStatus.DEEP_CRAWL_QUEUED)

    @patch("app.workers.tasks.process_tiktok_profile.apply_async")
    @patch("app.services.preflight_service.preflight_service.run_preflight")
    def test_tc16_manager_approved_triggers_single_deep_crawl_and_passes_gate(
        self, mock_run_preflight, mock_deep_crawl_apply
    ):
        """TC16: Data Manager APPROVE -> Kích hoạt duy nhất 1 Deep Crawl Task và Worker Gate mở."""
        username = "approved_creator_tc16"

        # Khởi tạo bản ghi ban đầu đang chờ Manager Review
        initial_record = CandidateStagingRecord(
            platform="TIKTOK",
            username=username,
            display_name="Approved Creator",
            processing_status=ProcessingStatus.PREFLIGHT_DONE,
            decision=Decision.NEEDS_MANAGER_REVIEW,
            primary_type="UNKNOWN",
        )
        staging_repo.upsert_candidate(initial_record)

        mock_run_preflight.return_value = RuleDecisionResult(
            decision=Decision.MANAGER_APPROVED,
            primary_type="KOC",
            reason_codes=["DATA_MANAGER_MANUAL_ACCEPT"],
            matched_signals=[],
            evidence_summary={"approved_by": "manager_admin"},
        )

        result = preflight_candidate_task.run(
            platform="TIKTOK", username=username
        )

        # 1. Xác nhận quyết định là MANAGER_APPROVED
        self.assertEqual(result["decision"], Decision.MANAGER_APPROVED.value)

        # 2. Kích hoạt đúng 1 tác vụ Deep Crawl vào hàng đợi deep_crawl_queue
        self.assertEqual(mock_deep_crawl_apply.call_count, 1)
        mock_deep_crawl_apply.assert_called_with(
            args=[username], queue="deep_crawl_queue"
        )

        # 3. Trạng thái staging được cập nhật thành DEEP_CRAWL_QUEUED
        rec = staging_repo.get_candidate("TIKTOK", username)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.processing_status, ProcessingStatus.DEEP_CRAWL_QUEUED)

        # 4. Khi Worker Deep Crawl nhận task này, Gate phải mở (không bị BLOCKED)
        with patch("app.workers.tasks._crawl_tiktok_profile_sync") as mock_crawl_sync, \
             patch("app.workers.tasks.process_ai_enrichment") as mock_ai, \
             patch("app.workers.tasks.java_client.send_creator_data") as mock_send_java, \
             patch("app.workers.tasks.JavaMapper.from_ingest_payload") as mock_mapper:

            mock_payload = MagicMock()
            mock_payload.creator = MagicMock()
            mock_payload.recentPosts = []
            mock_payload.aiEnrichment = MagicMock(creatorType="KOC")
            mock_payload.model_dump.return_value = {"creator": username, "status": "CRAWLED"}
            mock_crawl_sync.return_value = mock_payload
            mock_ai.return_value = MagicMock(creatorType="KOC")
            mock_mapper.return_value = {"creatorId": username}
            mock_send_java.return_value = True

            worker_result = process_tiktok_profile.run(username=username)

            # Phải vượt qua Gate (status không phải là BLOCKED)
            self.assertNotEqual(
                isinstance(worker_result, dict) and worker_result.get("status"),
                "BLOCKED",
            )
            mock_crawl_sync.assert_called_once_with(username)

    @patch("app.workers.tasks._crawl_tiktok_profile_sync")
    def test_tc20_unauthorized_direct_call_is_blocked_at_gate(
        self, mock_crawl_sync
    ):
        """TC20: Thử gọi trực tiếp process_tiktok_profile(unauthorized_user) -> Bị chặn ngay dòng đầu."""
        unauthorized_user = "sneaky_bypasser_tc20"

        rec = staging_repo.get_candidate("TIKTOK", unauthorized_user)
        if rec:
            staging_repo.update_status_and_decision(
                platform="TIKTOK",
                username=unauthorized_user,
                status=ProcessingStatus.PREFLIGHT_DONE,
                decision=Decision.AUTO_REJECT,
            )

        # Gọi trực tiếp Deep Crawl Task mà không có is_refresh=True
        result = process_tiktok_profile.run(username=unauthorized_user)

        # Phải bị chặn với mã BLOCKED và UNAUTHORIZED_DEEP_CRAWL
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("status"), "BLOCKED")
        self.assertEqual(result.get("reason"), "UNAUTHORIZED_DEEP_CRAWL")
        self.assertEqual(result.get("username"), unauthorized_user)

        # Trình duyệt cào sâu Playwright tuyệt đối không được mở
        mock_crawl_sync.assert_not_called()

    @patch("app.workers.tasks._crawl_facebook_profile_sync")
    def test_tc20_facebook_gate_blocks_unauthorized_call(self, mock_fb_sync):
        """TC20 (FB): Deep Crawl Gate Facebook chặn đứng lệnh gọi trái phép."""
        fb_user = "unauthorized_fb_page_tc20"
        result = process_facebook_profile.run(username=fb_user)

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("status"), "BLOCKED")
        self.assertEqual(result.get("reason"), "UNAUTHORIZED_DEEP_CRAWL")
        mock_fb_sync.assert_not_called()

    @patch("app.workers.tasks._crawl_instagram_profile_sync")
    def test_tc20_instagram_gate_blocks_unauthorized_call(self, mock_ig_sync):
        """TC20 (IG): Deep Crawl Gate Instagram chặn đứng lệnh gọi trái phép."""
        ig_user = "unauthorized_ig_profile_tc20"
        result = process_instagram_profile.run(username=ig_user)

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("status"), "BLOCKED")
        self.assertEqual(result.get("reason"), "UNAUTHORIZED_DEEP_CRAWL")
        mock_ig_sync.assert_not_called()

    @patch("app.workers.tasks.process_tiktok_profile.apply_async")
    @patch("app.services.preflight_service.preflight_service.run_preflight")
    def test_auto_accept_koc_auto_enqueues_deep_crawl(
        self, mock_run_preflight, mock_deep_crawl_apply
    ):
        """Kiểm tra luồng AUTO_ACCEPT_KOC tự động enqueue vào deep_crawl_queue."""
        username = "genuine_reviewer_koc"
        mock_run_preflight.return_value = RuleDecisionResult(
            decision=Decision.AUTO_ACCEPT_KOC,
            primary_type="KOC",
            reason_codes=["BR03_KOC_AFFILIATE_AND_PRODUCT_REVIEW"],
            matched_signals=["review chân thật"],
            evidence_summary={"followers": 15000, "review_ratio": 0.8},
        )

        result = preflight_candidate_task.run(
            platform="TIKTOK", username=username
        )

        self.assertEqual(result["decision"], Decision.AUTO_ACCEPT_KOC.value)
        self.assertEqual(result["primary_type"], "KOC")
        mock_deep_crawl_apply.assert_called_once_with(
            args=[username], queue="deep_crawl_queue"
        )

    @patch("app.api.routers.crawl_router.process_tiktok_profile.delay")
    @patch("app.api.routers.crawl_router.preflight_candidate_task.delay")
    def test_api_enqueue_tiktok_routes_to_preflight_by_default(
        self, mock_preflight_delay, mock_deep_delay
    ):
        """Kiểm tra API /tiktok/enqueue mặc định đi qua Preflight Gate."""
        import asyncio
        from app.api.routers.crawl_router import enqueue_tiktok

        mock_preflight_delay.return_value = MagicMock(id="task-mock-preflight-1")
        res = asyncio.run(enqueue_tiktok("creator_user_1", force_deep_crawl=False))
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["mode"], "PREFLIGHT_GATE")
        mock_preflight_delay.assert_called_once_with("TIKTOK", "creator_user_1")
        mock_deep_delay.assert_not_called()

    @patch("app.api.routers.crawl_router.process_tiktok_profile.delay")
    @patch("app.api.routers.crawl_router.preflight_candidate_task.delay")
    def test_api_enqueue_tiktok_force_deep_crawl(
        self, mock_preflight_delay, mock_deep_delay
    ):
        """Kiểm tra API /tiktok/enqueue với force_deep_crawl=True kích hoạt Deep Crawl."""
        import asyncio
        from app.api.routers.crawl_router import enqueue_tiktok

        mock_deep_delay.return_value = MagicMock(id="task-mock-deep-1")
        res = asyncio.run(enqueue_tiktok("creator_user_2", force_deep_crawl=True))
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["mode"], "FORCE_DEEP_CRAWL")
        mock_deep_delay.assert_called_once_with("creator_user_2", is_refresh=True)
        mock_preflight_delay.assert_not_called()


if __name__ == "__main__":
    unittest.main()

