import os
import sys
import time
import unittest
from io import StringIO

sys.path.insert(0, os.path.abspath("."))

from tests.test_rule_engine import TestRuleEngine
from tests.test_preflight_staging import TestPreflightAndStaging
from tests.test_celery_gates import TestCeleryGatesAndPipeline
from tests.test_post_validation import TestPostValidationAndSafeIngestion


MANDATORY_TEST_CASES = [
    # Phase 1: Core Classification Rules (BR-01 -> BR-05)
    {"id": "TC01", "name": "KOC review mỹ phẩm rõ ràng -> AUTO_ACCEPT_KOC", "class": TestRuleEngine, "method": "test_tc01_koc_clear_beauty_review", "phase": "Phase 1"},
    {"id": "TC02", "name": "KOC review ẩm thực không có affiliate -> AUTO_ACCEPT_KOC", "class": TestRuleEngine, "method": "test_tc02_koc_food_review_without_affiliate", "phase": "Phase 1"},
    {"id": "TC03", "name": "KOC 300K followers có booking -> Không tự biến thành KOL", "class": TestRuleEngine, "method": "test_tc03_koc_high_followers_with_booking", "phase": "Phase 1"},
    {"id": "TC04", "name": "KOL bác sĩ da liễu 45K followers chia sẻ chuyên môn -> AUTO_ACCEPT_KOL", "class": TestRuleEngine, "method": "test_tc04_kol_expertise_with_independent_evidence", "phase": "Phase 1"},
    {"id": "TC05", "name": "Meme/Reup 500K followers chỉ có email booking -> Chặn KOL", "class": TestRuleEngine, "method": "test_tc05_meme_account_high_followers_only_booking", "phase": "Phase 1"},
    {"id": "TC06", "name": "Official Store catalog sản phẩm -> AUTO_REJECT", "class": TestRuleEngine, "method": "test_tc06_official_store_catalog", "phase": "Phase 1"},
    {"id": "TC07", "name": "Cơ quan báo chí / đài truyền hình nhà nước -> AUTO_REJECT", "class": TestRuleEngine, "method": "test_tc07_state_media_organization", "phase": "Phase 1"},
    {"id": "TC08", "name": "Creator có shop cá nhân vẫn review thật -> Không reject nhầm", "class": TestRuleEngine, "method": "test_tc08_creator_with_own_shop_still_reviews", "phase": "Phase 1"},
    {"id": "TC09", "name": "Chỉ có linktree / keyword review ở bio mà video không có review", "class": TestRuleEngine, "method": "test_tc09_only_linktree_or_review_keyword", "phase": "Phase 1"},
    {"id": "TC10", "name": "Tín hiệu KOC và KOL đồng thời mạnh -> NEEDS_MANAGER_REVIEW", "class": TestRuleEngine, "method": "test_tc10_dual_koc_kol_signals", "phase": "Phase 1"},
    {"id": "TC11", "name": "Bio mơ hồ, không có bài viết -> SKIP_INSUFFICIENT", "class": TestRuleEngine, "method": "test_tc11_insufficient_vague_bio", "phase": "Phase 1"},
    {"id": "TC12", "name": "Preflight chỉ có 2 bài thay vì 5 bài -> Không tự coi đạt", "class": TestRuleEngine, "method": "test_tc12_insufficient_posts_count", "phase": "Phase 1"},

    # Phase 2: Lightweight Preflight & Staging
    {"id": "TC13", "name": "Staging WAL Concurrency, Redis Atomic Lock & Deduplication", "class": TestPreflightAndStaging, "method": "test_staging_upsert_and_get", "phase": "Phase 2"},

    # Phase 3: Celery Pipeline & Worker Deep Crawl Gate
    {"id": "TC14", "name": "Candidate AUTO_REJECT -> Không tạo Deep Crawl Task", "class": TestCeleryGatesAndPipeline, "method": "test_tc14_auto_reject_does_not_enqueue_deep_crawl", "phase": "Phase 3"},
    {"id": "TC15", "name": "Candidate NEEDS_MANAGER_REVIEW chưa duyệt -> Không kích hoạt Deep Crawl", "class": TestCeleryGatesAndPipeline, "method": "test_tc15_needs_manager_review_does_not_enqueue_deep_crawl", "phase": "Phase 3"},
    {"id": "TC16", "name": "Data Manager APPROVE -> Kích hoạt duy nhất 1 Deep Crawl Task", "class": TestCeleryGatesAndPipeline, "method": "test_tc16_manager_approved_triggers_single_deep_crawl_and_passes_gate", "phase": "Phase 3"},
    {"id": "TC20", "name": "Direct Deep Crawl call bị chặn đứng tại Worker Gate (TikTok/FB/IG)", "class": TestCeleryGatesAndPipeline, "method": "test_tc20_unauthorized_direct_call_is_blocked_at_gate", "phase": "Phase 3"},

    # Phase 4: Post-Validation, Review Action & Identity Preservation
    {"id": "TC17", "name": "Deep Crawl xong nhưng phát hiện catalog shop -> Chặn tại Post-Validation", "class": TestPostValidationAndSafeIngestion, "method": "test_tc17_commercial_shop_catalog_blocked_at_post_validation", "phase": "Phase 4"},
    {"id": "TC18", "name": "Candidate đạt mọi tiêu chuẩn Post-Validation -> Gửi thành công sang Java", "class": TestPostValidationAndSafeIngestion, "method": "test_tc18_valid_candidate_passes_post_validation_and_ingests_to_java", "phase": "Phase 4"},
    {"id": "TC19", "name": "Candidate có tín hiệu KOC & KOL đồng thời -> Xử lý nhất quán, không lỗi", "class": TestPostValidationAndSafeIngestion, "method": "test_tc19_dual_koc_kol_signals_consistent_handling", "phase": "Phase 4"},
    {"id": "TC21", "name": "Luồng Refresh tài khoản cũ (is_refresh=True) -> Bảo toàn metrics, không duplicate", "class": TestPostValidationAndSafeIngestion, "method": "test_tc21_refresh_flow_bypasses_follower_gate_and_no_duplicates", "phase": "Phase 4"},
    {"id": "TC22", "name": "Toàn vẹn luồng End-to-End & Cách ly danh tính Master Profile (sourceType='PUBLIC')", "class": TestPostValidationAndSafeIngestion, "method": "test_tc22_end_to_end_pipeline_and_identity_isolation", "phase": "Phase 4"}
]


def run_all_mandatory_tests():
    print("=" * 105)
    print("       INFLUENCERMATCH AI - PRE-CRAWL KOC/KOL HARD FILTER SUITE (22 MANDATORY TEST CASES)")
    print("=" * 105)
    print(f"{'TC ID':<6} | {'Phase':<8} | {'Status':<8} | {'Time (ms)':<10} | {'Test Case Description':<60}")
    print("-" * 105)

    passed_count = 0
    failed_count = 0
    total_start_time = time.time()

    for tc in MANDATORY_TEST_CASES:
        suite = unittest.TestSuite()
        suite.addTest(tc["class"](tc["method"]))
        
        # Capture standard output to keep matrix display clean
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = StringIO()
        sys.stderr = StringIO()

        t0 = time.time()
        result = unittest.TestResult()
        suite.run(result)
        elapsed_ms = (time.time() - t0) * 1000

        sys.stdout = old_stdout
        sys.stderr = old_stderr

        is_passed = result.wasSuccessful()
        if is_passed:
            passed_count += 1
            status_str = "✅ PASS"
        else:
            failed_count += 1
            status_str = "❌ FAIL"

        print(f"{tc['id']:<6} | {tc['phase']:<8} | {status_str:<8} | {elapsed_ms:>8.2f}ms | {tc['name']:<60}")

    total_elapsed = time.time() - total_start_time
    print("=" * 105)
    print(f"📊 SUMMARY: Total: {len(MANDATORY_TEST_CASES)} | Passed: {passed_count} | Failed: {failed_count} | Total Time: {total_elapsed:.2f}s")
    success_rate = (passed_count / len(MANDATORY_TEST_CASES)) * 100
    print(f"🎯 SUCCESS RATE: {success_rate:.1f}%")
    if passed_count == len(MANDATORY_TEST_CASES):
        print("🏆 ALL 22 MANDATORY TEST CASES PASSED WITH 100% ACCURACY!")
    print("=" * 105)

    return failed_count == 0


if __name__ == "__main__":
    success = run_all_mandatory_tests()
    sys.exit(0 if success else 1)
