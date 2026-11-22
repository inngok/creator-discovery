import asyncio
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.core.post_metrics_models import (
    OPENAPI_EXAMPLE_ERROR_400,
    OPENAPI_EXAMPLE_ERROR_404,
    OPENAPI_EXAMPLE_ERROR_422,
    OPENAPI_EXAMPLE_ERROR_429,
    OPENAPI_EXAMPLE_ERROR_502,
    OPENAPI_EXAMPLE_ERROR_503,
    OPENAPI_EXAMPLE_ERROR_504,
    OPENAPI_EXAMPLE_PARTIAL_MISSING,
    OPENAPI_EXAMPLE_SUCCESS_COMPLETE,
    PostMetricsErrorResponse,
    PostMetricsSuccessResponse,
)

router = APIRouter()


class SinglePostMetricsRequest(BaseModel):
    post_url: Optional[str] = Field(
        default=None,
        alias="postUrl",
        description="Đường link bài đăng cụ thể (TikTok, YouTube, Facebook, Instagram)",
    )

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    def get_url(self) -> str:
        return (self.post_url or "").strip()


@router.post(
    "/post-metrics",
    summary="Cào thông số bài đăng đơn lẻ để theo dõi Campaign (WF-03 Post Metrics)",
    description=(
        "Bóc tách tức thì views, likes, comments, shares, caption, thumbnail, thời gian đăng từ link bài đăng "
        "của chiến dịch (TikTok, YouTube, Facebook, Instagram). Phân biệt số 0 thực tế (AVAILABLE) với dữ liệu thiếu "
        "(null / FETCH_FAILED / NOT_SUPPORTED). Tuân thủ schemaVersion 1.1."
    ),
    response_model=PostMetricsSuccessResponse,
    responses={
        200: {
            "model": PostMetricsSuccessResponse,
            "description": "Bóc tách thành công thông số bài đăng (Schema 1.1)",
            "content": {
                "application/json": {
                    "examples": {
                        "success_complete": {
                            "summary": "TikTok đầy đủ thông số",
                            "value": OPENAPI_EXAMPLE_SUCCESS_COMPLETE,
                        },
                        "partial_missing": {
                            "summary": "Số 0 hợp lệ và một phần metric bị thiếu/không hỗ trợ",
                            "value": OPENAPI_EXAMPLE_PARTIAL_MISSING,
                        },
                    }
                }
            },
        },
        400: {
            "model": PostMetricsErrorResponse,
            "description": "URL không hợp lệ hoặc vi phạm an toàn SSRF",
            "content": {"application/json": {"example": OPENAPI_EXAMPLE_ERROR_400}},
        },
        404: {
            "model": PostMetricsErrorResponse,
            "description": "Bài viết không tồn tại hoặc đã bị xóa/riêng tư",
            "content": {"application/json": {"example": OPENAPI_EXAMPLE_ERROR_404}},
        },
        422: {
            "model": PostMetricsErrorResponse,
            "description": "Nền tảng không được hỗ trợ",
            "content": {"application/json": {"example": OPENAPI_EXAMPLE_ERROR_422}},
        },
        429: {
            "model": PostMetricsErrorResponse,
            "description": "Bị giới hạn tần suất (Rate limit) từ upstream",
            "content": {"application/json": {"example": OPENAPI_EXAMPLE_ERROR_429}},
        },
        502: {
            "model": PostMetricsErrorResponse,
            "description": "Lỗi phân tích cú pháp hoặc không có metric nào khả dụng",
            "content": {"application/json": {"example": OPENAPI_EXAMPLE_ERROR_502}},
        },
        503: {
            "model": PostMetricsErrorResponse,
            "description": "Nguồn upstream không khả dụng hoặc bị chặn",
            "content": {"application/json": {"example": OPENAPI_EXAMPLE_ERROR_503}},
        },
        504: {
            "model": PostMetricsErrorResponse,
            "description": "Timeout khi cào dữ liệu từ nguồn upstream",
            "content": {"application/json": {"example": OPENAPI_EXAMPLE_ERROR_504}},
        },
    },
)
async def track_single_post_metrics(req: SinglePostMetricsRequest):
    """
    Nhận link bài đăng bất kỳ của chiến dịch:
    - Bóc tách tức thì views, likes, comments, shares, caption, thumbnail, thời gian đăng.
    - Giúp Brand theo dõi kết quả campaign tự động mà không cần nhập thủ công.
    """
    from app.services.single_post_tracker_service import single_post_tracker

    url = req.get_url()
    if not url:
        return JSONResponse(
            status_code=400,
            content={
                "schemaVersion": "1.1",
                "status": "error",
                "error": {
                    "code": "INVALID_POST_URL",
                    "message": "URL bài đăng không được để trống",
                    "retryable": False,
                    "retryAfterSeconds": None,
                },
                "trackedAt": None,
            },
        )

    res = await asyncio.to_thread(single_post_tracker.fetch_post_metrics, url)
    status_code = res.pop("_http_status", 200 if res.get("status") == "success" else 400)
    return JSONResponse(status_code=status_code, content=res)
