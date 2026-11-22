from datetime import UTC, datetime
from typing import Any, Optional

from app.core.post_metrics_models import MetricStatusEnum
from app.services.post_metrics.url_validator import PostMetricsException

MAX_SAFE_INTEGER = 9007199254740991


def normalize_metric_int(val: Any) -> Optional[int]:
    """Chuẩn hóa giá trị metric về số nguyên không âm an toàn hoặc None."""
    if val is None:
        return None
    try:
        int_val = int(val)
        return min(int_val, MAX_SAFE_INTEGER) if int_val >= 0 else None
    except (ValueError, TypeError):
        return None


def calculate_engagement_rate(
    platform: str,
    views: Optional[int],
    likes: Optional[int],
    comments: Optional[int],
    shares: Optional[int],
    views_status: MetricStatusEnum,
    likes_status: MetricStatusEnum,
    comments_status: MetricStatusEnum,
    shares_status: MetricStatusEnum,
) -> tuple[Optional[float], Optional[str]]:
    """Tính ER % kèm công thức, trả về (None, None) nếu thiếu bất kỳ chỉ số bắt buộc nào."""
    if views_status != MetricStatusEnum.AVAILABLE or views is None or views <= 0:
        return None, None

    if platform == "TIKTOK":
        if (
            likes_status == MetricStatusEnum.AVAILABLE
            and comments_status == MetricStatusEnum.AVAILABLE
            and shares_status == MetricStatusEnum.AVAILABLE
            and likes is not None
            and comments is not None
            and shares is not None
        ):
            return round((likes + comments + shares) / views * 100, 2), "(likes + comments + shares) / views * 100"

    elif platform in ("YOUTUBE", "INSTAGRAM", "FACEBOOK"):
        if (
            likes_status == MetricStatusEnum.AVAILABLE
            and comments_status == MetricStatusEnum.AVAILABLE
            and likes is not None
            and comments is not None
        ):
            return round((likes + comments) / views * 100, 2), "(likes + comments) / views * 100"

    return None, None


def build_success_payload(
    platform: str,
    post_url: str,
    canonical_post_url: str,
    post_id: str,
    author: Optional[dict] = None,
    views: Optional[int] = None,
    likes: Optional[int] = None,
    comments: Optional[int] = None,
    shares: Optional[int] = None,
    views_status: MetricStatusEnum = MetricStatusEnum.NOT_AVAILABLE,
    likes_status: MetricStatusEnum = MetricStatusEnum.NOT_AVAILABLE,
    comments_status: MetricStatusEnum = MetricStatusEnum.NOT_AVAILABLE,
    shares_status: MetricStatusEnum = MetricStatusEnum.NOT_AVAILABLE,
    caption: Optional[str] = None,
    thumbnail_url: Optional[str] = None,
    posted_at: Optional[str] = None,
    tracked_at: Optional[str] = None,
    metric_errors: Optional[dict] = None,
    warnings: Optional[list[str]] = None,
    method: str = "SSR",
) -> dict[str, Any]:
    """Xây dựng response payload chuẩn schema 1.1."""
    v = normalize_metric_int(views) if views_status == MetricStatusEnum.AVAILABLE else None
    l = normalize_metric_int(likes) if likes_status == MetricStatusEnum.AVAILABLE else None
    c = normalize_metric_int(comments) if comments_status == MetricStatusEnum.AVAILABLE else None
    s = normalize_metric_int(shares) if shares_status == MetricStatusEnum.AVAILABLE else None

    # Nếu tất cả metric đều không AVAILABLE -> lỗi 502
    all_unavailable = all(
        st != MetricStatusEnum.AVAILABLE
        for st in (views_status, likes_status, comments_status, shares_status)
    )
    if all_unavailable:
        raise PostMetricsException("NO_METRICS_AVAILABLE", "Không có số liệu metric nào khả dụng từ bài đăng", 502)

    er, er_formula = calculate_engagement_rate(
        platform, v, l, c, s, views_status, likes_status, comments_status, shares_status
    )

    now_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    actual_tracked_at = tracked_at or now_utc

    author_data = author or {}
    final_warnings = list(warnings or [])
    final_errors = dict(metric_errors or {})

    # Tự động gán error code cho các metric FETCH_FAILED
    status_map = {
        "views": views_status.value,
        "likes": likes_status.value,
        "comments": comments_status.value,
        "shares": shares_status.value,
    }
    for m_name, m_stat in status_map.items():
        if m_stat == MetricStatusEnum.FETCH_FAILED.value and m_name not in final_errors:
            final_errors[m_name] = {"code": "COUNTER_UNAVAILABLE", "retryable": True}
            warn_code = f"{m_name.upper()}_UNAVAILABLE"
            if warn_code not in final_warnings:
                final_warnings.append(warn_code)

    return {
        "schemaVersion": "1.1",
        "status": "success",
        "platform": platform,
        "postUrl": post_url,
        "canonicalPostUrl": canonical_post_url,
        "postId": post_id,
        "author": {
            "externalId": author_data.get("externalId") or None,
            "username": author_data.get("username") or None,
            "displayName": author_data.get("displayName") or None,
        },
        "metrics": {
            "views": v,
            "likes": l,
            "comments": c,
            "shares": s,
            "engagementRate": er,
        },
        "metricStatus": status_map,
        "metricErrors": final_errors,
        "content": {
            "caption": caption or None,
            "thumbnailUrl": thumbnail_url or None,
            "postedAt": posted_at or None,
        },
        "trackedAt": actual_tracked_at,
        "servedAt": now_utc,
        "collection": {
            "source": "PUBLIC_PAGE",
            "method": method,
            "cacheHit": False,
            "scope": "LIFETIME_AT_COLLECTION",
            "engagementRateFormula": er_formula,
            "engagementRateUnit": "PERCENT",
        },
        "warnings": final_warnings,
    }
