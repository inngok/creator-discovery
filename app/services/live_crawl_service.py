import json
import os
import re
import time
from datetime import UTC, datetime
from typing import Any

from app.ai.rules.rule_config import Decision, ProcessingStatus
from app.ai.rules.rule_engine import RuleEngine
from app.ai.rules.signal_extractor import ProfileMetadata
from app.core.candidate_staging import CandidateStagingRecord, staging_repo
from app.core.logger import setup_logger
from app.core.metrics_tracker import metrics_tracker
from app.crawlers.preflight_fetcher import fetch_tiktok_ssr_raw_data

logger = setup_logger("live_crawl_service")

RESULTS_FILE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "crawled_results.jsonl",
)


def append_to_crawled_results(record: dict[str, Any]):
    """Lưu bản ghi cào thành công vào data/crawled_results.jsonl để phục vụ Overview & Dashboard."""
    try:
        os.makedirs(os.path.dirname(RESULTS_FILE_PATH), exist_ok=True)
        with open(RESULTS_FILE_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"Lỗi khi lưu vào crawled_results.jsonl: {e}")


def clean_username_from_input(val: str) -> str:
    cleaned = val.strip()
    if "tiktok.com" in cleaned:
        match = re.search(r"tiktok\.com/@?([^/?&#]+)", cleaned)
        if match:
            cleaned = match.group(1)
    return cleaned.removeprefix("@").strip()


def crawl_single_koc_live(username_or_url: str, platform: str = "TIKTOK") -> dict[str, Any]:
    """
    Cào trực tiếp 1 KOC trong thời gian thực:
    - Bóc Live SSR DOM TikTok.
    - Lấy thông tin Profile (Avatar, Bio, Followers, Verified) + video gần nhất.
    - Chạy Rule Engine phân loại KOC.
    - Ghi nhận Metrics (Latency ms, Rate limit 429 nếu có).
    - Cập nhật Staging DB & crawled_results.jsonl.
    """
    clean_user = clean_username_from_input(username_or_url)
    platform_upper = platform.upper()

    start_time = time.time()
    logger.info(f"[LIVE CRAWL] Bắt đầu cào trực tiếp cho @{clean_user} ({platform_upper})...")

    # 1. Gọi fetch live SSR
    ssr_data = fetch_tiktok_ssr_raw_data(clean_user)
    latency_ms = (time.time() - start_time) * 1000

    if not ssr_data:
        # Ghi nhận metric thất bại / rate limit
        metrics_tracker.record_request(
            platform=platform_upper,
            username=clean_user,
            latency_ms=latency_ms,
            status_code=429,
            is_success=False,
            is_rate_limited=True,
            error_msg="Blocked or Rate Limited by TikTok",
        )
        return {
            "status": "error",
            "username": clean_user,
            "platform": platform_upper,
            "latency_ms": round(latency_ms, 2),
            "error": "Không thể kết nối hoặc bị Rate Limit bởi TikTok.",
        }

    user_module = ssr_data.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {}).get("userInfo", {})
    user_info = user_module.get("user", {})
    stats_module = user_module.get("stats", {})

    if not user_info:
        metrics_tracker.record_request(
            platform=platform_upper,
            username=clean_user,
            latency_ms=latency_ms,
            status_code=404,
            is_success=False,
            error_msg="User not found in SSR",
        )
        return {
            "status": "error",
            "username": clean_user,
            "platform": platform_upper,
            "latency_ms": round(latency_ms, 2),
            "error": "Không tìm thấy hồ sơ người dùng trên TikTok.",
        }

    author_id = str(user_info.get("id", ""))
    unique_id = str(user_info.get("uniqueId", clean_user))
    display_name = user_info.get("nickname", unique_id)
    followers = int(stats_module.get("followerCount", 0))
    following = int(stats_module.get("followingCount", 0))
    heart_count = int(stats_module.get("heartCount", 0))
    video_count = int(stats_module.get("videoCount", 0))
    bio = user_info.get("signature", "")
    avatar_url = (
        user_info.get("avatarLarger")
        or user_info.get("avatarMedium")
        or user_info.get("avatarThumb")
        or ""
    )
    is_verified = bool(user_info.get("verified", False))
    profile_url = f"https://www.tiktok.com/@{unique_id}"

    # 2. Bóc danh sách video/clip từ itemList trong SSR
    recent_posts = []
    item_list = ssr_data.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {}).get("itemList", [])
    if not item_list:
        item_list = ssr_data.get("itemList", [])

    for item in item_list[:10]:
        pid = str(item.get("id", ""))
        desc = item.get("desc", "")
        pstats = item.get("stats", {})
        video_obj = item.get("video", {})
        cover_url = video_obj.get("cover") or video_obj.get("originCover") or ""
        create_time = item.get("createTime")
        posted_at = datetime.fromtimestamp(int(create_time), tz=UTC).isoformat() if create_time else None

        recent_posts.append({
            "platformPostId": pid,
            "postUrl": f"https://www.tiktok.com/@{unique_id}/video/{pid}",
            "caption": desc,
            "views": int(pstats.get("playCount", 0)),
            "likes": int(pstats.get("diggCount", 0)),
            "comments": int(pstats.get("commentCount", 0)),
            "shares": int(pstats.get("shareCount", 0)),
            "coverUrl": cover_url,
            "postedAt": posted_at,
        })

    # 3. Đánh giá Rule Engine
    profile_meta = ProfileMetadata(
        platform=platform_upper,
        external_id=author_id,
        username=f"@{unique_id}",
        display_name=display_name,
        bio=bio,
        followers=followers,
        is_verified=is_verified,
    )
    decision_result = RuleEngine.evaluate(profile_meta, [])

    # 4. Lưu Staging DB
    staging_record = CandidateStagingRecord(
        platform=platform_upper,
        external_id=author_id,
        username=unique_id,
        display_name=display_name,
        profile_url=profile_url,
        processing_status=ProcessingStatus.INGESTED,
        decision=decision_result.decision,
        primary_type=decision_result.primary_type,
        reason_codes=decision_result.reason_codes,
        matched_signals=decision_result.matched_signals,
        evidence_summary=decision_result.evidence,
        last_checked_at=datetime.now(UTC).isoformat(),
    )
    staging_repo.upsert_candidate(staging_record)

    # 5. Lưu kết quả crawled_results.jsonl
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
        "latency_ms": round(latency_ms, 2),
        "recent_posts": recent_posts,
        "collected_at": datetime.now(UTC).isoformat(),
    }
