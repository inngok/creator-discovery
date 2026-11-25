import time
from datetime import UTC, datetime
from typing import Any

from app.core.logger import setup_logger
from app.core.post_dates import normalize_post_date
from app.core.metrics_tracker import metrics_tracker
from app.crawlers.preflight_fetcher import (
    fetch_tiktok_ssr_raw_data,
    normalize_tiktok_item_list,
)

logger = setup_logger("live_fetcher_service")


def fetch_raw_creator_profile(
    clean_user: str,
    platform_upper: str,
    start_time: float,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any] | None]:
    """
    Bóc tách thông tin hồ sơ và danh sách bài đăng gần nhất theo từng nền tảng:
    - FACEBOOK: Playwright Stealth qua OpenGraph / Mobile Fallback
    - INSTAGRAM: Playwright Stealth qua Mobile Safari OpenGraph
    - TIKTOK: Live SSR Payload (không cần tài khoản đăng nhập)

    Trả về: (profile_dict, recent_posts_list, error_dict)
    Nếu thất bại, profile_dict là None và error_dict chứa thông tin trả về cho client.
    """
    if platform_upper == "FACEBOOK":
        from app.crawlers.facebook_crawler import _crawl_facebook_profile_sync

        fb_payload = _crawl_facebook_profile_sync(clean_user)
        latency_ms = (time.time() - start_time) * 1000
        if not fb_payload or not fb_payload.creator:
            metrics_tracker.record_request(
                platform="FACEBOOK",
                username=clean_user,
                latency_ms=latency_ms,
                status_code=404,
                is_success=False,
                error_msg="Không thể cào thông tin Page Facebook (yêu cầu login hoặc không tồn tại)",
            )
            return None, [], {
                "status": "error",
                "username": clean_user,
                "platform": "FACEBOOK",
                "latency_ms": round(latency_ms, 2),
                "error": "Không thể kết nối hoặc Facebook yêu cầu đăng nhập.",
            }

        creator = fb_payload.creator
        unique_id = str(creator.username or clean_user).lstrip("@")
        profile_dict = {
            "author_id": creator.externalId or clean_user,
            "unique_id": unique_id,
            "display_name": creator.displayName or unique_id,
            "followers": creator.followers or 0,
            "following": 0,
            "heart_count": 0,
            "video_count": len(fb_payload.recentPosts),
            "bio": creator.bio or "",
            "avatar_url": creator.avatarUrl or "",
            "is_verified": bool(getattr(creator, "isVerified", False)),
            "profile_url": creator.profileUrl or f"https://www.facebook.com/{unique_id}",
            "latency_ms": latency_ms,
        }
        recent_posts = [
            {
                "platformPostId": p.platformPostId,
                "postUrl": p.postUrl,
                "caption": p.caption,
                "views": p.views or 0,
                "likes": p.likes or 0,
                "comments": p.comments or 0,
                "shares": p.shares or 0,
                "coverUrl": getattr(p, "coverUrl", "") or "",
                "postedAt": p.postedAt,
            }
            for p in fb_payload.recentPosts
        ]
        return profile_dict, recent_posts, None

    elif platform_upper == "INSTAGRAM":
        from app.crawlers.instagram_crawler import _crawl_instagram_profile_sync

        ig_payload = _crawl_instagram_profile_sync(clean_user)
        latency_ms = (time.time() - start_time) * 1000
        if not ig_payload or not ig_payload.creator:
            metrics_tracker.record_request(
                platform="INSTAGRAM",
                username=clean_user,
                latency_ms=latency_ms,
                status_code=404,
                is_success=False,
                error_msg="Không thể cào thông tin Instagram",
            )
            return None, [], {
                "status": "error",
                "username": clean_user,
                "platform": "INSTAGRAM",
                "latency_ms": round(latency_ms, 2),
                "error": "Không thể kết nối hoặc Instagram yêu cầu đăng nhập.",
            }

        creator = ig_payload.creator
        unique_id = str(creator.username or clean_user).lstrip("@")
        profile_dict = {
            "author_id": creator.externalId or clean_user,
            "unique_id": unique_id,
            "display_name": creator.displayName or unique_id,
            "followers": creator.followers or 0,
            "following": 0,
            "heart_count": 0,
            "video_count": len(ig_payload.recentPosts),
            "bio": creator.bio or "",
            "avatar_url": creator.avatarUrl or "",
            "is_verified": bool(getattr(creator, "isVerified", False)),
            "profile_url": creator.profileUrl or f"https://www.instagram.com/{unique_id}",
            "latency_ms": latency_ms,
        }
        recent_posts = [
            {
                "platformPostId": p.platformPostId,
                "postUrl": p.postUrl,
                "caption": p.caption,
                "views": p.views or 0,
                "likes": p.likes or 0,
                "comments": p.comments or 0,
                "shares": p.shares or 0,
                "coverUrl": getattr(p, "coverUrl", "") or "",
                "postedAt": p.postedAt,
            }
            for p in ig_payload.recentPosts
        ]
        return profile_dict, recent_posts, None

    else:
        # Mặc định TIKTOK: Bóc Live SSR
        ssr_data = fetch_tiktok_ssr_raw_data(clean_user)
        latency_ms = (time.time() - start_time) * 1000

        if not ssr_data:
            metrics_tracker.record_request(
                platform=platform_upper,
                username=clean_user,
                latency_ms=latency_ms,
                status_code=429,
                is_success=False,
                is_rate_limited=True,
                error_msg="Blocked or Rate Limited by TikTok",
            )
            return None, [], {
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
            return None, [], {
                "status": "error",
                "username": clean_user,
                "platform": platform_upper,
                "latency_ms": round(latency_ms, 2),
                "error": "Không tìm thấy hồ sơ người dùng trên TikTok.",
            }

        unique_id = str(user_info.get("uniqueId", clean_user)).lstrip("@")
        bio_link = ""
        if "bioLink" in user_info and isinstance(user_info["bioLink"], dict) and "link" in user_info["bioLink"]:
            bio_link = user_info["bioLink"]["link"]
            if "target=" in bio_link:
                try:
                    import urllib.parse
                    bio_link = urllib.parse.unquote(bio_link.split("target=")[1].split("&")[0])
                except Exception:
                    pass

        signature = user_info.get("signature", "")
        full_bio = f"{signature} {bio_link}".strip()
        bio_links = [bio_link] if bio_link else []

        profile_dict = {
            "author_id": str(user_info.get("id", "")),
            "unique_id": unique_id,
            "display_name": user_info.get("nickname", unique_id),
            "followers": int(stats_module.get("followerCount", 0)),
            "following": int(stats_module.get("followingCount", 0)),
            "heart_count": int(stats_module.get("heartCount", 0)),
            "video_count": int(stats_module.get("videoCount", 0)),
            "bio": full_bio,
            "bio_link": bio_link,
            "bio_links": bio_links,
            "avatar_url": (
                user_info.get("avatarLarger")
                or user_info.get("avatarMedium")
                or user_info.get("avatarThumb")
                or ""
            ),
            "is_verified": bool(user_info.get("verified", False)),
            "profile_url": f"https://www.tiktok.com/@{unique_id}",
            "latency_ms": latency_ms,
        }

        recent_posts = []
        item_list = ssr_data.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {}).get("itemList", [])
        if not item_list:
            item_list = ssr_data.get("itemList", [])
        item_list = normalize_tiktok_item_list(item_list, unique_id)[:12]

        for item in item_list[:12]:
            pid = str(item.get("id", ""))
            desc = item.get("desc", "")
            pstats = item.get("stats", {}) or {}
            video_obj = item.get("video", {}) or {}
            cover_url = video_obj.get("cover") or video_obj.get("originCover") or ""
            create_time = item.get("createTime")
            posted_at = normalize_post_date(create_time)
            is_pinned = bool(item.get("isTop") or item.get("isPinned"))

            recent_posts.append({
                "platformPostId": pid,
                "postUrl": f"https://www.tiktok.com/@{unique_id}/video/{pid}",
                "caption": desc,
                "views": int(pstats.get("playCount", 0) or 0),
                "likes": int(pstats.get("diggCount", 0) or 0),
                "comments": int(pstats.get("commentCount", 0) or 0),
                "shares": int(pstats.get("shareCount", 0) or 0),
                "coverUrl": cover_url,
                "postedAt": posted_at,
                "isPinned": is_pinned,
            })

        return profile_dict, recent_posts, None

