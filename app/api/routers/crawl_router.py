from fastapi import APIRouter, HTTPException

from app.core.models import IngestPayload
from app.crawlers.facebook_crawler import crawl_facebook_profile
from app.crawlers.instagram_crawler import crawl_instagram_profile
from app.crawlers.tiktok_crawler import crawl_tiktok_profile
from app.workers.tasks import (
    preflight_candidate_task,
    process_facebook_profile,
    process_instagram_profile,
    process_tiktok_profile,
)

router = APIRouter(prefix="/api/v1/crawl", tags=["Crawl"])


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
    try:
        clean_user = username.removeprefix("@").strip()
        if force_deep_crawl:
            task = process_tiktok_profile.delay(clean_user, is_refresh=True)
            return {
                "status": "success",
                "mode": "FORCE_DEEP_CRAWL",
                "task_id": task.id,
                "message": f"Đã ép buộc đưa @{clean_user} vào hàng đợi Deep Crawl (is_refresh=True).",
            }
        task = preflight_candidate_task.delay("TIKTOK", clean_user)
        return {
            "status": "success",
            "mode": "PREFLIGHT_GATE",
            "task_id": task.id,
            "message": f"Đã đưa @{clean_user} vào Preflight Gate để phân loại trước khi cào sâu.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
    try:
        clean_user = username.removeprefix("@").strip()
        if force_deep_crawl:
            task = process_facebook_profile.delay(clean_user, is_refresh=True)
            return {
                "status": "success",
                "mode": "FORCE_DEEP_CRAWL",
                "task_id": task.id,
                "message": f"Đã ép buộc đưa {clean_user} vào hàng đợi Deep Crawl FB (is_refresh=True).",
            }
        task = preflight_candidate_task.delay("FACEBOOK", clean_user)
        return {
            "status": "success",
            "mode": "PREFLIGHT_GATE",
            "task_id": task.id,
            "message": f"Đã đưa {clean_user} vào Preflight Gate FB để phân loại trước khi cào sâu.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
    try:
        clean_user = username.removeprefix("@").strip()
        if force_deep_crawl:
            task = process_instagram_profile.delay(clean_user, is_refresh=True)
            return {
                "status": "success",
                "mode": "FORCE_DEEP_CRAWL",
                "task_id": task.id,
                "message": f"Đã ép buộc đưa @{clean_user} vào hàng đợi Deep Crawl IG (is_refresh=True).",
            }
        task = preflight_candidate_task.delay("INSTAGRAM", clean_user)
        return {
            "status": "success",
            "mode": "PREFLIGHT_GATE",
            "task_id": task.id,
            "message": f"Đã đưa @{clean_user} vào Preflight Gate IG để phân loại trước khi cào sâu.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


from pydantic import BaseModel
from typing import Optional
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
