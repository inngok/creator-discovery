import asyncio
import sys
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import ai_router, crawl_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Khởi tạo environment variables
    load_dotenv()

    # Cấu hình event loop cho Windows
    if sys.platform == "win32" and sys.version_info >= (3, 8):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    yield
    # Cleanup resources (if bất kỳ)


app = FastAPI(
    title="InfluencerMatch Crawler API",
    description="API for crawling and normalizing Creator data from YouTube and TikTok.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gắn các routers đã được tách ra
app.include_router(crawl_router.router)
app.include_router(ai_router.router)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
