import json
from typing import Optional

from app.ai.analyzer import process_ai_enrichment
from app.clients.java_client import java_client
from app.core.logger import setup_logger
from app.crawlers.facebook_crawler import _crawl_facebook_profile_sync
from app.crawlers.instagram_crawler import _crawl_instagram_profile_sync
from app.crawlers.tiktok_crawler import _crawl_tiktok_profile_sync
from app.mappers.java_mapper import JavaMapper

from .celery_app import app

logger = setup_logger("celery_tasks")


@app.task(bind=True, max_retries=3)
def process_tiktok_profile(self, username: str) -> Optional[dict]:
    logger.info(f"[WORKER] Starting crawl for: {username}")
    try:
        # Call the synchronous crawling function directly
        result = _crawl_tiktok_profile_sync(username)

        if result:
            # Chạy AI ở background worker thay vì Uvicorn
            result.aiEnrichment = process_ai_enrichment(
                result.creator, result.recentPosts
            )

            with open("data/crawled_results.jsonl", "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
                    + "\n"
                )

            safe_category = (
                str(result.aiEnrichment.tags if result.aiEnrichment else [])
                .encode("ascii", "ignore")
                .decode()
            )
            logger.info(
                f"[WORKER] SUCCESS: {username} - {result.creator.followers} Followers. Type: {getattr(result.aiEnrichment, 'creatorType', 'UNKNOWN')}, Niche: {safe_category}"
            )

            java_payload = JavaMapper.from_ingest_payload(result)

            # Lưu tạm payload sẽ gửi cho Java Backend ra file để User dễ kiểm tra
            with open("data/java_payloads_debug.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(java_payload, ensure_ascii=False) + "\n")

            java_client.send_creator_data(java_payload)

            return result.model_dump(mode="json")
        else:
            logger.warning(f"[WORKER] FAILED: Could not crawl {username}. Retrying...")
            raise self.retry(countdown=10)
    except Exception as e:
        logger.error(f"[WORKER] ERROR processing {username}: {e}")
        raise self.retry(exc=e, countdown=10)


@app.task(bind=True, max_retries=3)
def process_facebook_profile(self, username: str) -> Optional[dict]:
    logger.info(f"[WORKER] Starting Facebook crawl for: {username}")
    try:
        result = _crawl_facebook_profile_sync(username)

        if result:
            result.aiEnrichment = process_ai_enrichment(
                result.creator, result.recentPosts
            )

            with open("data/crawled_results.jsonl", "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
                    + "\n"
                )

            safe_category = (
                str(result.aiEnrichment.tags if result.aiEnrichment else [])
                .encode("ascii", "ignore")
                .decode()
            )
            logger.info(
                f"[WORKER] SUCCESS FB: {username} - {result.creator.followers} Followers. Type: {getattr(result.aiEnrichment, 'creatorType', 'UNKNOWN')}, Niche: {safe_category}"
            )

            java_payload = JavaMapper.from_ingest_payload(result)

            with open("data/java_payloads_debug.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(java_payload, ensure_ascii=False) + "\n")

            java_client.send_creator_data(java_payload)

            return result.model_dump(mode="json")
        else:
            logger.warning(
                f"[WORKER] FAILED: Could not crawl Facebook {username}. Retrying..."
            )
            raise self.retry(countdown=10)
    except Exception as e:
        logger.error(f"[WORKER] ERROR processing Facebook {username}: {e}")
        raise self.retry(exc=e, countdown=10)


@app.task(bind=True, max_retries=3)
def process_instagram_profile(self, username: str) -> Optional[dict]:
    logger.info(f"[WORKER] Starting Instagram crawl for: {username}")
    try:
        result = _crawl_instagram_profile_sync(username)

        if result:
            result.aiEnrichment = process_ai_enrichment(
                result.creator, result.recentPosts
            )

            with open("data/crawled_results.jsonl", "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
                    + "\n"
                )

            safe_category = (
                str(result.aiEnrichment.tags if result.aiEnrichment else [])
                .encode("ascii", "ignore")
                .decode()
            )
            logger.info(
                f"[WORKER] SUCCESS IG: {username} - {result.creator.followers} Followers. Type: {getattr(result.aiEnrichment, 'creatorType', 'UNKNOWN')}, Niche: {safe_category}"
            )

            java_payload = JavaMapper.from_ingest_payload(result)

            with open("data/java_payloads_debug.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(java_payload, ensure_ascii=False) + "\n")

            java_client.send_creator_data(java_payload)

            return result.model_dump(mode="json")
        else:
            logger.warning(
                f"[WORKER] FAILED: Could not crawl Instagram {username}. Retrying..."
            )
            raise self.retry(countdown=10)
    except Exception as e:
        logger.error(f"[WORKER] ERROR processing Instagram {username}: {e}")
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
