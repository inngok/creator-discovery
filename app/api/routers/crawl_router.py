import asyncio
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.models import IngestPayload
from app.crawlers.facebook_crawler import crawl_facebook_profile
from app.crawlers.instagram_crawler import crawl_instagram_profile
from app.crawlers.tiktok_crawler import crawl_tiktok_profile
from app.services.preflight_service import PreflightService
from app.workers.tasks import (
    preflight_candidate_task,
    process_facebook_profile,
    process_instagram_profile,
    process_tiktok_profile,
)

router = APIRouter(prefix="/api/v1/crawl", tags=["Crawl"])


DEEP_CRAWL_TASKS = {
    "TIKTOK": process_tiktok_profile,
    "FACEBOOK": process_facebook_profile,
    "INSTAGRAM": process_instagram_profile,
}


def _dispatch_enqueue(platform: str, username: str, force_deep_crawl: bool = False) -> dict:
    plat = (platform or "TIKTOK").upper()
    clean_user = username.removeprefix("@").strip()
    if not clean_user:
        raise HTTPException(status_code=400, detail="Username không được để trống")

    task_fn = DEEP_CRAWL_TASKS.get(plat)
    if not task_fn:
        supported = ", ".join(DEEP_CRAWL_TASKS)
        raise HTTPException(
            status_code=400,
            detail=f"Nền tảng {plat} không được hỗ trợ. Vui lòng chọn: {supported}",
        )

    if force_deep_crawl:
        task = task_fn.delay(clean_user, is_refresh=True)
        return {
            "status": "success",
            "platform": plat,
            "username": clean_user,
            "mode": "FORCE_DEEP_CRAWL",
            "task_id": task.id,
            "message": f"Đã ép buộc đưa {clean_user} ({plat}) vào hàng đợi Deep Crawl (is_refresh=True).",
        }

    task = preflight_candidate_task.delay(plat, clean_user)
    return {
        "status": "success",
        "platform": plat,
        "username": clean_user,
        "mode": "PREFLIGHT_GATE",
        "task_id": task.id,
        "message": f"Đã đưa {clean_user} ({plat}) vào Preflight Gate để phân loại trước khi cào sâu.",
    }


class EnqueueCandidateRequest(BaseModel):
    platform: str = Field(default="TIKTOK", description="Nền tảng: TIKTOK, FACEBOOK, INSTAGRAM")
    username: str = Field(..., description="Username hoặc handle của Creator")
    force_deep_crawl: bool = Field(default=False, description="Bỏ qua Preflight Gate, cào sâu ngay lập tức")


@router.post("/enqueue", summary="Đưa Creator (TikTok / Facebook / Instagram) vào hàng đợi Celery Preflight Gate")
async def enqueue_candidate(req: EnqueueCandidateRequest) -> dict:
    return _dispatch_enqueue(req.platform, req.username, req.force_deep_crawl)


@router.get("/tiktok", response_model=IngestPayload)
async def crawl_tiktok(username: str):
    try:
        profile = await crawl_tiktok_profile(username)
        if not profile:
            raise HTTPException(
                status_code=404,
                detail="TikTok profile not found or could not extract data",
            )
        return profile
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to crawl TikTok: {e}")


@router.post("/tiktok/enqueue", summary="Đưa Creator TikTok vào hàng đợi (Preflight Gate mặc định)")
async def enqueue_tiktok(username: str, force_deep_crawl: bool = False) -> dict:
    return _dispatch_enqueue("TIKTOK", username, force_deep_crawl)


@router.get("/facebook", response_model=IngestPayload, summary="Cào trực tiếp Facebook")
async def crawl_facebook(username: str):
    try:
        profile = await crawl_facebook_profile(username)
        if not profile:
            raise HTTPException(
                status_code=404,
                detail="Facebook profile not found or could not extract data",
            )
        return profile
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to crawl Facebook: {e}")


@router.post("/facebook/enqueue", summary="Đưa Creator Facebook vào hàng đợi (Preflight Gate mặc định)")
async def enqueue_facebook(username: str, force_deep_crawl: bool = False) -> dict:
    return _dispatch_enqueue("FACEBOOK", username, force_deep_crawl)


@router.get("/instagram", response_model=IngestPayload, summary="Cào trực tiếp Instagram")
async def crawl_instagram(username: str):
    try:
        profile = await crawl_instagram_profile(username)
        if not profile:
            raise HTTPException(
                status_code=404,
                detail="Instagram profile not found or could not extract data",
            )
        return profile
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to crawl Instagram: {e}")


@router.post("/instagram/enqueue", summary="Đưa Creator Instagram vào hàng đợi (Preflight Gate mặc định)")
async def enqueue_instagram(username: str, force_deep_crawl: bool = False) -> dict:
    return _dispatch_enqueue("INSTAGRAM", username, force_deep_crawl)


from app.services.shortlist_crawl_service import trigger_shortlist_crawl_background


class ShortlistTriggerRequest(BaseModel):
    platform: str = "TIKTOK"
    username: str
    creatorId: Optional[str] = None
    campaignId: Optional[str] = None


@router.post("/shortlist-trigger", summary="Kích hoạt cào dữ liệu khi Creator được thêm vào Shortlist")
async def trigger_shortlist_crawl(request: ShortlistTriggerRequest) -> dict:
    try:
        clean_user = request.username.removeprefix("@").strip()
        trigger_shortlist_crawl_background(
            platform=request.platform,
            username=clean_user,
            creator_id=request.creatorId,
        )
        return {
            "status": "QUEUED",
            "message": f"Đã tiếp nhận yêu cầu cào dữ liệu cho @{clean_user} ({request.platform}) khi vào Shortlist.",
            "platform": request.platform.upper(),
            "username": clean_user,
            "creatorId": request.creatorId,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trigger-weekly-discovery", summary="Kích hoạt thủ công luồng cào tự động tìm KOC mới (Weekly Discovery)")
async def trigger_weekly_discovery(max_new_kocs: int = 30) -> dict:
    """Cho phép kích hoạt ngay luồng cào tự động KOC mới (thay vì đợi đến 3h sáng Thứ Hai)."""
    try:
        from app.workers.tasks import weekly_discover_new_creators_task

        task = weekly_discover_new_creators_task.delay(max_new_kocs=max_new_kocs)
        return {
            "status": "QUEUED",
            "message": f"Đã kích hoạt luồng cào KOC mới tự động vào Celery! Task ID: {task.id}",
            "taskId": task.id,
            "maxNewKocs": max_new_kocs,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trigger-db-refresh", summary="Kích hoạt thủ công luồng cào làm mới (refresh) KOC cũ trong DB")
async def trigger_db_refresh(limit: int = 25) -> dict:
    """Cho phép kích hoạt ngay luồng cào làm mới KOC cũ trong DB (thay vì đợi đến 4h sáng Thứ Năm)."""
    try:
        from app.workers.tasks import auto_refresh_creators_in_db_task

        task = auto_refresh_creators_in_db_task.delay(limit=limit)
        return {
            "status": "QUEUED",
            "message": f"Đã kích hoạt luồng cào làm mới KOC cũ trong DB vào Celery! Task ID: {task.id}",
            "taskId": task.id,
            "limit": limit,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




class ResolveAuthorRequest(BaseModel):
    username: str = Field(..., description="Username hiện tại của KOC trên TikTok (ví dụ: domixi hoặc @domixi)")
    expected_external_id: Optional[str] = Field(
        default=None,
        description="External ID (Author ID số cố định) lưu trong DB để đối soát"
    )
    auto_update_staging: bool = Field(
        default=True,
        description="Tự động cập nhật username mới vào DB Staging nếu trùng khớp Author ID"
    )


class PreflightResolveAuthorRequest(BaseModel):
    platform: str = Field(default="TIKTOK", description="Nền tảng (mặc định: TIKTOK)")
    username: str = Field(..., description="Username hiện tại của KOC")
    expected_external_id: Optional[str] = Field(
        default=None,
        description="External ID (Author ID số cố định) lưu trong DB để đối soát"
    )
    auto_update_staging: bool = Field(
        default=True,
        description="Tự động cập nhật username mới vào DB Staging nếu trùng khớp Author ID"
    )


@router.post(
    "/tiktok/resolve-author",
    summary="Bóc tách Author ID TikTok qua SSR và đối soát KOC đổi tên",
    description="Sử dụng Lightweight Preflight SSR để lấy Author ID (dãy số cố định) cho username TikTok mới (ví dụ: domixi), phục vụ đối soát với external_id trong DB khi KOC đổi tên trước khi đăng nhập TikTok OAuth.",
)
async def resolve_tiktok_author(req: ResolveAuthorRequest) -> dict:
    clean_user = req.username.removeprefix("@").strip()
    if not clean_user:
        raise HTTPException(status_code=400, detail="Username không được để trống")

    result = await asyncio.to_thread(
        PreflightService.resolve_author_id,
        platform="TIKTOK",
        username=clean_user,
        expected_external_id=req.expected_external_id,
        auto_update_staging=req.auto_update_staging,
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result.get("message", "Không thể lấy thông tin tác giả"))
    return result


@router.get(
    "/tiktok/resolve-author",
    summary="Tra cứu nhanh Author ID TikTok qua query params (SSR Preflight)",
)
async def get_tiktok_author(
    username: str,
    expected_external_id: Optional[str] = None,
    auto_update_staging: bool = True,
) -> dict:
    clean_user = username.removeprefix("@").strip()
    if not clean_user:
        raise HTTPException(status_code=400, detail="Username không được để trống")

    result = await asyncio.to_thread(
        PreflightService.resolve_author_id,
        platform="TIKTOK",
        username=clean_user,
        expected_external_id=expected_external_id,
        auto_update_staging=auto_update_staging,
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result.get("message", "Không thể lấy thông tin tác giả"))
    return result


@router.post(
    "/preflight/resolve-author",
    summary="Đối soát Author ID tổng quát qua Preflight SSR Gate",
)
async def resolve_preflight_author(req: PreflightResolveAuthorRequest) -> dict:
    clean_user = req.username.removeprefix("@").strip()
    if not clean_user:
        raise HTTPException(status_code=400, detail="Username không được để trống")

    result = await asyncio.to_thread(
        PreflightService.resolve_author_id,
        platform=req.platform,
        username=clean_user,
        expected_external_id=req.expected_external_id,
        auto_update_staging=req.auto_update_staging,
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result.get("message", "Không thể lấy thông tin tác giả"))
    return result
