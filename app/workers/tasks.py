import json
import os
from datetime import datetime
from typing import Any, Callable, Optional

from app.ai.analyzer import process_ai_enrichment
from app.ai.creator_classifier import classify_creator_type_smart
from app.ai.rules.post_validator import validate_post_crawl
from app.ai.rules.rule_config import Decision, ProcessingStatus
from app.clients.java_client import java_client
from app.core.candidate_staging import CandidateStagingRecord, staging_repo
from app.core.logger import setup_logger
from app.crawlers.facebook_crawler import _crawl_facebook_profile_sync
from app.crawlers.instagram_crawler import _crawl_instagram_profile_sync
from app.crawlers.tiktok_crawler import _crawl_tiktok_profile_sync
from app.mappers.java_mapper import JavaMapper
from app.services.preflight_service import preflight_service

from .celery_app import app

logger = setup_logger("celery_tasks")

AUTHORIZED_DEEP_CRAWL_DECISIONS = {
    Decision.AUTO_ACCEPT_KOC,
    Decision.AUTO_ACCEPT_KOL,
    Decision.MANAGER_APPROVED,
}
VALID_CREATOR_TYPES = {"KOL", "KOC"}


def _get_deep_crawl_task(platform: str):
    """Bảng ánh xạ (Dispatch Table) chọn Deep Crawl Task theo nền tảng (0 if-else)."""
    return {
        "TIKTOK": process_tiktok_profile,
        "FACEBOOK": process_facebook_profile,
        "INSTAGRAM": process_instagram_profile,
    }.get((platform or "").upper())


def _get_pg_connection():
    """Khởi tạo kết nối PostgreSQL từ biến môi trường."""
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "221.121.3.226"),
        user=os.environ.get("DB_USER", "influencermatch_user"),
        password=os.environ.get("DB_PASSWORD", "12345"),
        dbname=os.environ.get("DB_NAME", "influencermatch_dev"),
        connect_timeout=5,
    )


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
    plat_upper = (platform or "TIKTOK").upper()
    logger.info(f"[CELERY PREFLIGHT] Đang xử lý {plat_upper}: @{clean_user}")
    try:
        decision_result = preflight_service.run_preflight(
            platform=plat_upper, username=clean_user, is_refresh=is_refresh
        )
        decision = decision_result.decision

        if decision == Decision.RETRY_PREFLIGHT:
            raise self.retry(countdown=180)

        if decision in AUTHORIZED_DEEP_CRAWL_DECISIONS:
            staging_repo.update_status_and_decision(
                platform=plat_upper,
                username=clean_user,
                status=ProcessingStatus.DEEP_CRAWL_QUEUED,
                decision=decision,
            )
            task_fn = _get_deep_crawl_task(plat_upper)
            if task_fn:
                task_args = [clean_user, True] if is_refresh else [clean_user]
                task_fn.apply_async(args=task_args, queue="deep_crawl_queue")

            logger.info(
                f"[CELERY PREFLIGHT GATE] Đã kích hoạt Deep Crawl cho @{clean_user} (Decision: {decision.value})"
            )
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
        raise self.retry(exc=e, countdown=180)


# ==============================================================================
# PHA 2: CELERY DEEP CRAWL TASKS (Cào sâu đầy đủ - Chỉ chạy khi đã qua GATE)
# ==============================================================================
def _execute_deep_crawl(
    task_self,
    platform: str,
    username: str,
    crawl_fn: Callable[[str], Optional[object]],
    is_refresh: bool = False,
    retry_countdown: int = 180,
) -> Optional[dict]:
    """Luồng xử lý Deep Crawl hợp nhất cho mọi nền tảng với Guard Clauses phẳng (hạn chế if-else lồng nhau)."""
    plat = platform.upper()
    clean_user = username.removeprefix("@").strip()
    logger.info(f"[WORKER] Starting {plat} crawl check for: @{clean_user}")

    # 🛡️ GUARD 1: CELERY DEEP CRAWL GATE (Double-Check Guard)
    staging_record = staging_repo.get_candidate(plat, clean_user)
    is_authorized = is_refresh or (
        staging_record is not None
        and staging_record.decision in AUTHORIZED_DEEP_CRAWL_DECISIONS
    )
    if not is_authorized:
        logger.error(
            f"[DEEP CRAWL GATE BLOCKED] Từ chối Deep Crawl {plat} @{clean_user}: "
            f"Chưa qua Preflight hoặc chưa được duyệt! (Staging Decision: {getattr(staging_record, 'decision', 'NONE')})"
        )
        return {
            "status": "BLOCKED",
            "reason": "UNAUTHORIZED_DEEP_CRAWL",
            "username": clean_user,
        }

    # Đảm bảo hồ sơ từ PostgreSQL trong luồng Refresh luôn có bản ghi theo dõi tại Staging
    if staging_record is None and is_refresh:
        staging_repo.upsert_candidate(
            CandidateStagingRecord(
                platform=plat,
                username=clean_user,
                processing_status=ProcessingStatus.DEEP_CRAWLING,
                decision=Decision.AUTO_ACCEPT_KOC,
                primary_type="KOC",
                reason_codes=["REFRESH_EXISTING_CREATOR"],
            )
        )
    else:
        staging_repo.update_status_and_decision(
            platform=plat,
            username=clean_user,
            status=ProcessingStatus.DEEP_CRAWLING,
        )

    try:
        result = crawl_fn(clean_user)
        if not result:
            logger.warning(f"[WORKER] FAILED: Could not crawl {plat} @{clean_user}. Retrying...")
            staging_repo.update_status_and_decision(
                platform=plat,
                username=clean_user,
                status=ProcessingStatus.FAILED,
                reason_codes=["DEEP_CRAWL_EMPTY_RESULT"],
            )
            raise task_self.retry(countdown=retry_countdown)

        # 🛡️ GUARD 2: POST-CRAWL VALIDATION GUARD
        if plat in {"FACEBOOK", "INSTAGRAM"}:
            from app.core.vietnam_evidence import vietnam_market_evidence
            market_check = vietnam_market_evidence(
                {"bio": result.creator.bio},
                [p.model_dump() for p in result.recentPosts])
            if not market_check["matches"]:
                staging_repo.update_status_and_decision(
                    platform=plat, username=clean_user, status=ProcessingStatus.FAILED,
                    decision=Decision.SKIP_INSUFFICIENT, reason_codes=market_check["reasons"])
                return {"status": "INCOMPLETE", "reason": "VIETNAM_MARKET_NOT_VERIFIED",
                        "username": clean_user, "market_check": market_check}

        post_val = validate_post_crawl(result, is_refresh=is_refresh)
        if not post_val.is_valid:
            incomplete = post_val.quarantine_reason in {
                "INSUFFICIENT_POST_DATA", "INVALID_EXTERNAL_ID", "MISSING_CREATOR_PAYLOAD"
            }
            logger.warning(
                f"[WORKER] QUARANTINE {plat} @{clean_user}: Không đạt Post-Validation Guard "
                f"(Lý do: {post_val.quarantine_reason}, Chi tiết: {post_val.details})"
            )
            staging_repo.update_status_and_decision(
                platform=plat,
                username=clean_user,
                status=ProcessingStatus.FAILED,
                decision=Decision.SKIP_INSUFFICIENT if incomplete else Decision.AUTO_REJECT,
                reason_codes=[post_val.quarantine_reason or "QUARANTINE_POST_VALIDATION_FAILED"],
            )
            return {
                "status": "INCOMPLETE" if incomplete else "QUARANTINE_REJECTED",
                "reason": post_val.quarantine_reason,
                "details": post_val.details,
                "username": clean_user,
            }

        # Chạy AI Enrichment & Phân loại Creator Type
        result.aiEnrichment = process_ai_enrichment(result.creator, result.recentPosts)

        with open("data/crawled_results.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(result.model_dump(mode="json"), ensure_ascii=False) + "\n")

        smart_type = classify_creator_type_smart(
            username=result.creator.username,
            display_name=result.creator.displayName,
            bio=result.creator.bio or "",
            followers=result.creator.followers,
            recent_posts=result.recentPosts,
            category=getattr(result.aiEnrichment, "category", "") or "",
            niche=getattr(result.aiEnrichment, "niche", "") or "",
            classified_type=getattr(result.aiEnrichment, "creatorType", "") or "",
        )
        if result.aiEnrichment:
            result.aiEnrichment.creatorType = smart_type

        # 🛡️ GUARD 3: Chỉ gửi KOL/KOC sang Java Backend
        if smart_type not in VALID_CREATOR_TYPES:
            logger.warning(
                f"[WORKER] SKIP INGEST {plat} @{clean_user}: Bỏ qua vì không phải KOL/KOC (Phân loại: {smart_type})."
            )
            staging_repo.update_status_and_decision(
                platform=plat,
                username=clean_user,
                status=ProcessingStatus.FAILED,
                decision=Decision.AUTO_REJECT,
                primary_type=smart_type,
                reason_codes=["POST_VALIDATION_NOT_KOC_KOL"],
            )
            return result.model_dump(mode="json")

        java_payload = JavaMapper.from_ingest_payload(result)
        with open("data/java_payloads_debug.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(java_payload, ensure_ascii=False) + "\n")

        sent_ok = java_client.send_creator_data(java_payload)
        staging_repo.update_status_and_decision(
            platform=plat,
            username=clean_user,
            status=ProcessingStatus.INGESTED if sent_ok else ProcessingStatus.FAILED,
            primary_type=smart_type,
        )
        return result.model_dump(mode="json")

    except Exception as e:
        logger.error(f"[WORKER] ERROR processing {plat} @{clean_user}: {e}")
        staging_repo.update_status_and_decision(
            platform=plat,
            username=clean_user,
            status=ProcessingStatus.FAILED,
        )
        raise task_self.retry(exc=e, countdown=retry_countdown)


@app.task(bind=True, max_retries=3, queue="deep_crawl_queue")
def process_tiktok_profile(self, username: str, is_refresh: bool = False) -> Optional[dict]:
    return _execute_deep_crawl(self, "TIKTOK", username, _crawl_tiktok_profile_sync, is_refresh, 180)


@app.task(bind=True, max_retries=3, queue="deep_crawl_queue")
def process_facebook_profile(self, username: str, is_refresh: bool = False) -> Optional[dict]:
    return _execute_deep_crawl(self, "FACEBOOK", username, _crawl_facebook_profile_sync, is_refresh, 10)


@app.task(bind=True, max_retries=3, queue="deep_crawl_queue")
def process_instagram_profile(self, username: str, is_refresh: bool = False) -> Optional[dict]:
    return _execute_deep_crawl(self, "INSTAGRAM", username, _crawl_instagram_profile_sync, is_refresh, 10)


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
# PHA 3: TỰ ĐỘNG CÀO KOC MỚI ĐỊNH KỲ (DISCOVERY OF NEW KOCS)
# ==============================================================================
DISCOVERY_KEYWORDS_VI = [
    "review mỹ phẩm",
    "review đồ ăn",
    "review công nghệ",
    "review thời trang",
    "review gia dụng",
    "review du lịch",
]

_PLATFORM_EXPAND_MAP = {
    "ALL": ("TIKTOK", "FACEBOOK", "INSTAGRAM"),
    "BOTH": ("TIKTOK", "FACEBOOK", "INSTAGRAM"),
    "TIKTOK": ("TIKTOK",),
    "FACEBOOK": ("FACEBOOK",),
    "INSTAGRAM": ("INSTAGRAM",),
}


def _load_target_keywords(custom_keywords: Optional[list[str]]) -> list[str]:
    cleaned = [k.strip() for k in (custom_keywords or []) if k and k.strip()]
    if cleaned:
        return cleaned
    try:
        conn = _get_pg_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT keyword FROM discovery_keyword WHERE is_active = true AND keyword_type = 'TARGET'"
        )
        rows = cur.fetchall()
        conn.close()
        db_keywords = [r[0].strip() for r in rows if r[0] and r[0].strip()]
        if db_keywords:
            logger.info(
                f"[WEEKLY DISCOVERY] Đã nạp {len(db_keywords)} từ khóa TARGET từ PostgreSQL: {db_keywords}"
            )
            return db_keywords
    except Exception as db_err:
        logger.debug(f"[WEEKLY DISCOVERY] Không lấy được từ khóa từ DB ({db_err}), sử dụng mặc định.")

    logger.info(f"[WEEKLY DISCOVERY] Sử dụng bộ từ khóa mặc định: {DISCOVERY_KEYWORDS_VI}")
    return DISCOVERY_KEYWORDS_VI


@app.task(bind=True, queue="default")
def weekly_discover_new_creators_task(
    self,
    max_new_kocs: int = 30,
    keywords: Optional[list[str]] = None,
    platform: str = "ALL",
) -> dict:
    """
    Task chạy định kỳ trên Celery Beat hoặc do Manager kích hoạt:
    - Quét tìm kiếm ứng viên trên các chuyên mục ngành nghề phổ biến hoặc từ khóa tùy chỉnh.
    - Hỗ trợ đa nền tảng: TIKTOK, FACEBOOK hoặc ALL (cô lập lỗi từng nền tảng để không mất dữ liệu).
    - Tự động lấy từ khóa TARGET đang active trong DB nếu không truyền trực tiếp.
    - LOẠI BỎ 100% KOC CŨ ĐÃ CÓ TRONG HỆ THỐNG (Chỉ chọn KOC mới toanh).
    """
    target_platform = (platform or "ALL").strip().upper()
    logger.info(
        f"[WEEKLY DISCOVERY] Bắt đầu luồng cào tự động tìm KOC MỚI ({target_platform}) (Tối đa {max_new_kocs} KOC)..."
    )
    try:
        from app.crawlers.discovery_crawler import (
            search_new_facebook_candidates,
            search_new_instagram_candidates,
            search_new_tiktok_candidates,
        )

        search_keywords = _load_target_keywords(keywords)
        discovery_runners = {
            "TIKTOK": search_new_tiktok_candidates,
            "FACEBOOK": search_new_facebook_candidates,
            "INSTAGRAM": search_new_instagram_candidates,
        }
        selected_platforms = _PLATFORM_EXPAND_MAP.get(target_platform, ())

        candidates: list[dict] = []
        platform_errors: dict[str, str] = {}

        for plat_name in selected_platforms:
            runner_fn = discovery_runners.get(plat_name)
            if not runner_fn:
                continue
            try:
                found = runner_fn(
                    keywords=search_keywords,
                    max_per_keyword=8,
                    max_total=max_new_kocs * 2,
                )
                candidates.extend(found)
            except Exception as plat_err:
                platform_errors[plat_name] = str(plat_err)
                logger.warning(
                    f"[WEEKLY DISCOVERY] Nền tảng {plat_name} gặp lỗi khi tìm kiếm (vẫn tiếp tục nền tảng khác): {plat_err}"
                )

        new_enqueued = 0
        skipped_old = 0

        for cand in candidates:
            if new_enqueued >= max_new_kocs:
                break

            cand_platform = cand.get("platform", "TIKTOK").upper()
            clean_user = cand.get("username", "").strip().removeprefix("@").strip().lower()
            if not clean_user:
                continue

            if staging_repo.get_candidate(cand_platform, clean_user) is not None:
                skipped_old += 1
                logger.debug(f"[WEEKLY DISCOVERY] Bỏ qua KOC CŨ: {cand_platform} @{clean_user} (đã có trong DB)")
                continue

            preflight_candidate_task.delay(cand_platform, clean_user)
            new_enqueued += 1
            logger.info(
                f"[WEEKLY DISCOVERY ENQUEUE] Đã phát hiện và đưa KOC MỚI ({cand_platform}) @{clean_user} "
                f"({cand.get('followers', 0)} fl) vào Preflight Queue ({new_enqueued}/{max_new_kocs})"
            )

        summary = {
            "status": "COMPLETED",
            "total_candidates_scanned": len(candidates),
            "new_kocs_enqueued": new_enqueued,
            "old_kocs_skipped": skipped_old,
            "platform_errors": platform_errors,
        }
        logger.info(f"[WEEKLY DISCOVERY] Hoàn tất luồng tự động: {summary}")
        return summary
    except Exception as e:
        logger.error(f"[WEEKLY DISCOVERY] Lỗi khi cào KOC mới tự động: {e}")
        return {"status": "FAILED", "error": str(e)}


# ==============================================================================
# PHA 4: TỰ ĐỘNG CÀO LẠI (REFRESH) CÁC KOC CŨ ĐÃ CÓ TRONG DATABASE
# ==============================================================================
def _fetch_stale_targets_from_pg(limit: int) -> list[dict]:
    """Lấy danh sách Creator cũ nhất từ PostgreSQL, loại bỏ các tài khoản vừa lỗi gần đây để chống lặp vô tận."""
    targets: list[dict] = []
    try:
        conn = _get_pg_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT c.platform, c.user_name
            FROM creator c
            LEFT JOIN LATERAL (
                SELECT observed_at, collected_at FROM public_creator_metric
                WHERE creator_id=c.id ORDER BY collected_at DESC, id DESC LIMIT 1
            ) m ON true
            WHERE UPPER(c.platform) IN ('TIKTOK', 'FACEBOOK', 'INSTAGRAM')
            ORDER BY COALESCE(m.observed_at, m.collected_at) ASC NULLS FIRST, c.id
            LIMIT %s;
            """,
            (max(limit * 4, limit),),
        )
        rows = cur.fetchall()
        conn.close()

        for plat_raw, uname_raw in rows:
            if not uname_raw:
                continue
            plat = str(plat_raw or "TIKTOK").upper().strip()
            uname = str(uname_raw).strip().removeprefix("@").strip()
            if uname and staging_repo.is_eligible_for_refresh(plat, uname):
                targets.append({"platform": plat, "username": uname})
                if len(targets) >= limit:
                    break
        logger.info(f"[AUTO REFRESH DB] Đã lấy {len(targets)} KOC cũ hợp lệ từ PostgreSQL Deploy.")
    except Exception as pg_err:
        logger.debug(f"[AUTO REFRESH DB] Không kết nối được PostgreSQL ({pg_err}), chuyển sang Staging SQLite.")
    return targets


@app.task(bind=True, queue="default")
def auto_refresh_creators_in_db_task(self, limit: int = 25) -> dict:
    """
    Task chạy định kỳ tự động:
    - Lấy danh sách các Creator cũ ĐÃ CÓ TRONG DATABASE có ngày cập nhật cũ nhất.
    - Loại trừ các tài khoản hỏng/private vừa thử cào thất bại gần đây (cooldown 72h) để xoay vòng mượt.
    - Đẩy thẳng vào Deep Crawl Queue với cờ is_refresh=True (không tốn bước Preflight thừa).
    """
    logger.info(f"[AUTO REFRESH DB] Bắt đầu quét cào lại các KOC cũ trong DB (Giới hạn: {limit} KOC)...")
    try:
        targets = _fetch_stale_targets_from_pg(limit)

        if not targets:
            stale_records = staging_repo.get_stale_ingested_candidates(limit=limit)
            targets = [
                {"platform": rec.platform.upper(), "username": rec.username.strip()}
                for rec in stale_records
                if staging_repo.is_eligible_for_refresh(rec.platform.upper(), rec.username.strip())
            ]
            logger.info(f"[AUTO REFRESH DB] Đã lấy {len(targets)} KOC cũ từ Staging SQLite.")

        if not targets:
            logger.info("[AUTO REFRESH DB] Không tìm thấy KOC cũ nào cần làm mới.")
            return {"status": "SUCCESS", "message": "Không có KOC nào cần cào lại.", "enqueued_count": 0}

        enqueued_count = 0
        for item in targets:
            plat = item["platform"]
            uname = item["username"].removeprefix("@").strip()
            task_fn = _get_deep_crawl_task(plat)
            if not uname or not task_fn:
                continue

            # Đẩy trực tiếp vào Deep Crawl với is_refresh=True để tiết kiệm 50% thời gian queue
            task_fn.apply_async(args=[uname, True], queue="deep_crawl_queue")
            enqueued_count += 1
            logger.info(
                f"[AUTO REFRESH DB ENQUEUE] Đã xếp hàng Deep Crawl làm mới ({enqueued_count}/{len(targets)}): {plat} @{uname}"
            )

        return {
            "status": "COMPLETED",
            "enqueued_count": enqueued_count,
            "total_stale_found": len(targets),
        }
    except Exception as e:
        logger.error(f"[AUTO REFRESH DB] Lỗi khi cào lại KOC cũ trong DB: {e}")
        return {"status": "FAILED", "error": str(e)}




@app.task(name="app.workers.tasks.batch_crawl_job", track_started=True, bind=True)
def batch_crawl_job(self, payload: dict) -> dict:
    import asyncio
    from threading import Lock
    from fastapi import HTTPException
    from app.api.routers.crawl_router import BatchCrawlRequest, batch_crawl_kocs
    from app.core.crawl_progress import (
        reset_crawl_progress_callback,
        set_crawl_progress_callback,
    )

    # Celery's request is thread-local; discovery reports from asyncio.to_thread.
    task_id = self.request.id
    progress_lock = Lock()
    progress: dict[str, Any] = {
        "logs": [],
        "current_keyword": "",
        "platform": "",
        "stage": "",
    }

    def publish_progress(event):
        with progress_lock:
            message = event["message"]
            timestamp = datetime.now().astimezone().strftime("[%H:%M:%S]")
            progress["logs"] = [f"{timestamp} {message}", *progress["logs"][:39]]
            progress["current_keyword"] = event.get("keyword") or progress["current_keyword"]
            progress["platform"] = event.get("platform") or progress["platform"]
            progress["stage"] = event.get("stage") or progress["stage"]
            self.update_state(task_id=task_id, state="PROGRESS", meta={"progress": dict(progress)})

    token = set_crawl_progress_callback(publish_progress)
    try:
        publish_progress({"message": "Worker nhận tác vụ, bắt đầu thu thập.", "stage": "starting"})
        return asyncio.run(batch_crawl_kocs(BatchCrawlRequest(**payload)))
    except HTTPException as exc:
        publish_progress({"message": f"Tác vụ thất bại: {exc.detail}", "stage": "failed"})
        return {"status": "error", "error": str(exc.detail), "progress": dict(progress)}
    except Exception as exc:
        logger.exception("Batch crawl job failed (task_id=%s)", task_id)
        error = f"Thu thập thất bại ({type(exc).__name__}); mã tác vụ: {task_id}"
        publish_progress({"message": error, "stage": "failed"})
        return {"status": "error", "error": error, "progress": dict(progress)}
    finally:
        reset_crawl_progress_callback(token)
