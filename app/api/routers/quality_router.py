import asyncio
import os
import json
import uuid
import time
from datetime import UTC, datetime
from typing import Any, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.candidate_staging import staging_repo
from app.ai.rules.rule_config import Decision, ProcessingStatus
from app.services.crawled_result_service import list_crawled_results, get_crawled_result_by_username
from app.services.live_crawl_service import crawl_single_koc_live
from app.services.audit_service import audit_service
from app.core.logger import setup_logger
from app.clients.java_client import java_client
from app.mappers.java_mapper import JavaMapper
from app.core.models import IngestPayload

logger = setup_logger("quality_router")

router = APIRouter(prefix="/api/v1/quality", tags=["Data Quality & Identity Resolution"])

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DATA_DIR = os.path.join(ROOT_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
CONFLICTS_FILE_PATH = os.path.join(DATA_DIR, "identity_conflicts.json")


class StandardizeRequest(BaseModel):
    niche: str = Field(..., description="Ngành hàng chuẩn hóa")
    primaryType: Optional[str] = "KOC"
    note: Optional[str] = ""
    reviewer: Optional[str] = "data_manager"


class ConflictResolveRequest(BaseModel):
    action: str = Field(..., description="'MERGE' hoặc 'SEPARATE'")
    masterProfileId: Optional[str] = ""
    note: Optional[str] = ""


class BatchReclassifyRequest(BaseModel):
    limit: int = Field(20, ge=1, le=100, description="Số lượng Creator kiểm duyệt trong đợt này (1 - 100)")
    offset: int = Field(0, ge=0, description="Offset phân trang")
    platform: Optional[str] = Field(None, description="Lọc theo nền tảng: TIKTOK, YOUTUBE, FACEBOOK, INSTAGRAM")
    category: Optional[str] = Field(None, description="Lọc theo ngành hàng (vd: Beauty, Lifestyle, Fashion...)")
    dry_run: bool = Field(False, description="Nếu True: chỉ đánh giá và thống kê, không ghi đè DB / gửi Java Backend")
    auto_quarantine: bool = Field(True, description="Nếu True: tự động cô lập tài khoản Shop/Rác vào Staging")


# Lưu trạng thái xử lý conflicts vào file JSON bền vững
def _load_conflicts_state() -> list[dict[str, Any]]:
    if os.path.exists(CONFLICTS_FILE_PATH):
        try:
            with open(CONFLICTS_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"Không thể đọc identity_conflicts.json: {e}")
    return []


def _save_conflicts_state(conflicts: list[dict[str, Any]]):
    try:
        with open(CONFLICTS_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(conflicts, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Không thể lưu identity_conflicts.json: {e}")


@router.get("/overview", summary="Tổng quan số liệu kiểm định chất lượng")
async def get_quality_overview() -> dict[str, Any]:
    """
    Trả về số lượng hồ sơ cần kiểm duyệt thực tế từ database staging (100% dữ liệu thực, không mock).
    """
    stats = staging_repo.get_summary_stats()
    by_decision = stats.get("by_decision", {})
    pending_count = (
        by_decision.get("NEEDS_MANAGER_REVIEW", 0)
        + by_decision.get("SKIP_INSUFFICIENT", 0)
    )

    approved_count = by_decision.get("MANAGER_APPROVED", 0) + by_decision.get("AUTO_ACCEPT_KOC", 0)
    total = sum(by_decision.values())
    accuracy_rate = round((approved_count / total * 100), 1) if total > 0 else 0.0

    return {
        "pendingCount": pending_count,
        "accuracyRate": accuracy_rate,
        "standardizedTodayCount": approved_count,
    }


@router.get("/pending", summary="Danh sách hồ sơ KOC cần chuẩn hóa ngách & kiểm định")
async def get_pending_quality_items(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """
    Lấy danh sách các KOC thực tế từ dữ liệu cào và Staging DB để chuẩn hóa ngách.
    """
    results: list[dict[str, Any]] = []
    seen_users: set[str] = set()

    # 1. Lấy từ crawled_results.jsonl
    crawled_records, _ = list_crawled_results(limit=limit, offset=offset)
    for r in crawled_records:
        uname = (r.get("username") or "").strip().lower()
        if not uname or uname in seen_users:
            continue

        r_plat = (r.get("platform") or "TIKTOK").upper()
        # Bỏ qua nếu hồ sơ này đã được duyệt hoặc lưu vào hệ thống
        stg = staging_repo.get_candidate(r_plat, uname)
        if stg and (stg.processing_status == ProcessingStatus.INGESTED or stg.decision == Decision.MANAGER_APPROVED):
            continue

        seen_users.add(uname)

        ai = r.get("aiEnrichment") or {}
        niche = ai.get("niche") or ai.get("category") or "Sản phẩm tiêu dùng"
        recent_posts = r.get("recentPosts") or []
        summary = ""
        if recent_posts and isinstance(recent_posts, list):
            first_post = recent_posts[0]
            if isinstance(first_post, dict):
                summary = first_post.get("caption") or ""
        if not summary:
            summary = r.get("bio") or "Nội dung bài đăng và video đang được chuẩn hóa qua hệ thống AI."

        # Tính độ tin cậy AI dựa trên mức độ hoàn thiện thông tin
        confidence = 0.58
        if len(recent_posts) >= 3:
            confidence = 0.72
        elif r.get("followers", 0) > 50000:
            confidence = 0.65

        results.append({
            "metricId": f"metric-{uname}",
            "creatorId": f"cr-{uname}",
            "platform": (r.get("platform") or "tiktok").lower(),
            "userName": uname,
            "displayName": r.get("displayName") or uname,
            "avatarUrl": r.get("avatarUrl") or f"https://api.dicebear.com/7.x/avataaars/svg?seed={uname}",
            "profileUrl": r.get("profileUrl") or f"https://www.tiktok.com/@{uname}",
            "niche": niche,
            "contentSummary": summary,
            "dataConfidence": round(confidence, 2),
            "collectedAt": r.get("collectedAt") or r.get("observedAt") or datetime.now(UTC).isoformat(),
        })

    # 2. Bổ sung từ Staging DB nếu còn chỗ
    staging_records, _ = staging_repo.list_candidates(limit=limit, offset=offset)
    for s in staging_records:
        uname = (s.username or "").strip().lower()
        if not uname or uname in seen_users:
            continue
        # Bỏ qua nếu đã duyệt (INGESTED / MANAGER_APPROVED)
        if s.processing_status == ProcessingStatus.INGESTED or s.decision == Decision.MANAGER_APPROVED:
            continue
        seen_users.add(uname)

        evidence = s.evidence_summary or {}
        niche = s.primary_type if s.primary_type and s.primary_type != "UNKNOWN" else "Đời sống & Review"

        confidence = 0.62 if s.decision == Decision.AUTO_ACCEPT_KOC else 0.54

        results.append({
            "metricId": f"metric-{s.id[:8]}",
            "creatorId": s.id,
            "platform": s.platform.lower(),
            "userName": uname,
            "displayName": s.display_name or uname,
            "avatarUrl": f"https://api.dicebear.com/7.x/avataaars/svg?seed={uname}",
            "profileUrl": s.profile_url or f"https://www.{s.platform.lower()}.com/@{uname}",
            "niche": niche,
            "contentSummary": f"Hồ sơ bóc tách từ {s.platform}. Nhận diện: {', '.join(s.matched_signals[:2]) or 'KOC tiềm năng'}.",
            "dataConfidence": round(confidence, 2),
            "collectedAt": s.updated_at or datetime.now(UTC).isoformat(),
        })

    return {
        "total": len(results),
        "items": results[:limit],
    }


@router.post("/{candidate_id}/standardize", summary="Chuẩn hóa ngách ngành hàng & duyệt KOC")
async def standardize_candidate(candidate_id: str, req: StandardizeRequest) -> dict[str, Any]:
    """
    Lưu ngành hàng đã chuẩn hóa và cập nhật trạng thái đã kiểm định.
    Chuyển trực tiếp hồ sơ sang bảng Creator chính qua Java Ingest API.
    """
    clean_id = candidate_id.removeprefix("metric-").removeprefix("cr-")
    now_str = datetime.now(UTC).isoformat()

    logger.info(f"[STANDARDIZE] Chuẩn hóa ngách '{req.niche}' cho KOC {clean_id}")

    # Cập nhật trong Staging DB nếu có
    record = staging_repo.get_candidate_by_id(clean_id)
    if not record:
        record = staging_repo.get_candidate_by_identifier(clean_id)

    plat = record.platform if record else "TIKTOK"
    username = record.username if record else clean_id

    # 1. Đồng bộ sang Database chính (Spring Boot / PostgreSQL creators table)
    try:
        raw_crawled = get_crawled_result_by_username(username, plat)
        if raw_crawled:
            if not raw_crawled.get("aiEnrichment"):
                raw_crawled["aiEnrichment"] = {}
            raw_crawled["aiEnrichment"]["niche"] = req.niche
            raw_crawled["aiEnrichment"]["creatorType"] = req.primaryType or "KOC"

            payload_obj = IngestPayload.model_validate(raw_crawled)
            java_payload = JavaMapper.from_ingest_payload(payload_obj)
            sent_ok = java_client.send_creator_data(java_payload)
            if sent_ok:
                logger.info(f"[STANDARDIZE] Đã đồng bộ KOC @{username} vào bảng Creator chính thành công!")
            else:
                logger.warning(f"[STANDARDIZE] Gửi sang Java Backend thất bại cho @{username}")
    except Exception as e:
        logger.warning(f"[STANDARDIZE] Lỗi đồng bộ sang Java Backend cho @{username}: {e}")

    # 2. Đánh dấu đã INGESTED và MANAGER_APPROVED để không còn hiển thị ở bảng chờ duyệt
    staging_repo.update_status_and_decision(
        platform=plat,
        username=username,
        status=ProcessingStatus.INGESTED,
        decision=Decision.MANAGER_APPROVED,
        primary_type=req.primaryType or "KOC",
        reason_codes=[f"STANDARDIZED_NICHE_{req.niche.upper().replace(' ', '_')}"],
    )

    return {
        "status": "success",
        "message": f"Đã chuẩn hóa ngành hàng thành công cho KOC ({req.niche})!",
        "candidateId": candidate_id,
        "niche": req.niche,
        "standardizedAt": now_str,
    }


@router.post("/{candidate_id}/reject", summary="Từ chối hồ sơ KOC (Đưa vào danh sách từ chối)")
async def reject_candidate(candidate_id: str, note: Optional[str] = "") -> dict[str, Any]:
    """
    Từ chối hồ sơ KOC: Đánh dấu MANAGER_REJECTED để loại khỏi danh sách chờ duyệt
    và lưu vào blacklist chống cào lặp.
    """
    clean_id = candidate_id.removeprefix("metric-").removeprefix("cr-")
    record = staging_repo.get_candidate_by_id(clean_id)
    if not record:
        record = staging_repo.get_candidate_by_identifier(clean_id)

    plat = record.platform if record else "TIKTOK"
    username = record.username if record else clean_id

    staging_repo.update_status_and_decision(
        platform=plat,
        username=username,
        status=ProcessingStatus.FAILED,
        decision=Decision.MANAGER_REJECTED,
        reason_codes=["REJECTED_BY_DATA_MANAGER"],
    )

    logger.info(f"[REJECT] Đã từ chối KOC @{username}")
    return {
        "status": "success",
        "message": f"Đã từ chối KOC @{username}!",
        "candidateId": candidate_id,
    }


@router.post("/{candidate_id}/recrawl", summary="Cào lại dữ liệu KOC mới nhất")
async def recrawl_candidate(candidate_id: str) -> dict[str, Any]:
    """
    Thực hiện cào lại trực tiếp để lấy dữ liệu mới nhất.
    """
    clean_user = candidate_id.removeprefix("metric-").removeprefix("cr-")
    logger.info(f"[RECRAWL] Gửi lệnh cào lại cho @{clean_user}")

    # Lấy thông tin nền tảng nếu có
    record = staging_repo.get_candidate_by_id(clean_id:=clean_user)
    plat = record.platform if record else "TIKTOK"

    try:
        res = crawl_single_koc_live(clean_user, platform=plat)
        return {
            "status": "success",
            "message": f"Đã cào lại thành công cho @{clean_user}!",
            "result": res,
        }
    except Exception as e:
        logger.warning(f"Lỗi cào lại @{clean_user}: {e}")
        return {
            "status": "error",
            "message": f"Lỗi cào lại: {str(e)}",
        }


@router.get("/identity-conflicts", summary="Danh sách xung đột danh tính đa nền tảng")
async def get_identity_conflicts(status: Optional[str] = Query(None)) -> list[dict[str, Any]]:
    """
    Lấy danh sách các trường hợp nghi vấn trùng lặp hoặc xung đột đa nền tảng.
    """
    conflicts = _load_conflicts_state()
    if status:
        return [c for c in conflicts if c.get("status") == status]
    return conflicts


@router.post("/identity-conflicts/{conflict_id}/resolve", summary="Giải quyết xung đột danh tính")
async def resolve_identity_conflict(conflict_id: str, req: ConflictResolveRequest) -> dict[str, Any]:
    """
    Hợp nhất hồ sơ (MERGE) hoặc xác nhận tách riêng (SEPARATE).
    """
    conflicts = _load_conflicts_state()
    found = False
    for c in conflicts:
        if c.get("id") == conflict_id:
            c["status"] = "MERGED" if req.action == "MERGE" else "RESOLVED_SEPARATE"
            c["resolvedAt"] = datetime.now(UTC).isoformat()
            c["resolvedBy"] = "data_manager"
            c["masterProfileId"] = req.masterProfileId
            found = True
            break

    if not found:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy conflict ID '{conflict_id}'")

    _save_conflicts_state(conflicts)
    action_text = "Hợp nhất hồ sơ thành công" if req.action == "MERGE" else "Đã tách biệt 2 hồ sơ độc lập"
    return {
        "status": "success",
        "message": f"{action_text}!",
        "conflictId": conflict_id,
        "action": req.action,
    }


@router.post("/batch-reclassify", summary="Thẩm định & tái phân loại Creator hàng loạt trong DB")
async def batch_reclassify_creators(req: BatchReclassifyRequest) -> dict[str, Any]:
    """
    Quét theo đợt các Creator đang lưu trong Database (PostgreSQL hoặc Staging SQLite):
    - Kiểm tra và sàng lọc rác: Shop bán hàng sỉ lẻ, reup/spam, tài khoản bị xóa.
    - Phân loại lại thông minh: Phân định chính xác KOC vs KOL dựa trên video & tương tác.
    - Tính lại điểm chất lượng (dataConfidence): nguồn, độ tươi, độ đầy đủ, tính hợp lý.
    - Sinh lại vector nhúng BKAI 768 chiều.
    - Đồng bộ sang Java Backend nếu dry_run=False.
    """
    return await asyncio.to_thread(
        audit_service.audit_batch,
        limit=req.limit,
        offset=req.offset,
        platform=req.platform,
        category=req.category,
        dry_run=req.dry_run,
        auto_quarantine=req.auto_quarantine,
    )


@router.get("/audit-report", summary="Lấy báo cáo thẩm định DB gần nhất")
async def get_latest_audit_report() -> dict[str, Any]:
    """
    Xem thống kê bao nhiêu % dữ liệu đạt chuẩn, bao nhiêu kênh rác bị phát hiện
    từ đợt quét thẩm định gần nhất.
    """
    return audit_service.get_latest_report()

