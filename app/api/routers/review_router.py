from datetime import UTC, datetime
from typing import Any, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.ai.rules.rule_config import Decision, ProcessingStatus
from app.core.candidate_staging import staging_repo
from app.core.logger import setup_logger
from app.workers.tasks import (
    process_facebook_profile,
    process_instagram_profile,
    process_tiktok_profile,
)

logger = setup_logger("review_router")

router = APIRouter(prefix="/api/v1/candidates/review", tags=["Candidate Review"])


class ReviewActionRequest(BaseModel):
    action: str  # "APPROVE_KOC", "APPROVE_KOL", "REJECT", "DEFER"
    reviewer: str = "data_manager"
    note: Optional[str] = ""


class CandidateReviewItem(BaseModel):
    id: str
    platform: str
    external_id: str
    username: str
    display_name: str
    profile_url: str
    processing_status: str
    decision: str
    primary_type: str
    reason_codes: list[str]
    matched_signals: list[str]
    evidence_summary: dict[str, Any]
    rule_version: str
    last_checked_at: str
    created_at: str
    updated_at: str


class PendingReviewResponse(BaseModel):
    total: int
    limit: int
    offset: int
    candidates: list[CandidateReviewItem]


@router.get("/pending", response_model=PendingReviewResponse, summary="Danh sách ứng viên chờ Data Manager duyệt")
async def get_pending_review_candidates(
    platform: Optional[str] = Query(None, description="Lọc theo platform (TIKTOK/FACEBOOK/INSTAGRAM)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """
    Lấy danh sách các Creator Candidate bị rơi vào vùng biên (Decision: NEEDS_MANAGER_REVIEW)
    để Data Manager thẩm định thủ công trước khi quyết định cấp tài nguyên cào sâu.
    """
    total = staging_repo.count_by_decision(Decision.NEEDS_MANAGER_REVIEW, platform=platform)
    records = staging_repo.list_by_decision(
        Decision.NEEDS_MANAGER_REVIEW, platform=platform, limit=limit, offset=offset
    )

    items = [
        CandidateReviewItem(
            id=r.id,
            platform=r.platform,
            external_id=r.external_id,
            username=r.username,
            display_name=r.display_name,
            profile_url=r.profile_url,
            processing_status=r.processing_status.value if hasattr(r.processing_status, "value") else str(r.processing_status),
            decision=r.decision.value if hasattr(r.decision, "value") else str(r.decision),
            primary_type=r.primary_type,
            reason_codes=r.reason_codes,
            matched_signals=r.matched_signals,
            evidence_summary=r.evidence_summary,
            rule_version=r.rule_version,
            last_checked_at=r.last_checked_at,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in records
    ]

    return PendingReviewResponse(total=total, limit=limit, offset=offset, candidates=items)


@router.post("/{candidate_id}/action", summary="Data Manager thực hiện phê duyệt / từ chối ứng viên")
async def process_candidate_review_action(candidate_id: str, req: ReviewActionRequest) -> dict:
    """
    Xử lý hành động từ Data Manager:
    - APPROVE_KOC / APPROVE_KOL: Phê duyệt -> Cập nhật MANAGER_APPROVED -> Kích hoạt Deep Crawl tự động.
    - REJECT: Từ chối -> Cập nhật MANAGER_REJECTED -> Kết thúc luồng, ghi log lý do.
    - DEFER: Tạm hoãn -> Giữ nguyên trạng thái để kiểm tra thêm sau.
    """
    record = staging_repo.get_candidate_by_id(candidate_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy candidate với ID '{candidate_id}'")

    action_upper = req.action.upper().strip()
    now_str = datetime.now(UTC).isoformat()
    clean_user = record.username.removeprefix("@").strip()
    platform_upper = record.platform.upper()

    logger.info(f"[REVIEW ACTION] Manager '{req.reviewer}' thực hiện '{action_upper}' cho @{clean_user} ({platform_upper})")

    if action_upper in ["APPROVE_KOC", "APPROVE_KOL"]:
        primary_type = "KOC" if action_upper == "APPROVE_KOC" else "KOL"

        # Cập nhật Staging: MANAGER_APPROVED và DEEP_CRAWL_QUEUED
        staging_repo.update_status_and_decision(
            platform=platform_upper,
            username=clean_user,
            status=ProcessingStatus.DEEP_CRAWL_QUEUED,
            decision=Decision.MANAGER_APPROVED,
            primary_type=primary_type,
            reason_codes=[f"DATA_MANAGER_{action_upper}"],
        )

        # 🚀 Tự động kích hoạt Deep Crawl Task
        if platform_upper == "TIKTOK":
            task = process_tiktok_profile.delay(clean_user)
        elif platform_upper == "FACEBOOK":
            task = process_facebook_profile.delay(clean_user)
        elif platform_upper == "INSTAGRAM":
            task = process_instagram_profile.delay(clean_user)
        else:
            task = None

        task_id = getattr(task, "id", None) if task else None
        logger.info(f"[REVIEW APPROVE] Đã kích hoạt Deep Crawl cho @{clean_user} (Task ID: {task_id})")

        return {
            "status": "success",
            "candidate_id": candidate_id,
            "username": clean_user,
            "action": action_upper,
            "decision": Decision.MANAGER_APPROVED.value,
            "primary_type": primary_type,
            "deep_crawl_enqueued": True,
            "task_id": task_id,
            "reviewed_by": req.reviewer,
            "reviewed_at": now_str,
        }

    elif action_upper == "REJECT":
        staging_repo.update_status_and_decision(
            platform=platform_upper,
            username=clean_user,
            status=ProcessingStatus.FAILED,
            decision=Decision.MANAGER_REJECTED,
            reason_codes=["DATA_MANAGER_REJECTED", req.note or "REJECTED_BY_MANAGER"],
        )
        return {
            "status": "success",
            "candidate_id": candidate_id,
            "username": clean_user,
            "action": action_upper,
            "decision": Decision.MANAGER_REJECTED.value,
            "deep_crawl_enqueued": False,
            "reviewed_by": req.reviewer,
            "reviewed_at": now_str,
        }

    elif action_upper == "DEFER":
        return {
            "status": "success",
            "candidate_id": candidate_id,
            "username": clean_user,
            "action": "DEFER",
            "decision": record.decision.value if hasattr(record.decision, "value") else str(record.decision),
            "message": "Đã ghi nhận tạm hoãn đánh giá ứng viên.",
            "reviewed_by": req.reviewer,
            "reviewed_at": now_str,
        }

    else:
        raise HTTPException(
            status_code=400,
            detail=f"Hành động '{req.action}' không hợp lệ. Chỉ chấp nhận APPROVE_KOC, APPROVE_KOL, REJECT, DEFER.",
        )
