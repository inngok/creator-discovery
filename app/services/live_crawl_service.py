import math
import re
import time
from datetime import UTC, datetime
from typing import Any

from app.ai.rules.rule_config import ProcessingStatus, Decision
from app.ai.rules.rule_engine import RuleDecisionResult, RuleEngine
from app.ai.rules.signal_extractor import PostPreview, ProfileMetadata
from app.core.candidate_staging import CandidateStagingRecord, staging_repo
from app.core.logger import setup_logger
from app.core.metrics_tracker import metrics_tracker
from app.services.live_fetcher_service import fetch_raw_creator_profile
from app.services.live_sync_service import append_to_crawled_results, sync_creator_to_java_backend

logger = setup_logger("live_crawl_service")


def _apply_verified_topic_auto_accept(
    result: RuleDecisionResult, verified_snapshot: dict | None
) -> RuleDecisionResult:
    if result.decision not in {Decision.SKIP_INSUFFICIENT, Decision.NEEDS_MANAGER_REVIEW}:
        return result
    topic_accept = (verified_snapshot or {}).get("topic_auto_accept")
    if not isinstance(topic_accept, dict):
        return result
    creator_type = topic_accept.get("creator_type")
    confidence = topic_accept.get("confidence")
    evidence = topic_accept.get("evidence")
    if (creator_type not in {"KOC", "KOL"} or type(confidence) not in {int, float}
            or not math.isfinite(confidence) or not 0.85 <= confidence <= 1
            or not isinstance(evidence, list)):
        return result
    snapshot_posts = verified_snapshot.get("posts")
    if not isinstance(snapshot_posts, list):
        return result
    sources = {
        post.get("platformPostId"): post.get("caption", "")
        for post in snapshot_posts
        if isinstance(post, dict) and isinstance(post.get("platformPostId"), str)
        and isinstance(post.get("caption"), str)
    }
    proven_posts = set()
    for item in evidence:
        if not isinstance(item, dict):
            continue
        post_id, quote = item.get("postId"), item.get("quote")
        if (isinstance(post_id, str) and isinstance(quote, str) and len(quote.strip()) >= 12
                and quote.casefold() in sources.get(post_id, "").casefold()):
            proven_posts.add(post_id)
    if len(proven_posts) < 2:
        return result
    return result.model_copy(update={
        "decision": Decision.AUTO_ACCEPT_KOC if creator_type == "KOC" else Decision.AUTO_ACCEPT_KOL,
        "primary_type": creator_type,
        "reason_codes": [*result.reason_codes, "DISCOVERY_TOPIC_AUTO_ACCEPT"],
        "evidence": {**result.evidence, "topic_auto_accept": topic_accept},
    })


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


def crawl_single_koc_live(username_or_url: str, platform: str = "TIKTOK",
                          verified_snapshot: dict | None = None) -> dict[str, Any]:
    """
    Cào trực tiếp 1 KOC trong thời gian thực (TikTok, Facebook, Instagram):
    - Điều phối qua live_fetcher_service để lấy dữ liệu hồ sơ và bài đăng.
    - Đánh giá hồ sơ qua RuleEngine phân loại KOC/KOL/Shop/Spam.
    - Lưu Staging DB và gọi live_sync_service đồng bộ dữ liệu sang Java Backend.
    """
    platform_upper = (platform or "TIKTOK").upper()
    clean_user = clean_username_from_input(username_or_url, platform=platform_upper)
    previous = staging_repo.get_candidate(platform_upper, clean_user)
    if previous and previous.decision == Decision.MANAGER_REJECTED:
        return {"status": "error", "error": "Hồ sơ đã bị chuyên viên từ chối. Không thể tự động cào lại để thay đổi quyết định.", "username": clean_user, "platform": platform_upper}

    start_time = time.time()
    logger.info(f"[LIVE CRAWL] Bắt đầu cào trực tiếp cho @{clean_user} ({platform_upper})...")

    # 1. Bóc tách dữ liệu profile và clips theo nền tảng
    active_verified_snapshot = None
    if (verified_snapshot and verified_snapshot.get("platform") == platform_upper
            and verified_snapshot.get("username", "").casefold() == clean_user.casefold()
            and 0 <= time.time() - verified_snapshot.get("verified_at", 0) <= 300):
        active_verified_snapshot = verified_snapshot
        profile_dict = verified_snapshot["profile"]
        recent_posts = verified_snapshot["posts"]
        err_resp = None
    else:
        profile_dict, recent_posts, err_resp = fetch_raw_creator_profile(clean_user, platform_upper, start_time)
    if err_resp is not None:
        return err_resp

    market_check = None
    if platform_upper in {"FACEBOOK", "INSTAGRAM"}:
        from app.core.vietnam_evidence import vietnam_market_evidence
        market_check = vietnam_market_evidence(profile_dict, recent_posts)
        if not market_check["matches"]:
            return {"status": "error", "platform": platform_upper, "username": clean_user,
                    "error": "Tài khoản không đủ bằng chứng thuộc thị trường Việt Nam.",
                    "market_check": market_check}

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
        bio_links=profile_dict.get("bio_links", []),
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

    # Nếu Rule Engine chưa chắc chắn (SKIP_INSUFFICIENT hoặc NEEDS_MANAGER_REVIEW) và có bài đăng, xin ý kiến AI Reviewer
    if decision_result.decision in {
        Decision.SKIP_INSUFFICIENT, Decision.NEEDS_MANAGER_REVIEW
    } and len(previews) > 0:
        from app.ai.analyzer import get_llm_enrichment
        from app.services.ai_candidate_review import review_candidate
        try:
            decision_result = review_candidate(
                profile_meta, previews, decision_result, get_llm_enrichment
            )
        except Exception as exc:
            logger.warning("AI second opinion failed in live crawl: %s", type(exc).__name__)

    decision_result = _apply_verified_topic_auto_accept(
        decision_result, active_verified_snapshot
    )

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
        "market_check": market_check,
    }
