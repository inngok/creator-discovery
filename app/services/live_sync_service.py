import json
import os
from datetime import UTC, datetime
from typing import Any

from app.ai.rules.rule_config import Decision, ProcessingStatus
from app.clients.java_client import java_client
from app.core.candidate_staging import staging_repo
from app.core.logger import setup_logger
from app.core.models import CreatorInfo, IngestPayload, Platform, Post
from app.mappers.java_mapper import JavaMapper

logger = setup_logger("live_sync_service")

RESULTS_FILE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "crawled_results.jsonl",
)


def append_to_crawled_results(record: dict[str, Any]) -> None:
    """Lưu bản ghi cào thành công vào data/crawled_results.jsonl để phục vụ Overview & Dashboard."""
    try:
        os.makedirs(os.path.dirname(RESULTS_FILE_PATH), exist_ok=True)
        with open(RESULTS_FILE_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"Lỗi khi lưu vào crawled_results.jsonl: {e}")


def sync_creator_to_java_backend(
    platform_upper: str,
    profile_dict: dict[str, Any],
    recent_posts: list[dict[str, Any]],
    decision_result: Any,
) -> tuple[bool, str | None, Any]:
    """
    Đồng bộ dữ liệu Creator sang Java Backend (PostgreSQL) và AI Enrichment:
    - Nếu là KOC/KOL hoặc cần kiểm duyệt (NEEDS_MANAGER_REVIEW), đồng bộ sang Java Backend.
    - Duy trì dataConfidence <= 0.65 cho hồ sơ cần kiểm duyệt (DM-03).
    - Cập nhật Staging DB trạng thái INGESTED.

    Trả về: (database_synced: bool, sync_error: str | None, updated_decision_result)
    """
    unique_id = profile_dict["unique_id"]
    database_synced = False
    sync_error = None
    latest = staging_repo.get_candidate(platform_upper, unique_id)
    if latest and latest.decision == Decision.MANAGER_REJECTED:
        return False, "MANAGER_REJECTED", decision_result.model_copy(update={"decision": Decision.MANAGER_REJECTED})

    if decision_result.decision not in [
        Decision.AUTO_ACCEPT_KOC,
        Decision.AUTO_ACCEPT_KOL,
        Decision.MANAGER_APPROVED,
        Decision.NEEDS_MANAGER_REVIEW,
    ]:
        return database_synced, decision_result.decision.value, decision_result

    try:
        creator_obj = CreatorInfo(
            platform=Platform.TIKTOK if platform_upper == "TIKTOK" else Platform(platform_upper),
            username=unique_id,
            displayName=profile_dict.get("display_name", unique_id),
            externalId=profile_dict.get("author_id", unique_id),
            profileUrl=profile_dict.get("profile_url", ""),
            avatarUrl=profile_dict.get("avatar_url", ""),
            followers=profile_dict.get("followers", 0),
            following=profile_dict.get("following", 0),
            heartCount=profile_dict.get("heart_count", 0),
            videoCount=profile_dict.get("video_count", 0),
            bio=profile_dict.get("bio", ""),
            isVerified=profile_dict.get("is_verified", False),
        )
        posts_objs = [
            Post(
                platformPostId=p.get("platformPostId", ""),
                postUrl=p.get("postUrl", ""),
                caption=p.get("caption", ""),
                views=p.get("views", 0),
                likes=p.get("likes", 0),
                comments=p.get("comments", 0),
                shares=p.get("shares", 0),
                coverUrl=p.get("coverUrl", ""),
                postedAt=p.get("postedAt"),
            )
            for p in recent_posts
        ]
        from app.ai.analyzer import process_ai_enrichment

        ai_obj = process_ai_enrichment(creator_obj, posts_objs)
        if ai_obj is None:
            raise ValueError("AI enrichment returned no result; skip ingest")

        if (ai_obj.creatorType in {"USER", "UNKNOWN"}
                and decision_result.decision in {Decision.AUTO_ACCEPT_KOC, Decision.AUTO_ACCEPT_KOL}
                and decision_result.evidence.get("posts_count", 0) >= 5):
            # Conflicting classifications over a real post sample need adjudication.
            # Neither the rule engine nor the model silently overrides the other.
            decision_result = decision_result.model_copy(update={
                "decision": Decision.NEEDS_MANAGER_REVIEW,
                "reason_codes": [*decision_result.reason_codes, "AI_CREATOR_TYPE_CONFLICT"],
                "evidence": {**decision_result.evidence, "ai_creator_type": ai_obj.creatorType},
            })
            staging_repo.update_status_and_decision(
                platform=platform_upper, username=unique_id,
                status=ProcessingStatus.PREFLIGHT_DONE, decision=Decision.NEEDS_MANAGER_REVIEW,
                primary_type=decision_result.primary_type, reason_codes=decision_result.reason_codes,
            )

        if ai_obj.creatorType in {"BRAND", "USER"} and decision_result.decision != Decision.NEEDS_MANAGER_REVIEW:
            decision_result = decision_result.model_copy(update={
                "decision": Decision.AUTO_REJECT,
                "primary_type": ai_obj.creatorType,
                "reason_codes": ["AI_ENTITY_NOT_INDEPENDENT_CREATOR"],
            })
            staging_repo.update_status_and_decision(
                platform=platform_upper,
                username=unique_id,
                status=ProcessingStatus.PREFLIGHT_DONE,
                decision=Decision.AUTO_REJECT,
                primary_type=ai_obj.creatorType,
                reason_codes=decision_result.reason_codes,
            )
            raise ValueError("AI classification does not qualify for creator ingest")

        effective_type = decision_result.primary_type if decision_result.primary_type in {"KOC", "KOL"} else "KOC"
        ai_obj.creatorType = effective_type
        ingest_payload = IngestPayload(
            creator=creator_obj,
            recentPosts=posts_objs,
            aiEnrichment=ai_obj,
            source="LIVE_DIRECT_CRAWLER",
        )
        append_to_crawled_results(ingest_payload.model_dump(mode="json"))
        java_payload = JavaMapper.from_ingest_payload(ingest_payload)

        # Hồ sơ AI không chắc chắn (NEEDS_MANAGER_REVIEW) giữ dataConfidence <= 0.65 để kích hoạt hàng đợi kiểm định tại Java Backend
        if decision_result.decision == Decision.NEEDS_MANAGER_REVIEW:
            current_conf = float(java_payload.get("dataConfidence") or 0.65)
            java_payload["dataConfidence"] = min(current_conf, 0.65)

        with open("data/java_payloads_debug.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(java_payload, ensure_ascii=False) + "\n")

        sent_ok = java_client.send_creator_data(java_payload)
        if sent_ok:
            database_synced = True
            staging_repo.update_status_and_decision(
                platform=platform_upper,
                username=unique_id,
                status=ProcessingStatus.INGESTED,
            )
            logger.info(f"[LIVE SYNC] Đã gửi thành công @{unique_id} sang Java Backend Database!")
        else:
            sync_error = "JAVA_INGEST_FAILED"
            staging_repo.update_status_and_decision(platform=platform_upper, username=unique_id, status=ProcessingStatus.FAILED)
    except Exception as ingest_err:
        sync_error = type(ingest_err).__name__
        logger.warning(f"[LIVE SYNC] Không thể gửi payload sang Java Backend: {ingest_err}")

    return database_synced, sync_error, decision_result

