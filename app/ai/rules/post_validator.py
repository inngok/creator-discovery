from typing import Any, Optional
from pydantic import BaseModel

from app.ai.rules.rule_config import (
    MIN_FOLLOWERS_THRESHOLD,
    REGEX_COMMERCIAL_POST,
    REGEX_COMMERCIAL_SELLER,
    REGEX_KOC_REVIEW,
    REGEX_OPINION_KEYWORDS,
)
from app.core.logger import setup_logger
from app.core.models import IngestPayload

logger = setup_logger("post_validator")

MIN_VALID_POSTS = 3


class PostValidationResult(BaseModel):
    is_valid: bool
    quarantine_reason: Optional[str] = None
    details: dict[str, Any] = {}


def validate_post_crawl(
    payload: IngestPayload, is_refresh: bool = False
) -> PostValidationResult:
    """
    Bộ thẩm định sau cào sâu (Post-Crawl Validation Guard):
    Đảm bảo dữ liệu sau khi cào sâu Playwright đạt chuẩn chất lượng trước khi gửi sang Java Ingestion.
    Ngăn chặn các ca lọt lưới do Preflight chỉ xem SSR ban đầu.
    """
    if not payload or not payload.creator:
        return PostValidationResult(
            is_valid=False,
            quarantine_reason="MISSING_CREATOR_PAYLOAD",
            details={"error": "Payload or creator is None"},
        )

    creator = payload.creator
    recent_posts = payload.recentPosts or []

    # 1. Kiểm tra tính hợp lệ của định danh (Identity Validity)
    external_id = str(creator.externalId or "").strip()
    if not external_id or external_id == "0":
        logger.warning(f"[POST-VALIDATION] @{creator.username} thiếu externalId hợp lệ!")
        return PostValidationResult(
            is_valid=False,
            quarantine_reason="INVALID_EXTERNAL_ID",
            details={"external_id": external_id},
        )

    if not creator.username or not creator.username.strip():
        return PostValidationResult(
            is_valid=False,
            quarantine_reason="INVALID_USERNAME",
            details={},
        )

    # 2. Kiểm tra ngưỡng Followers (trừ khi là luồng Refresh định kỳ)
    followers = creator.followers or 0
    if not is_refresh and followers < MIN_FOLLOWERS_THRESHOLD:
        logger.warning(
            f"[POST-VALIDATION] @{creator.username} có follower thấp ({followers} < {MIN_FOLLOWERS_THRESHOLD})"
        )
        return PostValidationResult(
            is_valid=False,
            quarantine_reason=f"LOW_FOLLOWERS_{followers}",
            details={"followers": followers, "threshold": MIN_FOLLOWERS_THRESHOLD},
        )

    # 3. Kiểm tra chất lượng dữ liệu bài viết (Data Quality)
    if len(recent_posts) < MIN_VALID_POSTS:
        logger.warning(
            f"[POST-VALIDATION] @{creator.username} chỉ cào được {len(recent_posts)} bài (< {MIN_VALID_POSTS})"
        )
        return PostValidationResult(
            is_valid=False,
            quarantine_reason="INSUFFICIENT_POST_DATA",
            details={"crawled_posts": len(recent_posts), "min_required": MIN_VALID_POSTS},
        )

    # 4. Kiểm tra mâu thuẫn thực thể (Entity Contradiction: Shop / Catalog Selling)
    commercial_count = 0
    opinion_count = 0
    valid_captions_count = 0

    for post in recent_posts:
        caption = (post.caption or "").strip()
        if caption:
            valid_captions_count += 1
            if REGEX_COMMERCIAL_SELLER.search(caption) or REGEX_COMMERCIAL_POST.search(caption):
                commercial_count += 1
            if REGEX_OPINION_KEYWORDS.search(caption) or REGEX_KOC_REVIEW.search(caption):
                opinion_count += 1

    total_posts = len(recent_posts)
    commercial_ratio = commercial_count / total_posts if total_posts > 0 else 0.0

    # Nếu >= 60% bài viết là chào bán sỉ/lẻ hàng hóa rõ ràng và hoàn toàn không có bài review/nhận xét
    if commercial_ratio >= 0.6 and opinion_count == 0:
        logger.warning(
            f"[POST-VALIDATION] @{creator.username} bộc lộ là tài khoản Shop/Catalog chuyên nghiệp! "
            f"({commercial_count}/{total_posts} bài thương mại, {opinion_count} bài review)"
        )
        return PostValidationResult(
            is_valid=False,
            quarantine_reason="QUARANTINE_POST_VALIDATION_FAILED",
            details={
                "reason": "COMMERCIAL_SHOP_CATALOG_POSTS",
                "commercial_ratio": round(commercial_ratio, 2),
                "commercial_posts": commercial_count,
                "total_posts": total_posts,
                "opinion_posts": opinion_count,
            },
        )

    logger.info(
        f"[POST-VALIDATION PASSED] @{creator.username}: {total_posts} bài hợp lệ, "
        f"commercial_ratio={round(commercial_ratio, 2)}, opinion_posts={opinion_count}"
    )
    return PostValidationResult(
        is_valid=True,
        details={
            "total_posts": total_posts,
            "valid_captions": valid_captions_count,
            "commercial_ratio": round(commercial_ratio, 2),
            "opinion_posts": opinion_count,
        },
    )
