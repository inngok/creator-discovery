from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.ai.analyzer import process_ai_enrichment
from app.ai.services.embedding_service import embedding_service
from app.ai.services.explanation_service import ExplanationInput, generate_explanation
from app.core.models import IngestPayload
from app.workers.tasks import run_retrain_pipeline

router = APIRouter(prefix="/api/v1/ai", tags=["AI"])


class EmbedRequest(BaseModel):
    text: str


class ExplainRequest(BaseModel):
    creatorName: str = Field(default="", description="Tên hiển thị của creator")
    followers: int = Field(..., ge=0)
    engagementRate: float = Field(default=0.0, ge=0.0)
    niche: str = Field(default="general")
    category: str = Field(default="Lifestyle")
    creatorType: str = Field(default="UNKNOWN")
    avgViews: float = Field(default=0.0, ge=0.0)
    avgLikes: float = Field(default=0.0, ge=0.0)
    similarityScore: float = Field(default=0.0, ge=0.0, le=1.0, description="Score từ vector search [0, 1]")
    campaignBrief: str = Field(default="", description="Mô tả campaign brief")
    contentSummary: str = Field(default="")
    topTags: list[str] = Field(default_factory=list)


@router.post("/embed")
async def embed_search_query(req: EmbedRequest):
    """
    Sinh vector 768 chiều (BKAI Bi-Encoder) phục vụ Semantic Search từ Java Backend.
    """
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="Văn bản không được để trống")
    try:
        vector = embedding_service.get_search_embedding(req.text.strip())
        return {
            "status": "success",
            "model": "bkai-foundation-models/vietnamese-bi-encoder",
            "dimensions": len(vector),
            "vector": vector,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi sinh vector: {e}")


@router.post("/explain")
async def explain_creator_recommendation(req: ExplainRequest):
    """
    Sinh câu giải thích AI tại sao Creator được đề xuất cho Campaign.
    Trả về: summary, danh sách reasons, và score_breakdown theo từng tiêu chí.
    """
    try:
        data = ExplanationInput(
            creator_name=req.creatorName,
            followers=req.followers,
            engagement_rate=req.engagementRate,
            niche=req.niche,
            category=req.category,
            creator_type=req.creatorType,
            avg_views=req.avgViews,
            avg_likes=req.avgLikes,
            similarity_score=req.similarityScore,
            campaign_brief=req.campaignBrief,
            content_summary=req.contentSummary,
            top_tags=req.topTags,
        )
        result = generate_explanation(data)
        return {
            "status": "success",
            **result.to_dict(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi sinh giải thích: {e}")


@router.post("/retrain")
async def trigger_ai_retrain():
    """API để chạy Manual kịch bản Data Flywheel (Cập nhật từ khóa mới cho AI)."""
    try:
        # Đưa vào Celery Worker chạy ngầm để không làm treo API request
        run_retrain_pipeline.delay()
        return {
            "status": "success",
            "message": "Đã đưa luồng Retrain AI vào hàng đợi Celery! Bạn có thể xem log của Worker.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/enrich",
    response_model=IngestPayload,
    summary="Phân tích AI độc lập cho dữ liệu Creator đã cào",
)
async def enrich_creator_ai(payload: IngestPayload):
    """
    Nhận dữ liệu thô (CreatorInfo + Posts) từ bất kỳ platform nào (Instagram, TikTok, FB,...)
    và chạy phân loại ngành nghề (Category), Niche, Creator Type và Sentiment qua AI.
    """
    try:
        enrichment = process_ai_enrichment(payload.creator, payload.recentPosts)
        payload.aiEnrichment = enrichment
        return payload
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi phân tích AI: {e}")

