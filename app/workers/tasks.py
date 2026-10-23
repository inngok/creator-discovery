import json
from typing import Optional

from app.ai.analyzer import process_ai_enrichment
from app.ai.rules.post_validator import validate_post_crawl
from app.ai.rules.rule_config import Decision, ProcessingStatus
from app.clients.java_client import java_client
from app.core.candidate_staging import staging_repo
from app.core.logger import setup_logger
from app.crawlers.facebook_crawler import _crawl_facebook_profile_sync
from app.crawlers.instagram_crawler import _crawl_instagram_profile_sync
from app.crawlers.tiktok_crawler import _crawl_tiktok_profile_sync
from app.mappers.java_mapper import JavaMapper
from app.services.preflight_service import preflight_service

from .celery_app import app

logger = setup_logger("celery_tasks")


# ==============================================================================
# PHA 1: CELERY PREFLIGHT TASK (Siêu nhẹ - chỉ kiểm tra & sàng lọc ban đầu)
# ==============================================================================
@app.task(bind=True, max_retries=2, queue="preflight_queue")
def preflight_candidate_task(
    self, platform: str, username: str, is_refresh: bool = False
) -> dict:
    """
    PHA 1: CELERY PREFLIGHT TASK (Siêu nhẹ)
    - Kiểm tra Deduplication & Lock
    - Chạy Lightweight Fetcher (chỉ tải SSR 1.5 - 3s)
    - Phân loại qua High-Precision Rule Engine (BR-01 đến BR-06)
    - Nếu AUTO_ACCEPT_KOC hoặc AUTO_ACCEPT_KOL -> Enqueue Deep Crawl
    - Nếu AUTO_REJECT hoặc NEEDS_MANAGER_REVIEW -> KHÔNG kích hoạt Deep Crawl
    """
    clean_user = username.removeprefix("@").strip()
    logger.info(f"[CELERY PREFLIGHT] Đang xử lý {platform}: @{clean_user}")
    try:
        decision_result = preflight_service.run_preflight(
            platform=platform, username=clean_user, is_refresh=is_refresh
        )

        decision = decision_result.decision
        if decision in [
            Decision.AUTO_ACCEPT_KOC,
            Decision.AUTO_ACCEPT_KOL,
            Decision.MANAGER_APPROVED,
        ]:
            staging_repo.update_status_and_decision(
                platform=platform.upper(),
                username=clean_user,
                status=ProcessingStatus.DEEP_CRAWL_QUEUED,
                decision=decision,
            )
            # Kích hoạt Deep Crawl vào hàng đợi deep_crawl_queue
            if platform.upper() == "TIKTOK":
                process_tiktok_profile.apply_async(args=[clean_user], queue="deep_crawl_queue")
            elif platform.upper() == "FACEBOOK":
                process_facebook_profile.apply_async(args=[clean_user], queue="deep_crawl_queue")
            elif platform.upper() == "INSTAGRAM":
                process_instagram_profile.apply_async(args=[clean_user], queue="deep_crawl_queue")

            logger.info(
                f"[CELERY PREFLIGHT GATE] Đã kích hoạt Deep Crawl cho @{clean_user} (Decision: {decision.value})"
            )
        elif decision == Decision.RETRY_PREFLIGHT:
            raise self.retry(countdown=15)
        else:
            logger.info(
                f"[CELERY PREFLIGHT GATE] KHÔNG Deep Crawl @{clean_user} do Decision: {decision.value} "
                f"(Lý do: {decision_result.reason_codes})"
            )

        return {
            "platform": platform,
            "username": clean_user,
            "decision": decision.value,
            "primary_type": decision_result.primary_type,
            "reason_codes": decision_result.reason_codes,
        }
    except Exception as e:
        logger.error(f"[CELERY PREFLIGHT ERROR] @{clean_user}: {e}")
        raise self.retry(exc=e, countdown=15)


# ==============================================================================
# PHA 2: CELERY DEEP CRAWL TASKS (Cào sâu đầy đủ - Chỉ chạy khi đã qua GATE)
# ==============================================================================
@app.task(bind=True, max_retries=3, queue="deep_crawl_queue")
def process_tiktok_profile(self, username: str, is_refresh: bool = False) -> Optional[dict]:
    clean_user = username.removeprefix("@").strip()
    logger.info(f"[WORKER] Starting TikTok crawl check for: @{clean_user}")

    # 🛡️ CELERY DEEP CRAWL GATE (Double-Check Guard)
    staging_record = staging_repo.get_candidate("TIKTOK", clean_user)
    is_authorized = (
        is_refresh
        or (
            staging_record is not None
            and staging_record.decision in [
                Decision.AUTO_ACCEPT_KOC,
                Decision.AUTO_ACCEPT_KOL,
                Decision.MANAGER_APPROVED,
            ]
        )
    )
    if not is_authorized:
        logger.error(
            f"[DEEP CRAWL GATE BLOCKED] Từ chối Deep Crawl @{clean_user}: "
            f"Chưa qua Preflight hoặc chưa được duyệt! (Staging Decision: {getattr(staging_record, 'decision', 'NONE')})"
        )
        return {
            "status": "BLOCKED",
            "reason": "UNAUTHORIZED_DEEP_CRAWL",
            "username": clean_user,
        }

    # Cập nhật trạng thái đang Deep Crawl
    staging_repo.update_status_and_decision(
        platform="TIKTOK",
        username=clean_user,
        status=ProcessingStatus.DEEP_CRAWLING,
    )

    try:
        result = _crawl_tiktok_profile_sync(clean_user)

        if result:
            # 🛡️ POST-CRAWL VALIDATION GUARD
            post_val = validate_post_crawl(result, is_refresh=is_refresh)
            if not post_val.is_valid:
                logger.warning(
                    f"[WORKER] QUARANTINE @{clean_user}: Không đạt Post-Validation Guard "
                    f"(Lý do: {post_val.quarantine_reason}, Chi tiết: {post_val.details})"
                )
                staging_repo.update_status_and_decision(
                    platform="TIKTOK",
                    username=clean_user,
                    status=ProcessingStatus.FAILED,
                    decision=Decision.AUTO_REJECT,
                    reason_codes=[post_val.quarantine_reason or "QUARANTINE_POST_VALIDATION_FAILED"],
                )
                return {
                    "status": "QUARANTINE_REJECTED",
                    "reason": post_val.quarantine_reason,
                    "details": post_val.details,
                    "username": clean_user,
                }

            # Chạy AI ở background worker
            result.aiEnrichment = process_ai_enrichment(
                result.creator, result.recentPosts
            )

            with open("data/crawled_results.jsonl", "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
                    + "\n"
                )

            creator_type = (
                getattr(result.aiEnrichment, "creatorType", "UNKNOWN")
                if result.aiEnrichment
                else "UNKNOWN"
            )

            # Chốt chặn nghiêm ngặt: Chỉ gửi KOL/KOC sang Java Backend
            if creator_type not in ["KOL", "KOC"]:
                logger.warning(
                    f"[WORKER] SKIP INGEST @{clean_user}: Bỏ qua vì không phải KOL/KOC (Phân loại: {creator_type})."
                )
                staging_repo.update_status_and_decision(
                    platform="TIKTOK",
                    username=clean_user,
                    status=ProcessingStatus.FAILED,
                    decision=Decision.AUTO_REJECT,
                    primary_type=creator_type,
                    reason_codes=["POST_VALIDATION_NOT_KOC_KOL"],
                )
                return result.model_dump(mode="json")

            java_payload = JavaMapper.from_ingest_payload(result)

            with open("data/java_payloads_debug.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(java_payload, ensure_ascii=False) + "\n")

            sent_ok = java_client.send_creator_data(java_payload)
            if sent_ok:
                staging_repo.update_status_and_decision(
                    platform="TIKTOK",
                    username=clean_user,
                    status=ProcessingStatus.INGESTED,
                )

            return result.model_dump(mode="json")
        else:
            logger.warning(f"[WORKER] FAILED: Could not crawl @{clean_user}. Retrying...")
            raise self.retry(countdown=10)
    except Exception as e:
        logger.error(f"[WORKER] ERROR processing @{clean_user}: {e}")
        raise self.retry(exc=e, countdown=10)


@app.task(bind=True, max_retries=3, queue="deep_crawl_queue")
def process_facebook_profile(self, username: str, is_refresh: bool = False) -> Optional[dict]:
    clean_user = username.removeprefix("@").strip()
    logger.info(f"[WORKER] Starting Facebook crawl check for: {clean_user}")

    # 🛡️ CELERY DEEP CRAWL GATE (Double-Check Guard)
    staging_record = staging_repo.get_candidate("FACEBOOK", clean_user)
    is_authorized = (
        is_refresh
        or (
            staging_record is not None
            and staging_record.decision in [
                Decision.AUTO_ACCEPT_KOC,
                Decision.AUTO_ACCEPT_KOL,
                Decision.MANAGER_APPROVED,
            ]
        )
    )
    if not is_authorized:
        logger.error(
            f"[DEEP CRAWL GATE BLOCKED] Từ chối Deep Crawl FB {clean_user}: "
            f"Chưa qua Preflight hoặc chưa được duyệt!"
        )
        return {
            "status": "BLOCKED",
            "reason": "UNAUTHORIZED_DEEP_CRAWL",
            "username": clean_user,
        }

    staging_repo.update_status_and_decision(
        platform="FACEBOOK",
        username=clean_user,
        status=ProcessingStatus.DEEP_CRAWLING,
    )

    try:
        result = _crawl_facebook_profile_sync(clean_user)

        if result:
            # 🛡️ POST-CRAWL VALIDATION GUARD
            post_val = validate_post_crawl(result, is_refresh=is_refresh)
            if not post_val.is_valid:
                logger.warning(
                    f"[WORKER] QUARANTINE FB {clean_user}: Không đạt Post-Validation Guard "
                    f"(Lý do: {post_val.quarantine_reason}, Chi tiết: {post_val.details})"
                )
                staging_repo.update_status_and_decision(
                    platform="FACEBOOK",
                    username=clean_user,
                    status=ProcessingStatus.FAILED,
                    decision=Decision.AUTO_REJECT,
                    reason_codes=[post_val.quarantine_reason or "QUARANTINE_POST_VALIDATION_FAILED"],
                )
                return {
                    "status": "QUARANTINE_REJECTED",
                    "reason": post_val.quarantine_reason,
                    "details": post_val.details,
                    "username": clean_user,
                }

            result.aiEnrichment = process_ai_enrichment(
                result.creator, result.recentPosts
            )

            with open("data/crawled_results.jsonl", "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
                    + "\n"
                )

            creator_type = (
                getattr(result.aiEnrichment, "creatorType", "UNKNOWN")
                if result.aiEnrichment
                else "UNKNOWN"
            )

            if creator_type not in ["KOL", "KOC"]:
                logger.warning(
                    f"[WORKER] SKIP INGEST FB {clean_user}: Bỏ qua vì không phải KOL/KOC."
                )
                staging_repo.update_status_and_decision(
                    platform="FACEBOOK",
                    username=clean_user,
                    status=ProcessingStatus.FAILED,
                    decision=Decision.AUTO_REJECT,
                )
                return result.model_dump(mode="json")

            java_payload = JavaMapper.from_ingest_payload(result)

            with open("data/java_payloads_debug.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(java_payload, ensure_ascii=False) + "\n")

            sent_ok = java_client.send_creator_data(java_payload)
            if sent_ok:
                staging_repo.update_status_and_decision(
                    platform="FACEBOOK",
                    username=clean_user,
                    status=ProcessingStatus.INGESTED,
                )

            return result.model_dump(mode="json")
        else:
            logger.warning(f"[WORKER] FAILED: Could not crawl Facebook {clean_user}. Retrying...")
            raise self.retry(countdown=10)
    except Exception as e:
        logger.error(f"[WORKER] ERROR processing Facebook {clean_user}: {e}")
        raise self.retry(exc=e, countdown=10)


@app.task(bind=True, max_retries=3, queue="deep_crawl_queue")
def process_instagram_profile(self, username: str, is_refresh: bool = False) -> Optional[dict]:
    clean_user = username.removeprefix("@").strip()
    logger.info(f"[WORKER] Starting Instagram crawl check for: @{clean_user}")

    # 🛡️ CELERY DEEP CRAWL GATE (Double-Check Guard)
    staging_record = staging_repo.get_candidate("INSTAGRAM", clean_user)
    is_authorized = (
        is_refresh
        or (
            staging_record is not None
            and staging_record.decision in [
                Decision.AUTO_ACCEPT_KOC,
                Decision.AUTO_ACCEPT_KOL,
                Decision.MANAGER_APPROVED,
            ]
        )
    )
    if not is_authorized:
        logger.error(
            f"[DEEP CRAWL GATE BLOCKED] Từ chối Deep Crawl IG @{clean_user}: "
            f"Chưa qua Preflight hoặc chưa được duyệt!"
        )
        return {
            "status": "BLOCKED",
            "reason": "UNAUTHORIZED_DEEP_CRAWL",
            "username": clean_user,
        }

    staging_repo.update_status_and_decision(
        platform="INSTAGRAM",
        username=clean_user,
        status=ProcessingStatus.DEEP_CRAWLING,
    )

    try:
        result = _crawl_instagram_profile_sync(clean_user)

        if result:
            # 🛡️ POST-CRAWL VALIDATION GUARD
            post_val = validate_post_crawl(result, is_refresh=is_refresh)
            if not post_val.is_valid:
                logger.warning(
                    f"[WORKER] QUARANTINE IG @{clean_user}: Không đạt Post-Validation Guard "
                    f"(Lý do: {post_val.quarantine_reason}, Chi tiết: {post_val.details})"
                )
                staging_repo.update_status_and_decision(
                    platform="INSTAGRAM",
                    username=clean_user,
                    status=ProcessingStatus.FAILED,
                    decision=Decision.AUTO_REJECT,
                    reason_codes=[post_val.quarantine_reason or "QUARANTINE_POST_VALIDATION_FAILED"],
                )
                return {
                    "status": "QUARANTINE_REJECTED",
                    "reason": post_val.quarantine_reason,
                    "details": post_val.details,
                    "username": clean_user,
                }

            result.aiEnrichment = process_ai_enrichment(
                result.creator, result.recentPosts
            )

            with open("data/crawled_results.jsonl", "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
                    + "\n"
                )

            creator_type = (
                getattr(result.aiEnrichment, "creatorType", "UNKNOWN")
                if result.aiEnrichment
                else "UNKNOWN"
            )

            if creator_type not in ["KOL", "KOC"]:
                logger.warning(
                    f"[WORKER] SKIP INGEST IG @{clean_user}: Bỏ qua vì không phải KOL/KOC."
                )
                staging_repo.update_status_and_decision(
                    platform="INSTAGRAM",
                    username=clean_user,
                    status=ProcessingStatus.FAILED,
                    decision=Decision.AUTO_REJECT,
                )
                return result.model_dump(mode="json")

            java_payload = JavaMapper.from_ingest_payload(result)

            with open("data/java_payloads_debug.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(java_payload, ensure_ascii=False) + "\n")

            sent_ok = java_client.send_creator_data(java_payload)
            if sent_ok:
                staging_repo.update_status_and_decision(
                    platform="INSTAGRAM",
                    username=clean_user,
                    status=ProcessingStatus.INGESTED,
                )

            return result.model_dump(mode="json")
        else:
            logger.warning(f"[WORKER] FAILED: Could not crawl Instagram @{clean_user}. Retrying...")
            raise self.retry(countdown=10)
    except Exception as e:
        logger.error(f"[WORKER] ERROR processing Instagram @{clean_user}: {e}")
        raise self.retry(exc=e, countdown=10)


@app.task(bind=True)
def run_retrain_pipeline(self) -> None:
    logger.info("[WORKER] Bắt đầu chạy Vòng lặp học tập (AI Retrain Pipeline)...")
    try:
        from app.scripts.auto_update_prototypes import update_prototypes

        update_prototypes()
        logger.info("[WORKER] Chạy thành công AI Retrain Pipeline.")
    except Exception as e:
        logger.error(f"[WORKER] Lỗi khi chạy AI Retrain Pipeline: {e}")
