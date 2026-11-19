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
    fetch_tiktok_author_info,
)

logger = setup_logger("preflight_service")

_PROFILE_URL_TEMPLATES = {
    "TIKTOK": "https://www.tiktok.com/@{username}",
    "FACEBOOK": "https://www.facebook.com/{username}",
    "INSTAGRAM": "https://www.instagram.com/{username}",
}

_REFRESH_DECISION_BY_TYPE = {
    "KOL": (Decision.AUTO_ACCEPT_KOL, "KOL"),
    "KOC": (Decision.AUTO_ACCEPT_KOC, "KOC"),
}


def _get_preflight_fetcher(platform_upper: str):
    return {
        "TIKTOK": fetch_tiktok_preflight,
        "FACEBOOK": fetch_facebook_preflight,
        "INSTAGRAM": fetch_instagram_preflight,
    }.get(platform_upper)


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

            # 3. Gọi Lightweight Fetcher theo platform (Declarative lookup)
            fetcher_fn = _get_preflight_fetcher(platform_upper)
            fetch_result = fetcher_fn(clean_user) if fetcher_fn else None

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

            # 4. Nếu là luồng Refresh Creator đã có trong hệ thống -> Bảo toàn loại hình KOL/KOC
            if is_refresh:
                logger.info(f"[PREFLIGHT SERVICE] @{clean_user} là luồng Refresh -> Giữ nguyên quyết định hợp lệ.")
                existing_type = getattr(existing_record, "primary_type", "KOC")
                ref_decision, ref_type = _REFRESH_DECISION_BY_TYPE.get(
                    existing_type, (Decision.AUTO_ACCEPT_KOC, "KOC")
                )
                decision_result = RuleDecisionResult(
                    decision=ref_decision,
                    primary_type=ref_type,
                    reason_codes=["REFRESH_EXISTING_CREATOR"],
                )
            else:
                # 5. Đưa qua High-Precision Rule Engine
                decision_result = RuleEngine.evaluate(profile, posts)

            if not is_refresh and decision_result.decision in {
                Decision.SKIP_INSUFFICIENT, Decision.NEEDS_MANAGER_REVIEW
            }:
                from app.ai.analyzer import get_llm_enrichment
                from app.services.ai_candidate_review import review_candidate
                try:
                    decision_result = review_candidate(
                        profile, posts, decision_result, get_llm_enrichment
                    )
                except Exception as exc:
                    logger.warning("AI second opinion failed: %s", type(exc).__name__)

            # 6. Lưu kết quả vào Staging Repository
            url_template = _PROFILE_URL_TEMPLATES.get(
                platform_upper, "https://www.tiktok.com/@{username}"
            )
            candidate_record = CandidateStagingRecord(
                platform=platform_upper,
                external_id=profile.external_id,
                username=clean_user,
                display_name=profile.display_name,
                profile_url=url_template.format(username=clean_user),
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


    @classmethod
    def resolve_author_id(
        cls,
        platform: str = "TIKTOK",
        username: str = "",
        expected_external_id: str | None = None,
        auto_update_staging: bool = True,
    ) -> dict:
        """
        Lightweight Preflight SSR Author ID Resolution & Verification:
        - Bóc tách dãy ID số cố định (Author ID / external_id) từ thẻ SSR __UNIVERSAL_DATA_FOR_REHYDRATION__.
        - Phục vụ đối soát danh tính KOC khi KOC đổi username (ví dụ: mixigaming -> domixi) trước khi xác thực qua TikTok OAuth.
        - So sánh với expected_external_id (nếu truyền vào).
        - Tra cứu trong Staging DB xem có KOC nào có cùng external_id.
        - Tự động cập nhật username mới vào Staging DB nếu có cờ auto_update_staging.
        """
        platform_upper = (platform or "TIKTOK").upper()
        clean_user = username.strip().removeprefix("@")
        if not clean_user:
            return {
                "status": "error",
                "message": "Username không được để trống",
            }

        if platform_upper != "TIKTOK":
            return {
                "status": "error",
                "message": f"Nền tảng {platform_upper} chưa hỗ trợ resolve Author ID qua SSR. Hiện chỉ hỗ trợ TIKTOK.",
            }

        logger.info(f"[PREFLIGHT AUTHOR RESOLVER] Đang bóc tách SSR Author ID cho @{clean_user}...")
        author_info = fetch_tiktok_author_info(clean_user)
        if not author_info or not author_info.get("author_id"):
            logger.warning(f"[PREFLIGHT AUTHOR RESOLVER] Không thể lấy Author ID từ SSR cho @{clean_user}")
            return {
                "status": "error",
                "platform": platform_upper,
                "input_username": clean_user,
                "message": f"Không thể lấy Author ID từ SSR cho @{clean_user}. Kênh có thể không tồn tại hoặc bị chặn bởi TikTok.",
            }

        author_id = author_info["author_id"]
        resolved_username = author_info.get("unique_id") or clean_user
        display_name = author_info.get("display_name", "")

        is_matched = None
        previous_username = None
        matched_candidate = None
        message = ""

        # 1. Tìm bản ghi hiện có trong Staging DB theo external_id
        existing_candidate = staging_repo.get_candidate_by_external_id(platform_upper, author_id)
        if existing_candidate:
            previous_username = existing_candidate.username
            matched_candidate = {
                "id": existing_candidate.id,
                "previous_username": previous_username,
                "display_name": existing_candidate.display_name,
                "decision": existing_candidate.decision.value,
                "primary_type": existing_candidate.primary_type,
            }

        # 2. Trường hợp A: Backend truyền expected_external_id để xác minh
        if expected_external_id:
            exp_clean = str(expected_external_id).strip()
            if author_id == exp_clean:
                is_matched = True
                message = f"Author ID '{author_id}' trùng khớp 100% với expected_external_id. KOC @{clean_user} đã được đối soát thành công."
                if auto_update_staging and existing_candidate and previous_username != clean_user:
                    staging_repo.update_candidate_username(
                        platform=platform_upper,
                        external_id=author_id,
                        new_username=clean_user,
                        new_display_name=display_name,
                    )
                    message += f" Đã cập nhật username từ @{previous_username} thành @{clean_user} trong Staging DB."
            else:
                is_matched = False
                message = f"Author ID không khớp! Kênh @{clean_user} có ID={author_id}, khác với expected_external_id={exp_clean}."

        # 3. Trường hợp B: Không truyền expected_external_id -> Tự động tìm trong hệ thống
        else:
            if existing_candidate:
                is_matched = True
                if previous_username != clean_user:
                    message = f"Phát hiện KOC đổi tên! Kênh này trước đây có username là @{previous_username}, hiện tại là @{clean_user} (Author ID: {author_id})."
                    if auto_update_staging:
                        staging_repo.update_candidate_username(
                            platform=platform_upper,
                            external_id=author_id,
                            new_username=clean_user,
                            new_display_name=display_name,
                        )
                        message += " Đã cập nhật username mới vào Staging DB."
                else:
                    message = f"KOC @{clean_user} đã tồn tại trong hệ thống với Author ID: {author_id}."
            else:
                is_matched = None
                message = f"Đã bóc tách thành công Author ID: {author_id} cho @{clean_user}. Sẵn sàng để đối soát trong DB chính."

        return {
            "status": "success",
            "platform": platform_upper,
            "input_username": clean_user,
            "resolved_username": resolved_username,
            "author_id": author_id,
            "sec_uid": author_info.get("sec_uid", ""),
            "expected_external_id": expected_external_id,
            "is_matched": is_matched,
            "previous_username": previous_username,
            "matched_staging_candidate": matched_candidate,
            "creator_info": {
                "display_name": display_name,
                "avatar_url": author_info.get("avatar_url", ""),
                "followers": author_info.get("followers", 0),
                "following": author_info.get("following", 0),
                "heart_count": author_info.get("heart_count", 0),
                "video_count": author_info.get("video_count", 0),
                "bio": author_info.get("bio", ""),
                "bio_link": author_info.get("bio_link", ""),
                "is_verified": author_info.get("is_verified", False),
                "profile_url": author_info.get("profile_url", ""),
            },
            "message": message,
        }


preflight_service = PreflightService()
