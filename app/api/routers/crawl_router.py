from fastapi import APIRouter, HTTPException

from app.core.models import IngestPayload
from app.crawlers.facebook_crawler import crawl_facebook_profile
from app.crawlers.tiktok_crawler import crawl_tiktok_profile
from app.crawlers.youtube_crawler import crawl_youtube_channel
from app.workers.tasks import process_facebook_profile, process_tiktok_profile

router = APIRouter(prefix="/api/v1/crawl", tags=["Crawl"])


@router.get("/youtube", response_model=IngestPayload)
async def crawl_youtube(handle: str):
    try:
        profile = await crawl_youtube_channel(handle)
        if not profile:
            raise HTTPException(status_code=404, detail="YouTube channel not found")
        return profile
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to crawl YouTube: {e}")


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


@router.post("/tiktok/enqueue")
async def enqueue_tiktok(username: str) -> dict:
    try:
        process_tiktok_profile.delay(username)
        return {
            "status": "success",
            "message": f"Đã đưa {username} vào hàng đợi Celery! Hệ thống sẽ cào và gửi sang Java tự động.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/facebook", response_model=IngestPayload, summary="Cào trực tiếp Fanpage KOC Facebook")
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


@router.post("/facebook/enqueue", summary="Đưa KOC Facebook vào hàng đợi Celery")
async def enqueue_facebook(username: str) -> dict:
    try:
        process_facebook_profile.delay(username)
        return {
            "status": "success",
            "message": f"Đã đưa {username} vào hàng đợi Celery Facebook! Hệ thống sẽ cào và gửi sang Java tự động.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
