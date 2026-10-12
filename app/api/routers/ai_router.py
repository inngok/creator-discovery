from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.ai.services.embedding_service import embedding_service
from app.workers.tasks import run_retrain_pipeline

router = APIRouter(prefix="/api/v1/ai", tags=["AI"])


class EmbedRequest(BaseModel):
    text: str


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
