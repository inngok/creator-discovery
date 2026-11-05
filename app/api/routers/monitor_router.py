from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from app.core.candidate_staging import staging_repo
from app.core.metrics_tracker import metrics_tracker
from app.services.crawled_result_service import (
    get_crawled_result_by_username,
    list_crawled_results,
)

router = APIRouter(prefix="/api/v1/monitor", tags=["Crawl Monitor"])


@router.get(
    "/overview",
    summary="1 API duy nhất lấy toàn bộ: Thống kê + Hoạt động đang cào + Dữ liệu đã cào",
    description="Frontend chỉ cần gọi 1 API này là có đủ cả số lượng, tiến trình đang cào và dữ liệu cào được để render giao diện.",
)
async def get_crawl_overview(
    limit: int = Query(20, ge=1, le=100, description="Số lượng bản ghi lấy về"),
) -> dict[str, Any]:
    """
    Trả về:
    - stats: Tóm tắt tổng số, số kênh đang cào (active), thành công, lỗi...
    - activities: Danh sách tiến trình cào (ai đang cào, ai xong, ai bị reject...)
    - crawled_data: Dữ liệu Creator vừa cào được (Follower, Bio, Bài viết, AI Tags...)
    - metrics: Chỉ số hiệu suất thời gian thực (429, latency)
    """
    # 1. Thống kê
    stats = staging_repo.get_summary_stats()

    # 2. Hoạt động cào gần nhất
    activity_records, total_activities = staging_repo.list_candidates(limit=limit, offset=0)
    activities = [
        {
            "id": r.id,
            "platform": r.platform,
            "username": r.username,
            "displayName": r.display_name,
            "status": r.processing_status.value if hasattr(r.processing_status, "value") else str(r.processing_status),
            "decision": r.decision.value if hasattr(r.decision, "value") else str(r.decision),
            "primaryType": r.primary_type,
            "updatedAt": r.updated_at,
        }
        for r in activity_records
    ]

    # 3. Dữ liệu đã cào được
    crawled_records, total_crawled = list_crawled_results(limit=limit, offset=0)

    # 4. Chỉ số hiệu suất thời gian thực
    metrics = metrics_tracker.get_metrics_summary()

    return {
        "stats": stats,
        "metrics": metrics,
        "activities": {
            "total": total_activities,
            "items": activities,
        },
        "crawled_data": {
            "total": total_crawled,
            "items": crawled_records,
        },
    }


@router.get(
    "/metrics",
    summary="Hiển thị chỉ số hiệu suất của Crawler thời gian thực (Performance Metrics)",
    description="Trả về tổng số request, số thành công, số lỗi Rate Limit 429, tốc độ phản hồi trung bình (latency) và lịch sử gần nhất.",
)
async def get_crawler_performance_metrics() -> dict[str, Any]:
    return metrics_tracker.get_metrics_summary()


@router.get(
    "/creator/{username}",
    summary="Xem chi tiết 1 Creator cụ thể",
    description="Gọi API này khi người dùng bấm vào xem chi tiết một Creator cụ thể.",
)
async def get_creator_detail(
    username: str,
    platform: Optional[str] = Query(None, description="Nền tảng: TIKTOK, FACEBOOK, INSTAGRAM"),
) -> dict[str, Any]:
    # Lấy dữ liệu cào chi tiết
    crawled = get_crawled_result_by_username(username, platform=platform)

    # Lấy thông tin trạng thái staging
    staging = staging_repo.get_candidate_by_identifier(username, platform=platform)

    if not crawled and not staging:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy dữ liệu cho Creator '@{username}'")

    return {
        "username": username,
        "platform": platform or (crawled.get("platform") if crawled else (staging.platform if staging else "TIKTOK")),
        "staging_status": {
            "status": staging.processing_status.value if hasattr(staging, "processing_status") else str(getattr(staging, "processing_status", "")),
            "decision": staging.decision.value if hasattr(staging, "decision") else str(getattr(staging, "decision", "")),
            "primaryType": getattr(staging, "primary_type", ""),
            "updatedAt": getattr(staging, "updated_at", ""),
        } if staging else None,
        "crawled_payload": crawled,
    }
