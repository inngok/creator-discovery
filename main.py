import asyncio
import os
import sys
from contextlib import asynccontextmanager

# Tắt thanh tiến trình tqdm của HuggingFace và ép UTF-8 để tránh lỗi vỡ font thanh tiến trình (mojibake)
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["TRANSFORMERS_VERBOSITY"] = "error"
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import ai_router, crawl_router, monitor_router, review_router, quality_router


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
    description="API for crawling and normalizing Creator data from YouTube, TikTok, Facebook and Instagram.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "*"
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gắn các routers đã được tách ra
app.include_router(crawl_router.router)
app.include_router(ai_router.router)
app.include_router(review_router.router)
app.include_router(monitor_router.router)
app.include_router(quality_router.router)

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )
