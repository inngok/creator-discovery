from contextvars import ContextVar, Token
from typing import Any, Callable

CrawlProgressEvent = dict[str, str | None]
CrawlProgressCallback = Callable[[CrawlProgressEvent], None]
DiscoveryRejectionCallback = Callable[[dict[str, Any]], None]

_progress_callback: ContextVar[CrawlProgressCallback | None] = ContextVar(
    "crawl_progress_callback", default=None
)
_rejection_callback: ContextVar[DiscoveryRejectionCallback | None] = ContextVar(
    "discovery_rejection_callback", default=None
)


def set_crawl_progress_callback(callback: CrawlProgressCallback) -> Token:
    return _progress_callback.set(callback)


def reset_crawl_progress_callback(token: Token) -> None:
    _progress_callback.reset(token)


def set_discovery_rejection_callback(callback: DiscoveryRejectionCallback) -> Token:
    return _rejection_callback.set(callback)


def reset_discovery_rejection_callback(token: Token) -> None:
    _rejection_callback.reset(token)


def report_crawl_progress(
    message: str,
    *,
    platform: str | None = None,
    keyword: str | None = None,
    stage: str | None = None,
) -> None:
    callback = _progress_callback.get()
    if callback is not None:
        callback({
            "message": message,
            "platform": platform,
            "keyword": keyword,
            "stage": stage,
        })


def report_discovery_rejection(item: dict[str, Any]) -> None:
    callback = _rejection_callback.get()
    if callback is not None and isinstance(item, dict) and item.get("username"):
        callback(item)


def build_discovery_rejection_result(
    candidate: dict[str, Any],
    *,
    reason_codes: list[str],
    summary_reason: str,
    profile: dict[str, Any] | None = None,
    posts: list[dict[str, Any]] | None = None,
    primary_type: str | None = None,
) -> dict[str, Any]:
    """Normalize a discovery-filtered candidate so UI can display why it was excluded."""
    snapshot = candidate.get("_verified_snapshot") or {}
    prof = profile or snapshot.get("profile") or {}
    raw_posts = posts if posts is not None else snapshot.get("posts") or []
    creator_check = candidate.get("creator_check") or {}
    username = (prof.get("unique_id") or candidate.get("username") or "").strip().lstrip("@")
    platform = (candidate.get("platform") or snapshot.get("platform") or "TIKTOK").upper()
    return {
        "status": "success",
        "platform": platform,
        "username": username,
        "display_name": prof.get("display_name") or candidate.get("display_name") or username,
        "author_id": prof.get("author_id") or candidate.get("externalId") or "",
        "profile_url": prof.get("profile_url") or candidate.get("profile_url") or "",
        "avatar_url": prof.get("avatar_url") or candidate.get("avatar_url") or "",
        "followers": int(prof.get("followers") if prof.get("followers") is not None else candidate.get("followers") or 0),
        "following": int(prof.get("following") or 0),
        "heart_count": int(prof.get("heart_count") or 0),
        "video_count": int(prof.get("video_count") or len(raw_posts) or 0),
        "bio": prof.get("bio") or candidate.get("bio_snippet") or "",
        "is_verified": bool(prof.get("is_verified", False)),
        "decision": "AUTO_REJECT",
        "primary_type": primary_type or creator_check.get("primary_type") or "UNKNOWN",
        "reason_codes": list(dict.fromkeys(reason_codes)),
        "database_synced": False,
        "sync_error": summary_reason,
        "latency_ms": round(float(prof.get("latency_ms") or 0.0), 2),
        "recent_posts": list(raw_posts[:5]),
    }
