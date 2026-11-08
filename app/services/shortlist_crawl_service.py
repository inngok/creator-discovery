import threading
from typing import Optional
from app.core.logger import setup_logger

logger = setup_logger("shortlist_crawler")


def crawl_and_sync_creator_sync(platform: str, username: str, creator_id: Optional[str] = None) -> dict:
    """
    Thực hiện cào dữ liệu cho Creator khi được thêm vào Shortlist:
    1. Ưu tiên dispatch qua Celery task (nếu Redis & Worker đang hoạt động).
    2. Nếu Celery không khả dụng (offline / redis down), tự động chạy cào trực tiếp qua Playwright,
       phân tích AI, phân loại KOC/KOL và đồng bộ ngược về Java Backend qua Ingest API.
    """
    clean_user = username.strip().removeprefix("@")
    plat = platform.upper()
    logger.info(f"[SHORTLIST CRAWLER] Nhận lệnh cào dữ liệu cho {plat}: @{clean_user} (CreatorID: {creator_id})")

    # 1. Thử gửi qua Celery queue nếu có
    try:
        from app.workers.tasks import (
            process_tiktok_profile,
            process_facebook_profile,
            process_instagram_profile,
        )

        if plat == "TIKTOK":
            task = process_tiktok_profile.delay(clean_user, is_refresh=True)
            logger.info(f"[SHORTLIST CRAWLER] Đã enqueue Celery Deep Crawl task {task.id} cho @{clean_user}")
            return {"mode": "CELERY", "status": "QUEUED", "task_id": task.id, "platform": plat, "username": clean_user}
        elif plat == "FACEBOOK":
            task = process_facebook_profile.delay(clean_user, is_refresh=True)
            logger.info(f"[SHORTLIST CRAWLER] Đã enqueue Celery Deep Crawl task {task.id} cho FB {clean_user}")
            return {"mode": "CELERY", "status": "QUEUED", "task_id": task.id, "platform": plat, "username": clean_user}
        elif plat == "INSTAGRAM":
            task = process_instagram_profile.delay(clean_user, is_refresh=True)
            logger.info(f"[SHORTLIST CRAWLER] Đã enqueue Celery Deep Crawl task {task.id} cho IG @{clean_user}")
            return {"mode": "CELERY", "status": "QUEUED", "task_id": task.id, "platform": plat, "username": clean_user}
    except Exception as celery_err:
        logger.warning(
            f"[SHORTLIST CRAWLER] Celery không khả dụng ({celery_err}). Chuyển sang chạy trực tiếp Direct Crawler."
        )

    # 2. Fallback: Cào trực tiếp đồng bộ
    try:
        from app.crawlers.tiktok_crawler import _crawl_tiktok_profile_sync
        from app.crawlers.facebook_crawler import _crawl_facebook_profile_sync
        from app.crawlers.instagram_crawler import _crawl_instagram_profile_sync
        from app.ai.analyzer import process_ai_enrichment
        from app.ai.creator_classifier import classify_creator_type_smart
        from app.mappers.java_mapper import JavaMapper
        from app.clients.java_client import java_client

        result = None
        if plat == "TIKTOK":
            result = _crawl_tiktok_profile_sync(clean_user)
        elif plat == "FACEBOOK":
            result = _crawl_facebook_profile_sync(clean_user)
        elif plat == "INSTAGRAM":
            result = _crawl_instagram_profile_sync(clean_user)
        else:
            logger.error(f"[SHORTLIST CRAWLER] Nền tảng chưa được hỗ trợ: {plat}")
            return {"mode": "DIRECT", "status": "FAILED", "error": f"Unsupported platform {plat}"}

        if not result or not result.creator:
            logger.warning(f"[SHORTLIST CRAWLER] Không trích xuất được dữ liệu cho @{clean_user} trên {plat}")
            return {"mode": "DIRECT", "status": "FAILED", "error": "No data extracted"}

        # AI Enrichment
        result.aiEnrichment = process_ai_enrichment(result.creator, result.recentPosts)

        # Smart KOC/KOL classification
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

        # Ánh xạ sang Java Payload và gửi sang Backend
        java_payload = JavaMapper.from_ingest_payload(result)
        sent_ok = java_client.send_creator_data(java_payload)

        if sent_ok:
            logger.info(
                f"[SHORTLIST CRAWLER] Đã cào và đồng bộ thành công dữ liệu ({len(result.recentPosts)} posts, {smart_type}) cho @{clean_user} vào Backend!"
            )
            return {
                "mode": "DIRECT",
                "status": "SUCCESS",
                "platform": plat,
                "username": clean_user,
                "posts_count": len(result.recentPosts),
                "creator_type": smart_type,
            }
        else:
            logger.warning(f"[SHORTLIST CRAWLER] Cào thành công nhưng lỗi gửi dữ liệu sang Java Backend cho @{clean_user}")
            return {"mode": "DIRECT", "status": "PARTIAL_SUCCESS", "message": "Crawled but failed to ingest to Java"}

    except Exception as e:
        logger.error(f"[SHORTLIST CRAWLER] Lỗi trong quá trình cào trực tiếp @{clean_user}: {e}", exc_info=True)
        return {"mode": "DIRECT", "status": "ERROR", "error": str(e)}


def trigger_shortlist_crawl_background(platform: str, username: str, creator_id: Optional[str] = None):
    """Kích hoạt cào ngầm trong thread riêng biệt, không block HTTP request."""
    t = threading.Thread(
        target=crawl_and_sync_creator_sync,
        args=(platform, username, creator_id),
        daemon=True,
    )
    t.start()
    return t
