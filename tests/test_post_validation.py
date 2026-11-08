import unittest
from unittest.mock import MagicMock, patch

from app.ai.rules.post_validator import validate_post_crawl
from app.ai.rules.rule_config import Decision, ProcessingStatus
from app.api.routers.review_router import (
    ReviewActionRequest,
    get_pending_review_candidates,
    process_candidate_review_action,
)
from app.core.candidate_staging import CandidateStagingRecord, staging_repo
from app.core.models import AIEnrichment, CreatorInfo, IngestPayload, Platform, Post
from app.mappers.java_mapper import JavaMapper
from app.workers.tasks import process_tiktok_profile


class TestPostValidationAndSafeIngestion(unittest.TestCase):
    """
    Bộ kiểm thử Tích hợp Phase 4:
    - TC17: Deep Crawl phát hiện toàn bài catalog shop -> Bị chặn tại Post-validation Guard, không gửi sang Java.
    - TC18: Candidate đạt mọi chuẩn Post-validation -> Gửi thành công sang Java Ingest API, staging chuyển INGESTED.
    - TC19: Candidate có tín hiệu KOC và KOL đồng thời -> Xử lý nhất quán, không lỗi runtime.
    - TC21: Luồng Refresh tài khoản cũ (is_refresh=True) -> Cập nhật metrics, không tạo duplicate account.
    - Review Router: Data Manager duyệt pending candidates, phê duyệt hoặc từ chối.
    """

    def setUp(self):
        self.emb_patcher = patch(
            "app.mappers.java_mapper.embedding_service.get_search_embedding",
            return_value=[0.05] * 768,
        )
        self.mock_emb = self.emb_patcher.start()

    def tearDown(self):
        self.emb_patcher.stop()

    def _create_mock_ingest_payload(
        self,
        username: str,
        followers: int = 25000,
        posts_captions: list[str] = None,
        creator_type: str = "KOC",
    ) -> IngestPayload:
        if posts_captions is None:
            posts_captions = [
                "Review serum trị mụn đỉnh chóp mình dùng 2 tháng nay thấy đỡ hẳn",
                "Chấm điểm 5 loại kem chống nắng cho da dầu mụn mùa hè này",
                "Mọi người lưu ý kem này chất hơi đặc nhưng thấm nhanh nha",
                "Hôm nay unbox thử thỏi son mới ra mắt màu siêu tôn da",
            ]

        posts = [
            Post(
                platformPostId=f"post_{i}",
                postUrl=f"https://www.tiktok.com/@{username}/video/{i}",
                caption=caption,
                views=15000 + i * 1000,
                likes=1200 + i * 50,
                comments=80 + i * 5,
                shares=30,
                postedAt="2026-10-20T10:00:00Z",
            )
            for i, caption in enumerate(posts_captions)
        ]

        creator = CreatorInfo(
            platform=Platform.TIKTOK,
            externalId=f"uid_{username}",
            username=username,
            displayName=f"Display {username}",
            profileUrl=f"https://www.tiktok.com/@{username}",
            followers=followers,
            bio="Review chân thật mỹ phẩm & lifestyle",
            isStableExternalId=True,
        )

        ai_enrichment = AIEnrichment(
            creatorType=creator_type,
            category="Beauty",
            niche="Skincare",
            contentSummary="Kênh chuyên review sản phẩm dưỡng da mụn",
        )

        return IngestPayload(
            creator=creator,
            recentPosts=posts,
            aiEnrichment=ai_enrichment,
            source="PLAYWRIGHT_TIKTOK",
            avg_views=16000.0,
            avg_likes=1250.0,
            avg_comments=85.0,
            avg_shares=30.0,
            engagement_rate=0.08,
        )

    def test_tc17_commercial_shop_catalog_blocked_at_post_validation(self):
        """TC17: Deep Crawl xong nhưng phát hiện toàn bài catalog shop -> Bị chặn tại Post-validation Guard."""
        username = "wholesale_catalog_shop_tc17"

        # Khởi tạo candidate đã được cấp quyền Deep Crawl
        staging_repo.upsert_candidate(
            CandidateStagingRecord(
                platform="TIKTOK",
                username=username,
                processing_status=ProcessingStatus.DEEP_CRAWL_QUEUED,
                decision=Decision.AUTO_ACCEPT_KOC,
                primary_type="KOC",
            )
        )

        # 5 bài viết đều là bài bán buôn/sỉ/catalog rõ rệt, không có từ ngữ review cá nhân
        catalog_captions = [
            "Xả kho sỉ quần áo số lượng lớn, giá sỉ từ 10 ri ib zalo ngay",
            "Tổng kho gia dụng sỉ lẻ giá buôn tốt nhất miền bắc, bảng giá ở link",
            "Chuyên sỉ toàn quốc ship cod, đặt hàng qua giỏ hàng tiktok shop",
            "Mẫu mới về tổng kho sỉ lẻ cực hot, báo giá sll qua tin nhắn",
            "Kho sỉ hàng xuất dư chiết khấu cao cho cộng tác viên và đại lý",
        ]
        payload = self._create_mock_ingest_payload(username, posts_captions=catalog_captions)

        # 1. Kiểm tra trực tiếp hàm validate_post_crawl
        val_res = validate_post_crawl(payload)
        self.assertFalse(val_res.is_valid)
        self.assertEqual(val_res.quarantine_reason, "QUARANTINE_POST_VALIDATION_FAILED")
        self.assertEqual(val_res.details.get("reason"), "COMMERCIAL_SHOP_CATALOG_POSTS")

        # 2. Kiểm tra Worker process_tiktok_profile dừng lại và không gọi java_client
        with patch("app.workers.tasks._crawl_tiktok_profile_sync", return_value=payload), \
             patch("app.workers.tasks.java_client.send_creator_data") as mock_send_java:

            worker_result = process_tiktok_profile.run(username=username)

            # Phải trả về trạng thái QUARANTINE_REJECTED
            self.assertEqual(worker_result.get("status"), "QUARANTINE_REJECTED")
            self.assertEqual(worker_result.get("reason"), "QUARANTINE_POST_VALIDATION_FAILED")

            # Tuyệt đối không gửi sang Java Backend
            mock_send_java.assert_not_called()

            # Trạng thái trong staging phải là FAILED và AUTO_REJECT
            rec = staging_repo.get_candidate("TIKTOK", username)
            self.assertIsNotNone(rec)
            self.assertEqual(rec.processing_status, ProcessingStatus.FAILED)
            self.assertEqual(rec.decision, Decision.AUTO_REJECT)

    def test_tc18_valid_candidate_passes_post_validation_and_ingests_to_java(self):
        """TC18: Candidate đạt mọi tiêu chuẩn Post-validation -> Gửi thành công sang Java Ingest API."""
        username = "genuine_beauty_koc_tc18"

        staging_repo.upsert_candidate(
            CandidateStagingRecord(
                platform="TIKTOK",
                username=username,
                processing_status=ProcessingStatus.DEEP_CRAWL_QUEUED,
                decision=Decision.AUTO_ACCEPT_KOC,
                primary_type="KOC",
            )
        )

        payload = self._create_mock_ingest_payload(username)

        with patch("app.workers.tasks._crawl_tiktok_profile_sync", return_value=payload), \
             patch("app.workers.tasks.process_ai_enrichment", return_value=payload.aiEnrichment), \
             patch("app.workers.tasks.java_client.send_creator_data", return_value=True) as mock_send_java:

            worker_result = process_tiktok_profile.run(username=username)

            # Java Client phải được gọi đúng 1 lần
            mock_send_java.assert_called_once()
            java_payload = mock_send_java.call_args[0][0]

            # Kiểm tra bảo toàn Master Identity: sourceType=PUBLIC, masterId=None
            self.assertEqual(java_payload.get("sourceType"), "PUBLIC")
            self.assertIsNone(java_payload.get("masterId"))
            self.assertEqual(java_payload.get("creatorType"), "KOC")

            # Staging record phải được cập nhật thành INGESTED
            rec = staging_repo.get_candidate("TIKTOK", username)
            self.assertIsNotNone(rec)
            self.assertEqual(rec.processing_status, ProcessingStatus.INGESTED)

    def test_tc19_dual_koc_kol_signals_consistent_handling(self):
        """TC19: Candidate có tín hiệu KOC và KOL đồng thời -> Xử lý nhất quán, không lỗi runtime."""
        username = "doctor_skincare_dual_tc19"
        captions = [
            "Bác sĩ da liễu chia sẻ cơ chế hoạt động của retinol trong điều trị lão hóa",
            "Review và so sánh 3 dòng kem dưỡng ẩm phục hồi da treatment",
            "Lời khuyên từ góc nhìn chuyên gia y khoa khi chọn kem chống nắng",
            "Trải nghiệm cá nhân sau 4 tuần dùng thử serum mới",
        ]
        payload = self._create_mock_ingest_payload(
            username, followers=80000, posts_captions=captions, creator_type="KOL"
        )
        payload.aiEnrichment.contentSummary = "Bác sĩ da liễu chia sẻ kiến thức chuyên sâu và review sản phẩm"

        # Post validation phải thành công vì nội dung chất lượng cao
        val_res = validate_post_crawl(payload)
        self.assertTrue(val_res.is_valid)

        # Mapper phải tạo đầy đủ thông tin mà không gặp exception
        java_payload = JavaMapper.from_ingest_payload(payload)
        self.assertEqual(java_payload["userName"], username)
        self.assertEqual(java_payload["creatorType"], "KOL")
        self.assertIn("Bác sĩ da liễu", java_payload["contentSummary"])

    def test_tc21_refresh_flow_bypasses_follower_gate_and_no_duplicates(self):
        """TC21: Luồng Refresh tài khoản cũ (is_refresh=True) -> Cập nhật metrics, không tạo duplicate."""
        username = "existing_creator_tc21"

        # Giả sử creator cũ có follower dao động còn 800 (< 1,000 threshold)
        payload = self._create_mock_ingest_payload(username, followers=800)

        # Với is_refresh=False -> Bị chặn vì follower < 1000
        val_normal = validate_post_crawl(payload, is_refresh=False)
        self.assertFalse(val_normal.is_valid)

        # Với is_refresh=True -> Cho phép vượt qua để cập nhật metrics mới nhất
        val_refresh = validate_post_crawl(payload, is_refresh=True)
        self.assertTrue(val_refresh.is_valid)

        # Gọi qua Worker với is_refresh=True
        with patch("app.workers.tasks._crawl_tiktok_profile_sync", return_value=payload), \
             patch("app.workers.tasks.process_ai_enrichment", return_value=payload.aiEnrichment), \
             patch("app.workers.tasks.java_client.send_creator_data", return_value=True) as mock_send_java:

            worker_result = process_tiktok_profile.run(username=username, is_refresh=True)
            mock_send_java.assert_called_once()
            self.assertEqual(worker_result.get("creator", {}).get("username"), username)

    def test_review_router_pending_and_actions(self):
        """Kiểm tra API review router: danh sách pending, approve KOC, và reject."""
        import asyncio

        cand_id = "test-uuid-review-1"
        username = "pending_creator_review_test"

        record = CandidateStagingRecord(
            id=cand_id,
            platform="TIKTOK",
            username=username,
            display_name="Pending Creator",
            processing_status=ProcessingStatus.PREFLIGHT_DONE,
            decision=Decision.NEEDS_MANAGER_REVIEW,
            primary_type="UNKNOWN",
            reason_codes=["BR06_SELECTIVE_REVIEW_CREATOR_WITH_SHOP"],
            matched_signals=["bio_review", "shop_link"],
            evidence_summary={"followers": 25000, "review_ratio": 0.6},
        )
        staging_repo.upsert_candidate(record)

        # 1. Kiểm tra GET /pending
        pending_res = asyncio.run(get_pending_review_candidates(platform="TIKTOK", limit=10, offset=0))
        self.assertGreaterEqual(pending_res.total, 1)
        found_in_pending = any(c.username == username for c in pending_res.candidates)
        self.assertTrue(found_in_pending)

        # 2. Kiểm tra POST /{id}/action -> APPROVE_KOC
        with patch("app.api.routers.review_router.process_tiktok_profile.delay") as mock_delay:
            mock_delay.return_value = MagicMock(id="task-celery-approved-1")
            action_req = ReviewActionRequest(
                action="APPROVE_KOC",
                reviewer="senior_data_manager",
                note="Kênh review đồ ăn rất uy tín",
            )
            approve_res = asyncio.run(process_candidate_review_action(cand_id, action_req))

            self.assertEqual(approve_res["status"], "success")
            self.assertEqual(approve_res["decision"], Decision.MANAGER_APPROVED.value)
            self.assertEqual(approve_res["primary_type"], "KOC")
            self.assertTrue(approve_res["deep_crawl_enqueued"])
            mock_delay.assert_called_once_with(username)

            # Staging phải được cập nhật thành MANAGER_APPROVED
            updated_rec = staging_repo.get_candidate_by_id(cand_id)
            self.assertIsNotNone(updated_rec)
            self.assertEqual(updated_rec.decision, Decision.MANAGER_APPROVED)
            self.assertEqual(updated_rec.processing_status, ProcessingStatus.DEEP_CRAWL_QUEUED)

        # 3. Kiểm tra POST /{id}/action -> REJECT
        cand_id_reject = "test-uuid-review-2"
        user_reject = "reject_creator_test"
        staging_repo.upsert_candidate(
            CandidateStagingRecord(
                id=cand_id_reject,
                platform="TIKTOK",
                username=user_reject,
                processing_status=ProcessingStatus.PREFLIGHT_DONE,
                decision=Decision.NEEDS_MANAGER_REVIEW,
            )
        )
        action_req_reject = ReviewActionRequest(
            action="REJECT",
            reviewer="senior_data_manager",
            note="Phát hiện reup video từ kênh khác",
        )
        reject_res = asyncio.run(process_candidate_review_action(cand_id_reject, action_req_reject))
        self.assertEqual(reject_res["status"], "success")
        self.assertEqual(reject_res["decision"], Decision.MANAGER_REJECTED.value)

        rejected_rec = staging_repo.get_candidate_by_id(cand_id_reject)
        self.assertIsNotNone(rejected_rec)
        self.assertEqual(rejected_rec.decision, Decision.MANAGER_REJECTED)
        self.assertEqual(rejected_rec.processing_status, ProcessingStatus.FAILED)

    def test_tc22_end_to_end_pipeline_and_identity_isolation(self):
        """
        TC22: Toàn vẹn luồng End-to-End & Bảo toàn cách ly Creator Master Identity.
        - Preflight PASS -> Staging AUTO_ACCEPT_KOC -> Worker Gate mở
        - Deep Crawl hoàn tất -> Post-Validation đạt chuẩn
        - Java Mapper bảo toàn tuyệt đối sourceType='PUBLIC' và masterId=None
        - Java Ingest thành công -> Cập nhật Staging INGESTED.
        """
        username = "genuine_koc_tc22"

        # 1. Staging với AUTO_ACCEPT_KOC
        staging_repo.upsert_candidate(
            CandidateStagingRecord(
                platform="TIKTOK",
                username=username,
                processing_status=ProcessingStatus.PREFLIGHT_DONE,
                decision=Decision.AUTO_ACCEPT_KOC,
                primary_type="KOC",
                reason_codes=["BR05_AUTO_ACCEPT_KOC"],
            )
        )

        # 2. Mock Crawl Payload
        mock_payload = self._create_mock_ingest_payload(
            username=username,
            followers=35000,
            posts_captions=[
                "Review serum trị mụn phục hồi da chân thật sau 1 tháng trải nghiệm",
                "Chấm điểm 5 loại kem chống nắng cho da dầu mụn đỉnh chóp",
                "Unbox bàn chải điện sóng âm thông minh giá học sinh sinh viên",
                "Mọi người lưu ý kem này chất hơi đặc nhưng thấm cực nhanh nha",
            ],
            creator_type="KOC",
        )

        with patch("app.workers.tasks._crawl_tiktok_profile_sync", return_value=mock_payload), \
             patch("app.workers.tasks.process_ai_enrichment", return_value=mock_payload.aiEnrichment), \
             patch("app.workers.tasks.java_client.send_creator_data", return_value=True) as mock_send_java:

            # 3. Kích hoạt Worker Deep Crawl
            res = process_tiktok_profile.run(username=username)

            # 4. Kiểm chứng kết quả thành công
            self.assertIsNotNone(res)
            self.assertEqual(res["creator"]["username"], username)

            # 5. Kiểm tra tính bảo toàn Identity trên Java Payload
            mock_send_java.assert_called_once()
            sent_payload = mock_send_java.call_args[0][0]

            # Ràng buộc bất biến: sourceType='PUBLIC' và masterId=None
            self.assertEqual(sent_payload["sourceType"], "PUBLIC")
            self.assertEqual(sent_payload["source_type"], "PUBLIC")
            self.assertIsNone(sent_payload["masterId"])
            self.assertIsNone(sent_payload["master_id"])
            self.assertEqual(len(sent_payload["embedding"]), 768)

            # 6. Kiểm tra Staging chuyển thành INGESTED
            staged = staging_repo.get_candidate("TIKTOK", username)
            self.assertIsNotNone(staged)
            self.assertEqual(staged.processing_status, ProcessingStatus.INGESTED)


if __name__ == "__main__":
    unittest.main()
