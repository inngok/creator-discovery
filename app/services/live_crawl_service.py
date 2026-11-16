import re
import time
from datetime import UTC, datetime
from typing import Any

from app.ai.rules.rule_config import ProcessingStatus
from app.ai.rules.rule_engine import RuleEngine
from app.ai.rules.signal_extractor import PostPreview, ProfileMetadata
from app.core.candidate_staging import CandidateStagingRecord, staging_repo
from app.core.logger import setup_logger
from app.core.metrics_tracker import metrics_tracker
from app.services.live_fetcher_service import fetch_raw_creator_profile
from app.services.live_sync_service import append_to_crawled_results, sync_creator_to_java_backend

logger = setup_logger("live_crawl_service")


def clean_username_from_input(val: str, platform: str = "TIKTOK") -> str:
    cleaned = val.strip()
    plat = platform.upper()
    if "tiktok.com" in cleaned or plat == "TIKTOK":
        match = re.search(r"tiktok\.com/@?([^/?&#]+)", cleaned)
        if match:
            cleaned = match.group(1)
    elif "facebook.com" in cleaned or plat == "FACEBOOK":
        from app.crawlers.facebook_crawler import clean_facebook_username

        cleaned = clean_facebook_username(cleaned)
    elif "instagram.com" in cleaned or plat == "INSTAGRAM":
        match = re.search(r"instagram\.com/([^/?&#]+)", cleaned)
        if match:
            cleaned = match.group(1)
    return cleaned.removeprefix("@").strip().rstrip("/")


def crawl_single_koc_live(username_or_url: str, platform: str = "TIKTOK") -> dict[str, Any]:
    """
    Cào trực tiếp 1 KOC trong thời gian thực (TikTok, Facebook, Instagram):
    - Điều phối qua live_fetcher_service để lấy dữ liệu hồ sơ và bài đăng.
    - Đánh giá hồ sơ qua RuleEngine phân loại KOC/KOL/Shop/Spam.
    - Lưu Staging DB và gọi live_sync_service đồng bộ dữ liệu sang Java Backend.
    """
    platform_upper = (platform or "TIKTOK").upper()
    clean_user = clean_username_from_input(username_or_url, platform=platform_upper)

    start_time = time.time()
    logger.info(f"[LIVE CRAWL] Bắt đầu cào trực tiếp cho @{clean_user} ({platform_upper})...")

    # 1. Bóc tách dữ liệu profile và clips theo nền tảng
    profile_dict, recent_posts, err_resp = fetch_raw_creator_profile(clean_user, platform_upper, start_time)
    if err_resp is not None:
        return err_resp

    author_id = profile_dict["author_id"]
    unique_id = profile_dict["unique_id"]
    display_name = profile_dict["display_name"]
    followers = profile_dict["followers"]
    following = profile_dict["following"]
    heart_count = profile_dict["heart_count"]
    video_count = profile_dict["video_count"]
    bio = profile_dict["bio"]
    avatar_url = profile_dict["avatar_url"]
    is_verified = profile_dict["is_verified"]
    profile_url = profile_dict["profile_url"]
    latency_ms = profile_dict["latency_ms"]

    # 2. Đánh giá Rule Engine
    profile_meta = ProfileMetadata(
        platform=platform_upper,
        external_id=author_id,
        username=f"@{unique_id}",
        display_name=display_name,
        bio=bio,
        followers=followers,
        is_verified=is_verified,
    )
    previews = [
        PostPreview(
            post_id=p.get("platformPostId", ""),
            caption=p.get("caption", ""),
            views=p.get("views", 0),
            likes=p.get("likes", 0),
        )
        for p in recent_posts
    ]
    decision_result = RuleEngine.evaluate(profile_meta, previews)

    # 3. Lưu Staging DB
    staging_record = CandidateStagingRecord(
        platform=platform_upper,
        external_id=author_id,
        username=unique_id,
        display_name=display_name,
        profile_url=profile_url,
        processing_status=ProcessingStatus.PREFLIGHT_DONE,
        decision=decision_result.decision,
        primary_type=decision_result.primary_type,
        reason_codes=decision_result.reason_codes,
        matched_signals=decision_result.matched_signals,
        evidence_summary=decision_result.evidence,
        last_checked_at=datetime.now(UTC).isoformat(),
    )
    staging_repo.upsert_candidate(staging_record)

    # 4. Lưu kết quả crawled_results.jsonl sơ bộ
    crawled_payload = {
        "creator": {
            "platform": platform_upper,
            "username": unique_id,
            "displayName": display_name,
            "externalId": author_id,
            "profileUrl": profile_url,
            "avatarUrl": avatar_url,
            "followers": followers,
            "following": following,
            "heartCount": heart_count,
            "videoCount": video_count,
            "bio": bio,
            "isVerified": is_verified,
        },
        "recentPosts": recent_posts,
        "aiEnrichment": {
            "creatorType": decision_result.primary_type,
            "decision": decision_result.decision.value,
            "matchedSignals": decision_result.matched_signals,
        },
        "source": "LIVE_DIRECT_CRAWLER",
        "collectedAt": datetime.now(UTC).isoformat(),
    }
    append_to_crawled_results(crawled_payload)

    # 5. Đồng bộ sang Java Backend và chạy AI Enrichment
    database_synced, sync_error, decision_result = sync_creator_to_java_backend(
        platform_upper=platform_upper,
        profile_dict=profile_dict,
        recent_posts=recent_posts,
        decision_result=decision_result,
    )

    # 6. Ghi nhận metric thành công
    metrics_tracker.record_request(
        platform=platform_upper,
        username=unique_id,
        latency_ms=latency_ms,
        status_code=200,
        is_success=True,
    )

    logger.info(
        f"[LIVE CRAWL] Cào thành công @{unique_id} ({latency_ms:.1f}ms): {followers:,} fl | {len(recent_posts)} clips | {decision_result.primary_type}"
    )

    return {
        "status": "success",
        "platform": platform_upper,
        "username": unique_id,
        "display_name": display_name,
        "author_id": author_id,
        "profile_url": profile_url,
        "avatar_url": avatar_url,
        "followers": followers,
        "following": following,
        "heart_count": heart_count,
        "video_count": video_count,
        "bio": bio,
        "is_verified": is_verified,
        "decision": decision_result.decision.value,
        "primary_type": decision_result.primary_type,
        "reason_codes": decision_result.reason_codes,
        "database_synced": database_synced,
        "sync_error": sync_error,
        "latency_ms": round(latency_ms, 2),
        "recent_posts": recent_posts,
        "collected_at": datetime.now(UTC).isoformat(),
    }
