from datetime import UTC, datetime
from app.ai.rules.rule_config import Decision, ProcessingStatus, MAX_PREFLIGHT_RETRIES, RULE_VERSION
from app.ai.rules.rule_engine import RuleDecisionResult, RuleEngine
from app.core.candidate_staging import CandidateStagingRecord, staging_repo
from app.core.deduplicator import acquire_candidate_lock, release_candidate_lock, should_enqueue_preflight
from app.core.logger import setup_logger
from app.crawlers.preflight_fetcher import (
    fetch_facebook_preflight,
    fetch_instagram_preflight,
    fetch_tiktok_preflight,
)

logger = setup_logger("preflight_service")


class PreflightService:
    """
    Điều phối luồng Lightweight Preflight:
    - Kiểm tra Deduplication & Lock.
    - Gọi Lightweight Fetcher.
    - Đưa qua Rule Engine.
    - Lưu Staging DB phục vụ Celery Deep Crawl Gate.
    """

    @classmethod
    def run_preflight(
        cls, platform: str, username: str, is_refresh: bool = False
    ) -> RuleDecisionResult:
        clean_user = username.removeprefix("@").strip()
        platform_upper = platform.upper()

        logger.info(f"[PREFLIGHT SERVICE] Bắt đầu Preflight cho {platform_upper}: @{clean_user}")

        # 1. Kiểm tra điều kiện đưa vào xử lý
        if not is_refresh:
            can_enqueue, reason = should_enqueue_preflight(platform_upper, clean_user)
            if not can_enqueue:
                logger.info(f"[PREFLIGHT SERVICE] Bỏ qua @{clean_user} do: {reason}")
                existing = staging_repo.get_candidate(platform_upper, clean_user)
                if existing:
                    return RuleDecisionResult(
                        decision=existing.decision,
                        primary_type=existing.primary_type,
                        reason_codes=existing.reason_codes,
                        matched_signals=existing.matched_signals,
                        evidence=existing.evidence_summary,
                    )
                return RuleDecisionResult(
                    decision=Decision.SKIP_INSUFFICIENT,
                    primary_type="UNKNOWN",
                    reason_codes=[reason],
                )

        # 2. Chiếm khóa nguyên tử Redis
        if not acquire_candidate_lock(platform_upper, clean_user, ttl_seconds=180):
            logger.warning(f"[PREFLIGHT SERVICE] Candidate @{clean_user} đang được xử lý bởi worker khác!")
            return RuleDecisionResult(
                decision=Decision.RETRY_PREFLIGHT,
                primary_type="UNKNOWN",
                reason_codes=["CONCURRENT_WORKER_LOCKED"],
            )

        try:
            # Cập nhật trạng thái đang chạy Preflight
            existing_record = staging_repo.get_candidate(platform_upper, clean_user)
            retry_count = existing_record.retry_count if existing_record else 0

            staging_repo.update_status_and_decision(
                platform=platform_upper,
                username=clean_user,
                status=ProcessingStatus.PREFLIGHT_RUNNING,
            )

            # 3. Gọi Lightweight Fetcher theo platform
            fetch_result = None
            if platform_upper == "TIKTOK":
                fetch_result = fetch_tiktok_preflight(clean_user)
            elif platform_upper == "FACEBOOK":
                fetch_result = fetch_facebook_preflight(clean_user)
            elif platform_upper == "INSTAGRAM":
                fetch_result = fetch_instagram_preflight(clean_user)

            # Xử lý khi Fetch thất bại
            if not fetch_result:
                retry_count += 1
                decision = Decision.RETRY_PREFLIGHT if retry_count < MAX_PREFLIGHT_RETRIES else Decision.SKIP_INSUFFICIENT
                reasons = [f"FETCH_FAILED_RETRY_{retry_count}"]

                staging_repo.upsert_candidate(
                    CandidateStagingRecord(
                        platform=platform_upper,
                        username=clean_user,
                        processing_status=ProcessingStatus.FAILED if retry_count >= MAX_PREFLIGHT_RETRIES else ProcessingStatus.DISCOVERED,
                        decision=decision,
                        primary_type="UNKNOWN",
                        reason_codes=reasons,
                        retry_count=retry_count,
                    )
                )
                return RuleDecisionResult(
                    decision=decision,
                    primary_type="UNKNOWN",
                    reason_codes=reasons,
                )

            profile, posts = fetch_result

            # 4. Nếu là luồng Refresh Creator đã có trong hệ thống -> Bỏ qua Hard Reject, cho phép tiếp tục
            if is_refresh:
                logger.info(f"[PREFLIGHT SERVICE] @{clean_user} là luồng Refresh -> Giữ nguyên quyết định hợp lệ.")
                decision_result = RuleDecisionResult(
                    decision=Decision.AUTO_ACCEPT_KOC,
                    primary_type="KOC",
                    reason_codes=["REFRESH_EXISTING_CREATOR"],
                )
            else:
                # 5. Đưa qua High-Precision Rule Engine
                decision_result = RuleEngine.evaluate(profile, posts)

            # 6. Lưu kết quả vào Staging Repository
            candidate_record = CandidateStagingRecord(
                platform=platform_upper,
                external_id=profile.external_id,
                username=clean_user,
                display_name=profile.display_name,
                profile_url=f"https://www.tiktok.com/@{clean_user}" if platform_upper == "TIKTOK" else f"https://www.facebook.com/{clean_user}",
                processing_status=ProcessingStatus.PREFLIGHT_DONE,
                decision=decision_result.decision,
                primary_type=decision_result.primary_type,
                reason_codes=decision_result.reason_codes,
                matched_signals=decision_result.matched_signals,
                evidence_summary=decision_result.evidence,
                rule_version=RULE_VERSION,
                retry_count=retry_count,
                last_checked_at=datetime.now(UTC).isoformat(),
            )
            staging_repo.upsert_candidate(candidate_record)

            logger.info(
                f"[PREFLIGHT SERVICE] Hoàn thành @{clean_user} -> Decision: {decision_result.decision.value} "
                f"({decision_result.primary_type}) | Lý do: {decision_result.reason_codes}"
            )
            return decision_result

        finally:
            release_candidate_lock(platform_upper, clean_user)


preflight_service = PreflightService()
