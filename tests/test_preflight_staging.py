import unittest
from unittest.mock import patch

from app.ai.rules.rule_config import Decision, ProcessingStatus
from app.ai.rules.signal_extractor import PostPreview, ProfileMetadata
from app.core.candidate_staging import CandidateStagingRecord, staging_repo
from app.core.deduplicator import (
    acquire_candidate_lock,
    release_candidate_lock,
    should_enqueue_preflight,
)
from app.crawlers.search_extractor import (
    evaluate_tier0_search_card,
    extract_search_card_candidates,
    parse_follower_string,
)
from app.services.preflight_service import PreflightService


class TestPreflightAndStaging(unittest.TestCase):

    def test_staging_upsert_and_get(self):
        """Kiểm tra ghi và đọc CandidateStagingRecord trong SQLite WAL."""
        record = CandidateStagingRecord(
            platform="TIKTOK",
            external_id="123456789",
            username="test_staging_user",
            display_name="Test Staging",
            processing_status=ProcessingStatus.PREFLIGHT_DONE,
            decision=Decision.AUTO_ACCEPT_KOC,
            primary_type="KOC",
            reason_codes=["TEST_REASON_CODE"],
        )
        staging_repo.upsert_candidate(record)

        retrieved = staging_repo.get_candidate("TIKTOK", "test_staging_user")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.username, "test_staging_user")
        self.assertEqual(retrieved.decision, Decision.AUTO_ACCEPT_KOC)
        self.assertEqual(retrieved.primary_type, "KOC")
        self.assertIn("TEST_REASON_CODE", retrieved.reason_codes)

    def test_staging_update_status(self):
        """Kiểm tra cập nhật trạng thái và quyết định."""
        initial = CandidateStagingRecord(
            platform="TIKTOK",
            username="test_update_user",
            processing_status=ProcessingStatus.DISCOVERED,
            decision=Decision.SKIP_INSUFFICIENT,
        )
        staging_repo.upsert_candidate(initial)

        staging_repo.update_status_and_decision(
            platform="TIKTOK",
            username="test_update_user",
            status=ProcessingStatus.DEEP_CRAWLING,
            decision=Decision.MANAGER_APPROVED,
        )
        updated = staging_repo.get_candidate("TIKTOK", "test_update_user")
        self.assertIsNotNone(updated)
        self.assertEqual(updated.processing_status, ProcessingStatus.DEEP_CRAWLING)
        self.assertEqual(updated.decision, Decision.MANAGER_APPROVED)

    def test_redis_atomic_lock_collision(self):
        """Kiểm tra khóa nguyên tử không cho 2 worker chạy cùng lúc."""
        user = "concurrent_user_99"
        # Worker 1 chiếm khóa thành công
        acquired1 = acquire_candidate_lock("TIKTOK", user, ttl_seconds=60)
        self.assertTrue(acquired1)

        # Worker 2 cố chiếm khóa -> Thất bại
        acquired2 = acquire_candidate_lock("TIKTOK", user, ttl_seconds=60)
        self.assertFalse(acquired2)

        # Giải phóng khóa
        release_candidate_lock("TIKTOK", user)

        # Worker 2 thử lại -> Thành công
        acquired3 = acquire_candidate_lock("TIKTOK", user, ttl_seconds=60)
        self.assertTrue(acquired3)
        release_candidate_lock("TIKTOK", user)

    def test_dedup_should_enqueue(self):
        """Kiểm tra cơ chế loại trừ candidate đã bị Reject gần đây."""
        reject_user = "rejected_store_user"
        record = CandidateStagingRecord(
            platform="TIKTOK",
            username=reject_user,
            processing_status=ProcessingStatus.PREFLIGHT_DONE,
            decision=Decision.AUTO_REJECT,
            primary_type="BRAND",
        )
        staging_repo.upsert_candidate(record)

        can_enqueue, reason = should_enqueue_preflight("TIKTOK", reject_user, cooldown_days=7)
        self.assertFalse(can_enqueue)
        self.assertEqual(reason, "RECENTLY_AUTO_REJECTED")

    def test_parse_follower_string(self):
        """Kiểm tra parse chuỗi follower đa định dạng."""
        self.assertEqual(parse_follower_string("120.5K"), 120500)
        self.assertEqual(parse_follower_string("1.2M"), 1200000)
        self.assertEqual(parse_follower_string("5.5 Triệu"), 5500000)
        self.assertEqual(parse_follower_string("5000"), 5000)
        self.assertEqual(parse_follower_string(""), 0)

    def test_tier0_search_card_filter(self):
        """Kiểm tra bộ lọc Nấc 0 không tốn HTTP request."""
        # 1. Candidate dưới 1000 followers -> Bị loại
        cand_low = {"username": "low_fl", "bio_snippet": "vlog đời sống", "followers": 500}
        pass1, reason1 = evaluate_tier0_search_card(cand_low)
        self.assertFalse(pass1)
        self.assertIn("DROP_LOW_FOLLOWERS", reason1)

        # 2. Candidate là tổng kho sỉ lẻ -> Bị loại
        cand_shop = {"username": "tong_kho", "bio_snippet": "chuyên sỉ mỹ phẩm giá xưởng", "followers": 50000}
        pass2, reason2 = evaluate_tier0_search_card(cand_shop)
        self.assertFalse(pass2)
        self.assertEqual(reason2, "DROP_COMMERCIAL_SELLER_SNIPPET")

        # 3. Candidate là KOC tiềm năng -> Vượt qua Nấc 0
        cand_koc = {"username": "koc_review", "bio_snippet": "kênh review đồ công nghệ và unbox", "followers": 80000}
        pass3, reason3 = evaluate_tier0_search_card(cand_koc)
        self.assertTrue(pass3)
        self.assertEqual(reason3, "PASS_TIER0_PREFILTER")

    @patch("app.services.preflight_service.fetch_tiktok_preflight")
    def test_preflight_service_auto_accept_flow(self, mock_fetch):
        """Kiểm tra điều phối Preflight Service thành công với KOC."""
        mock_profile = ProfileMetadata(
            username="service_koc",
            display_name="Service KOC Review",
            bio="Review đồ gia dụng chân thực",
            followers=50000,
            bio_links=["https://beacons.ai/service_koc"],
        )
        mock_posts = [
            PostPreview(caption="Review robot hút bụi, ưu điểm êm, nhược điểm app chậm."),
            PostPreview(caption="Đánh giá nồi chiên không dầu, chấm 9/10 rất đáng mua."),
            PostPreview(caption="Unboxing máy ép hoa quả."),
            PostPreview(caption="So sánh 2 dòng máy lọc không khí mini."),
            PostPreview(caption="Trải nghiệm máy sấy tóc ion âm."),
        ]
        mock_fetch.return_value = (mock_profile, mock_posts)

        result = PreflightService.run_preflight("TIKTOK", "service_koc")
        self.assertEqual(result.decision, Decision.AUTO_ACCEPT_KOC)

        # Kiểm tra bản ghi trong DB Staging
        staged = staging_repo.get_candidate("TIKTOK", "service_koc")
        self.assertIsNotNone(staged)
        self.assertEqual(staged.decision, Decision.AUTO_ACCEPT_KOC)
        self.assertEqual(staged.processing_status, ProcessingStatus.PREFLIGHT_DONE)


if __name__ == "__main__":
    unittest.main()
