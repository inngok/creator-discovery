from fastapi import APIRouter, HTTPException

from app.workers.tasks import run_retrain_pipeline

router = APIRouter(prefix="/api/v1/ai", tags=["AI"])


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
