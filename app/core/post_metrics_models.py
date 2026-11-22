from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class MetricStatusEnum(str, Enum):
    AVAILABLE = "AVAILABLE"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    FETCH_FAILED = "FETCH_FAILED"


class MetricErrorInfo(BaseModel):
    code: str = Field(..., description="Mã lỗi metric cụ thể (ví dụ: COUNTER_UNAVAILABLE)")
    retryable: bool = Field(default=False, description="Có thể thử lại hay không")


class PostAuthor(BaseModel):
    externalId: Optional[str] = Field(None, description="Author ID ổn định từ nguồn nếu có, không biết thì null")
    username: Optional[str] = Field(None, description="Username hoặc handle của tác giả")
    displayName: Optional[str] = Field(None, description="Tên hiển thị của tác giả")


class PostMetrics(BaseModel):
    views: Optional[int] = Field(None, ge=0, le=9007199254740991, description="Lượt xem (số nguyên không âm hoặc null)")
    likes: Optional[int] = Field(None, ge=0, le=9007199254740991, description="Lượt thích (số nguyên không âm hoặc null)")
    comments: Optional[int] = Field(None, ge=0, le=9007199254740991, description="Lượt bình luận (số nguyên không âm hoặc null)")
    shares: Optional[int] = Field(None, ge=0, le=9007199254740991, description="Lượt chia sẻ (số nguyên không âm hoặc null)")
    engagementRate: Optional[float] = Field(None, ge=0.0, description="Tỷ lệ tương tác % (null nếu thiếu dữ liệu thành phần)")


class MetricStatusMap(BaseModel):
    views: MetricStatusEnum = Field(default=MetricStatusEnum.NOT_AVAILABLE, description="Trạng thái của metric views")
    likes: MetricStatusEnum = Field(default=MetricStatusEnum.NOT_AVAILABLE, description="Trạng thái của metric likes")
    comments: MetricStatusEnum = Field(default=MetricStatusEnum.NOT_AVAILABLE, description="Trạng thái của metric comments")
    shares: MetricStatusEnum = Field(default=MetricStatusEnum.NOT_AVAILABLE, description="Trạng thái của metric shares")


class PostContent(BaseModel):
    caption: Optional[str] = Field(None, description="Nội dung caption hoặc tiêu đề bài viết")
    thumbnailUrl: Optional[str] = Field(None, description="URL ảnh thumbnail bài đăng")
    postedAt: Optional[str] = Field(None, description="Thời điểm đăng bài (UTC ISO-8601)")


class CollectionMetadata(BaseModel):
    source: str = Field("PUBLIC_PAGE", description="Nguồn thu thập")
    method: str = Field("SSR", description="Phương thức bóc tách: SSR, OEMBED_SSR, PLAYWRIGHT")
    cacheHit: bool = Field(False, description="Đánh dấu dữ liệu được lấy từ cache")
    scope: str = Field("LIFETIME_AT_COLLECTION", description="Phạm vi số liệu")
    engagementRateFormula: Optional[str] = Field(None, description="Công thức tính ER")
    engagementRateUnit: str = Field("PERCENT", description="Đơn vị tính ER")


class PostMetricsSuccessResponse(BaseModel):
    schemaVersion: Literal["1.1"] = "1.1"
    status: Literal["success"] = "success"
    platform: str = Field(..., description="Nền tảng mạng xã hội: TIKTOK, YOUTUBE, FACEBOOK, INSTAGRAM")
    postUrl: str = Field(..., description="URL bài đăng đầu vào được giữ nguyên")
    canonicalPostUrl: str = Field(..., description="URL bài đăng chuẩn hóa đã loại bỏ tracking params")
    postId: str = Field(..., description="ID bài đăng định danh thật từ nguồn")
    author: PostAuthor = Field(default_factory=PostAuthor)
    metrics: PostMetrics = Field(default_factory=PostMetrics)
    metricStatus: MetricStatusMap = Field(default_factory=MetricStatusMap)
    metricErrors: Dict[str, MetricErrorInfo] = Field(default_factory=dict)
    content: PostContent = Field(default_factory=PostContent)
    trackedAt: str = Field(..., description="Thời điểm lấy dữ liệu từ nguồn (UTC ISO-8601)")
    servedAt: str = Field(..., description="Thời điểm trả response (UTC ISO-8601)")
    collection: CollectionMetadata = Field(default_factory=CollectionMetadata)
    warnings: List[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class ErrorDetail(BaseModel):
    code: str = Field(..., description="Mã lỗi hệ thống: INVALID_POST_URL, UNSUPPORTED_PLATFORM, POST_NOT_FOUND, POST_PRIVATE_OR_UNAVAILABLE, RATE_LIMITED, UPSTREAM_TIMEOUT, UPSTREAM_UNAVAILABLE, PARSE_FAILED, NO_METRICS_AVAILABLE")
    message: str = Field(..., description="Thông điệp mô tả lỗi chi tiết")
    retryable: bool = Field(default=False, description="Cho biết client có nên thử lại hay không")
    retryAfterSeconds: Optional[int] = Field(None, description="Thời gian chờ khuyến nghị trước khi thử lại (giây)")


class PostMetricsErrorResponse(BaseModel):
    schemaVersion: Literal["1.1"] = "1.1"
    status: Literal["error"] = "error"
    error: ErrorDetail
    trackedAt: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


# ============================================================================
# OPENAPI EXAMPLES (WF-03 Specification Section 1 & Section 3)
# ============================================================================

OPENAPI_EXAMPLE_SUCCESS_COMPLETE = {
    "schemaVersion": "1.1",
    "status": "success",
    "platform": "TIKTOK",
    "postUrl": "https://www.tiktok.com/@schannelvn/video/74382910291?tracking=summer_campaign",
    "canonicalPostUrl": "https://www.tiktok.com/@schannelvn/video/74382910291",
    "postId": "74382910291",
    "author": {
        "externalId": "6631682124809076737",
        "username": "schannelvn",
        "displayName": "SChannel Official",
    },
    "metrics": {
        "views": 150000,
        "likes": 12500,
        "comments": 450,
        "shares": 80,
        "engagementRate": 8.69,
    },
    "metricStatus": {
        "views": "AVAILABLE",
        "likes": "AVAILABLE",
        "comments": "AVAILABLE",
        "shares": "AVAILABLE",
    },
    "metricErrors": {},
    "content": {
        "caption": "Trải nghiệm tính năng mới trên Galaxy S26 Ultra",
        "thumbnailUrl": "https://p16-sign.tiktokcdn.com/tos-alisg-p-0037/thumb.jpeg",
        "postedAt": "2026-11-20T05:23:23Z",
    },
    "trackedAt": "2026-11-22T01:00:00Z",
    "servedAt": "2026-11-22T01:02:00Z",
    "collection": {
        "source": "PUBLIC_PAGE",
        "method": "SSR",
        "cacheHit": False,
        "scope": "LIFETIME_AT_COLLECTION",
        "engagementRateFormula": "(likes + comments + shares) / views * 100",
        "engagementRateUnit": "PERCENT",
    },
    "warnings": [],
}

OPENAPI_EXAMPLE_PARTIAL_MISSING = {
    "schemaVersion": "1.1",
    "status": "success",
    "platform": "TIKTOK",
    "postUrl": "https://www.tiktok.com/@example/video/123?tracking=example",
    "canonicalPostUrl": "https://www.tiktok.com/@example/video/123",
    "postId": "123",
    "author": {
        "externalId": None,
        "username": "example",
        "displayName": "Example",
    },
    "metrics": {
        "views": 1000,
        "likes": 0,
        "comments": None,
        "shares": None,
        "engagementRate": None,
    },
    "metricStatus": {
        "views": "AVAILABLE",
        "likes": "AVAILABLE",
        "comments": "FETCH_FAILED",
        "shares": "NOT_SUPPORTED",
    },
    "metricErrors": {
        "comments": {"code": "COUNTER_UNAVAILABLE", "retryable": True}
    },
    "content": {
        "caption": "Nội dung bài đăng",
        "thumbnailUrl": None,
        "postedAt": "2026-11-20T05:23:23Z",
    },
    "trackedAt": "2026-11-22T01:00:00Z",
    "servedAt": "2026-11-22T01:02:00Z",
    "collection": {
        "source": "PUBLIC_PAGE",
        "method": "SSR",
        "cacheHit": True,
        "scope": "LIFETIME_AT_COLLECTION",
        "engagementRateFormula": None,
        "engagementRateUnit": "PERCENT",
    },
    "warnings": ["COMMENTS_UNAVAILABLE"],
}

OPENAPI_EXAMPLE_ERROR_400 = {
    "schemaVersion": "1.1",
    "status": "error",
    "error": {
        "code": "INVALID_POST_URL",
        "message": "URL bài đăng không hợp lệ hoặc chứa thông tin định danh không an toàn",
        "retryable": False,
        "retryAfterSeconds": None,
    },
    "trackedAt": None,
}

OPENAPI_EXAMPLE_ERROR_404 = {
    "schemaVersion": "1.1",
    "status": "error",
    "error": {
        "code": "POST_NOT_FOUND",
        "message": "Bài đăng không tồn tại hoặc đã bị xóa khỏi nền tảng",
        "retryable": False,
        "retryAfterSeconds": None,
    },
    "trackedAt": None,
}

OPENAPI_EXAMPLE_ERROR_422 = {
    "schemaVersion": "1.1",
    "status": "error",
    "error": {
        "code": "UNSUPPORTED_PLATFORM",
        "message": "Nền tảng không được hỗ trợ. Chỉ chấp nhận bài đăng từ TikTok, YouTube, Facebook, Instagram.",
        "retryable": False,
        "retryAfterSeconds": None,
    },
    "trackedAt": None,
}

OPENAPI_EXAMPLE_ERROR_429 = {
    "schemaVersion": "1.1",
    "status": "error",
    "error": {
        "code": "RATE_LIMITED",
        "message": "Tần suất cào dữ liệu từ nền tảng bị giới hạn",
        "retryable": True,
        "retryAfterSeconds": 30,
    },
    "trackedAt": None,
}

OPENAPI_EXAMPLE_ERROR_502 = {
    "schemaVersion": "1.1",
    "status": "error",
    "error": {
        "code": "NO_METRICS_AVAILABLE",
        "message": "Không có số liệu metric nào khả dụng từ bài đăng",
        "retryable": False,
        "retryAfterSeconds": None,
    },
    "trackedAt": None,
}

OPENAPI_EXAMPLE_ERROR_503 = {
    "schemaVersion": "1.1",
    "status": "error",
    "error": {
        "code": "UPSTREAM_UNAVAILABLE",
        "message": "Không lấy được dữ liệu bài đăng do nguồn upstream không phản hồi",
        "retryable": True,
        "retryAfterSeconds": 60,
    },
    "trackedAt": None,
}

OPENAPI_EXAMPLE_ERROR_504 = {
    "schemaVersion": "1.1",
    "status": "error",
    "error": {
        "code": "UPSTREAM_TIMEOUT",
        "message": "Quá thời gian chờ phản hồi từ nguồn upstream (vượt quá 15 giây)",
        "retryable": True,
        "retryAfterSeconds": 10,
    },
    "trackedAt": None,
}
