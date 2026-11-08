import json
from typing import Optional

from app.ai.analyzer import process_ai_enrichment
from app.ai.creator_classifier import classify_creator_type_smart
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

            smart_type = classify_creator_type_smart(
                username=result.creator.username,
                display_name=result.creator.displayName,
                bio=result.creator.bio or "",
                followers=result.creator.followers,
                recent_posts=result.recentPosts,
                category=getattr(result.aiEnrichment, "category", "") or "",
                niche=getattr(result.aiEnrichment, "niche", "") or "",
            )
            if result.aiEnrichment:
                result.aiEnrichment.creatorType = smart_type

            creator_type = smart_type

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

            smart_type = classify_creator_type_smart(
                username=result.creator.username,
                display_name=result.creator.displayName,
                bio=result.creator.bio or "",
                followers=result.creator.followers,
                recent_posts=result.recentPosts,
                category=getattr(result.aiEnrichment, "category", "") or "",
                niche=getattr(result.aiEnrichment, "niche", "") or "",
            )
            if result.aiEnrichment:
                result.aiEnrichment.creatorType = smart_type

            creator_type = smart_type

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

            smart_type = classify_creator_type_smart(
                username=result.creator.username,
                display_name=result.creator.displayName,
                bio=result.creator.bio or "",
                followers=result.creator.followers,
                recent_posts=result.recentPosts,
                category=getattr(result.aiEnrichment, "category", "") or "",
                niche=getattr(result.aiEnrichment, "niche", "") or "",
            )
            if result.aiEnrichment:
                result.aiEnrichment.creatorType = smart_type

            creator_type = smart_type

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


# ==============================================================================
# PHA 3: TỰ ĐỘNG CÀO KOC MỚI ĐỊNH KỲ HÀNG TUẦN (WEEKLY DISCOVERY OF NEW KOCS)
# ==============================================================================
DISCOVERY_KEYWORDS_VI = [
    "review mỹ phẩm",
    "review đồ ăn",
    "review công nghệ",
    "review thời trang",
    "review gia dụng",
    "review du lịch",
]


@app.task(bind=True, queue="default")
def weekly_discover_new_creators_task(
    self,
    max_new_kocs: int = 30,
    keywords: Optional[list[str]] = None,
) -> dict:
    """
    Task chạy định kỳ hàng tuần trên Celery Beat hoặc do Manager kích hoạt:
    - Quét tìm kiếm ứng viên trên các chuyên mục ngành nghề phổ biến hoặc từ khóa tùy chỉnh.
    - Tự động lấy từ khóa TARGET đang active trong DB nếu không truyền trực tiếp.
    - LOẠI BỎ 100% KOC CŨ ĐÃ CÓ TRONG HỆ THỐNG (Chỉ chọn KOC mới toanh).
    - Đưa KOC mới vào hàng đợi Preflight để lọc tự động (Rule Engine BR-01 -> BR-06)
      và Deep Crawl nếu đạt chuẩn.
    """
    logger.info(f"[WEEKLY DISCOVERY] Bắt đầu luồng cào tự động tìm KOC MỚI (Tối đa {max_new_kocs} KOC)...")
    try:
        from app.crawlers.discovery_crawler import search_new_tiktok_candidates

        search_keywords = [k.strip() for k in keywords if k and k.strip()] if keywords else []
        if not search_keywords:
            try:
                import psycopg2

                db_host = os.environ.get("DB_HOST", "221.121.3.226")
                db_user = os.environ.get("DB_USER", "influencermatch_user")
                db_pass = os.environ.get("DB_PASSWORD", "12345")
                db_name = os.environ.get("DB_NAME", "influencermatch_dev")

                conn = psycopg2.connect(
                    host=db_host,
                    user=db_user,
                    password=db_pass,
                    dbname=db_name,
                    connect_timeout=5,
                )
                cur = conn.cursor()
                cur.execute(
                    "SELECT keyword FROM discovery_keyword WHERE is_active = true AND keyword_type = 'TARGET'"
                )
                rows = cur.fetchall()
                conn.close()
                db_keywords = [r[0].strip() for r in rows if r[0] and r[0].strip()]
                if db_keywords:
                    search_keywords = db_keywords
                    logger.info(
                        f"[WEEKLY DISCOVERY] Đã nạp {len(search_keywords)} từ khóa TARGET từ PostgreSQL: {search_keywords}"
                    )
            except Exception as db_err:
                logger.debug(f"[WEEKLY DISCOVERY] Không lấy được từ khóa từ DB ({db_err}), sử dụng mặc định.")

        if not search_keywords:
            search_keywords = DISCOVERY_KEYWORDS_VI
            logger.info(f"[WEEKLY DISCOVERY] Sử dụng bộ từ khóa mặc định: {search_keywords}")

        candidates = search_new_tiktok_candidates(
            keywords=search_keywords,
            max_per_keyword=8,
            max_total=max_new_kocs * 2,  # Quét rộng để trừ hao các KOC cũ
        )

        new_enqueued = 0
        skipped_old = 0

        for cand in candidates:
            if new_enqueued >= max_new_kocs:
                break

            platform = cand.get("platform", "TIKTOK")
            username = cand.get("username", "").strip()
            clean_user = username.removeprefix("@").strip().lower()

            if not clean_user:
                continue

            # 🚨 KIỂM TRA CHỐNG CÀO KOC CŨ:
            # Nếu username đã có trong staging database -> Bỏ qua tuyệt đối!
            existing = staging_repo.get_candidate(platform, clean_user)
            if existing is not None:
                skipped_old += 1
                logger.debug(f"[WEEKLY DISCOVERY] Bỏ qua KOC CŨ: {platform} @{clean_user} (đã có trong DB)")
                continue

            # Đây là KOC HOÀN TOÀN MỚI -> Đưa vào hàng đợi Preflight Task
            preflight_candidate_task.delay(platform, clean_user)
            new_enqueued += 1
            logger.info(
                f"[WEEKLY DISCOVERY ENQUEUE] Đã phát hiện và đưa KOC MỚI @{clean_user} "
                f"({cand.get('followers', 0)} fl) vào Preflight Queue ({new_enqueued}/{max_new_kocs})"
            )

        summary = {
            "status": "COMPLETED",
            "total_candidates_scanned": len(candidates),
            "new_kocs_enqueued": new_enqueued,
            "old_kocs_skipped": skipped_old,
        }
        logger.info(f"[WEEKLY DISCOVERY] Hoàn tất luồng tự động hàng tuần: {summary}")
        return summary
    except Exception as e:
        logger.error(f"[WEEKLY DISCOVERY] Lỗi khi cào KOC mới tự động hàng tuần: {e}")
        return {"status": "FAILED", "error": str(e)}


# ==============================================================================
# PHA 4: TỰ ĐỘNG CÀO LẠI (REFRESH) CÁC KOC CŨ ĐÃ CÓ TRONG DATABASE
# ==============================================================================
@app.task(bind=True, queue="default")
def auto_refresh_creators_in_db_task(self, limit: int = 25) -> dict:
    """
    Task chạy định kỳ tự động:
    - Lấy danh sách các Creator cũ ĐÃ CÓ TRONG DATABASE có ngày cập nhật cũ nhất.
    - Kích hoạt luồng cào làm mới (is_refresh=True) để lấy số follower, view, like mới
      và tính lại dataConfidence (hồi phục độ tươi freshness về 1.00).
    - Cập nhật trực tiếp (Upsert) vào hồ sơ KOC hiện có, KHÔNG tạo bản ghi mới.
    """
    logger.info(f"[AUTO REFRESH DB] Bắt đầu quét cào lại các KOC cũ trong DB (Giới hạn: {limit} KOC)...")
    try:
        targets: list[dict] = []

        # 1. Thử lấy từ PostgreSQL Database nếu có cấu hình kết nối
        try:
            import psycopg2

            db_host = os.environ.get("DB_HOST", "221.121.3.226")
            db_user = os.environ.get("DB_USER", "influencermatch_user")
            db_pass = os.environ.get("DB_PASSWORD", "12345")
            db_name = os.environ.get("DB_NAME", "influencermatch_dev")

            conn = psycopg2.connect(
                host=db_host,
                user=db_user,
                password=db_pass,
                dbname=db_name,
                connect_timeout=5,
            )
            cur = conn.cursor()
            cur.execute("""
                SELECT c.platform, c.user_name
                FROM creator c
                LEFT JOIN public_creator_metric m ON c.id = m.creator_id
                ORDER BY m.observed_at ASC NULLS FIRST
                LIMIT %s;
            """, (limit,))
            rows = cur.fetchall()
            for r in rows:
                if r[1]:
                    targets.append({"platform": str(r[0]).upper(), "username": str(r[1]).strip()})
            conn.close()
            logger.info(f"[AUTO REFRESH DB] Đã lấy {len(targets)} KOC cũ từ PostgreSQL Deploy.")
        except Exception as pg_err:
            logger.debug(f"[AUTO REFRESH DB] Không kết nối được PostgreSQL ({pg_err}), chuyển sang Staging SQLite.")

        # 2. Nếu không lấy được từ PostgreSQL, lấy từ Staging SQLite
        if not targets:
            stale_records = staging_repo.get_stale_ingested_candidates(limit=limit)
            for rec in stale_records:
                targets.append({"platform": rec.platform.upper(), "username": rec.username.strip()})
            logger.info(f"[AUTO REFRESH DB] Đã lấy {len(targets)} KOC cũ từ Staging SQLite.")

        if not targets:
            logger.info("[AUTO REFRESH DB] Không tìm thấy KOC cũ nào cần làm mới.")
            return {"status": "SUCCESS", "message": "Không có KOC nào cần cào lại.", "enqueued_count": 0}

        enqueued_count = 0
        for item in targets:
            plat = item["platform"]
            uname = item["username"].removeprefix("@").strip()
            if not uname:
                continue

            # Kích hoạt cào làm mới với cờ is_refresh=True
            preflight_candidate_task.delay(plat, uname, is_refresh=True)
            enqueued_count += 1
            logger.info(f"[AUTO REFRESH DB ENQUEUE] Đã xếp hàng cào lại ({enqueued_count}/{len(targets)}): {plat} @{uname}")

        return {
            "status": "COMPLETED",
            "enqueued_count": enqueued_count,
            "total_stale_found": len(targets),
        }
    except Exception as e:
        logger.error(f"[AUTO REFRESH DB] Lỗi khi cào lại KOC cũ trong DB: {e}")
        return {"status": "FAILED", "error": str(e)}


