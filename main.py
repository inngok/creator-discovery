import os
import sys
import asyncio
from dotenv import load_dotenv

load_dotenv()

if sys.platform == "win32" and sys.version_info >= (3, 8):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, HTTPException
from app.core.models import IngestPayload
from app.crawlers.youtube_crawler import crawl_youtube_channel
from app.crawlers.tiktok_crawler import crawl_tiktok_profile
from app.crawlers.instagram_crawler import crawl_instagram_profile

app = FastAPI(
    title="InfluencerMatch Crawler API",
    description="API for crawling and normalizing Creator data from YouTube and TikTok.",
    version="1.0.0"
)

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/v1/crawl/youtube", response_model=IngestPayload)
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

@app.get("/api/v1/crawl/tiktok", response_model=IngestPayload)
async def crawl_tiktok(username: str):
    try:
        profile = await crawl_tiktok_profile(username)
        if not profile:
            raise HTTPException(status_code=404, detail="TikTok profile not found or could not extract data")
        return profile
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to crawl TikTok: {e}")

@app.get("/api/v1/crawl/instagram", response_model=IngestPayload)
async def crawl_instagram(username: str):
    try:
        profile = await crawl_instagram_profile(username)
        if not profile:
            raise HTTPException(status_code=404, detail="Instagram profile not found or could not extract data")
        return profile
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to crawl Instagram: {e}")

@app.post("/api/v1/crawl/tiktok/enqueue")
async def enqueue_tiktok(username: str):
    try:
        from app.workers.tasks import process_tiktok_profile
        process_tiktok_profile.delay(username)
        return {"status": "success", "message": f"Đã đưa {username} vào hàng đợi Celery! Hệ thống sẽ cào và gửi sang Java tự động."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
