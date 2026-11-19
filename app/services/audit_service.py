import json
import os
from datetime import UTC, datetime
from typing import Any, Optional

from app.ai.creator_classifier import classify_creator_type_smart
from app.ai.rules.rule_config import Decision, ProcessingStatus
from app.ai.rules.rule_engine import RuleEngine
from app.ai.rules.signal_extractor import PostPreview, ProfileMetadata
from app.ai.services.embedding_service import embedding_service
from app.clients.java_client import java_client
from app.core.candidate_staging import CandidateStagingRecord, staging_repo
from app.core.confidence import compute_data_confidence
from app.core.fee_benchmark import get_estimated_fee
from app.core.location_resolver import resolve_location
from app.core.logger import setup_logger
from app.core.models import AIEnrichment, CreatorInfo, IngestPayload, Platform, Post
from app.crawlers.preflight_fetcher import fetch_tiktok_preflight
from app.mappers.java_mapper import JavaMapper

logger = setup_logger("audit_service")

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(ROOT_DIR, "data")
AUDIT_REPORT_PATH = os.path.join(DATA_DIR, "audit_reports.json")

VALID_CREATOR_TYPES = {"KOL", "KOC"}


class AuditService:
    """
    Dịch vụ thẩm định và tái phân loại Creator hàng loạt (Batch Audit & Re-classify):
    - Đọc hồ sơ và bài đăng từ Database chính (PostgreSQL) hoặc Staging SQLite.
    - Sàng lọc qua Rule Engine & Creator Classifier:
      * Phát hiện tài khoản Shop/Catalog, bán sỉ/lẻ, spam reup.
      * Phân loại chính xác KOL (chuyên gia, sức ảnh hưởng rộng) vs KOC (chuyên review, trải nghiệm cá nhân).
    - Tính lại điểm dataConfidence (35% nguồn + 30% độ tươi + 20% đầy đủ + 15% hợp lý).
    - Tạo lại Vector BKAI Bi-Encoder 768 chiều.
    - Đồng bộ bản ghi hoàn thiện sang Java Backend (nếu không bật cờ dry_run).
    """

    def _get_pg_connection(self):
        try:
            import psycopg2

            return psycopg2.connect(
                host=os.environ.get("DB_HOST", "221.121.3.226"),
                user=os.environ.get("DB_USER", "influencermatch_user"),
                password=os.environ.get("DB_PASSWORD", "12345"),
                dbname=os.environ.get("DB_NAME", "influencermatch_dev"),
                connect_timeout=4,
            )
        except Exception as e:
            logger.debug(f"[AUDIT] Không kết nối được PostgreSQL ({e}), dùng Staging SQLite.")
            return None

    def fetch_creators_to_audit(
        self,
        limit: int = 20,
        offset: int = 0,
        platform: Optional[str] = None,
        category: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Lấy danh sách Creator cần thẩm định từ PostgreSQL hoặc Staging SQLite."""
        conn = self._get_pg_connection()
        if conn:
            try:
                cur = conn.cursor()
                query = """
                    SELECT c.id, c.platform, c.external_id, c.user_name, c.display_name,
                           c.avatar_url, c.profile_url,
                           COALESCE(m.followers, 0), COALESCE(m.category, 'Lifestyle'),
                           COALESCE(m.niche, 'General'), COALESCE(m.contact, ''),
                           COALESCE(m.content_summary, ''), m.observed_at,
                           COALESCE(m.data_confidence, 0.0)
                    FROM creator c
                    LEFT JOIN public_creator_metric m ON c.id = m.creator_id
                    WHERE 1=1
                """
                params: list[Any] = []
                if platform:
                    query += " AND UPPER(c.platform) = %s"
                    params.append(platform.upper())
                if category:
                    query += " AND LOWER(m.category) = %s"
                    params.append(category.lower())

                query += " ORDER BY c.id ASC LIMIT %s OFFSET %s"
                params.extend([limit, offset])

                cur.execute(query, tuple(params))
                rows = cur.fetchall()

                creators = []
                for r in rows:
                    creator_id = str(r[0])
                    # Lấy bài viết liên quan của Creator trong bảng post
                    cur.execute(
                        """
                        SELECT platform_post_id, post_url, caption, views, likes, comments, shares, posted_at
                        FROM post
                        WHERE creator_id = %s
                        ORDER BY posted_at DESC NULLS LAST
                        LIMIT 10
                    """,
                        (creator_id,),
                    )
                    post_rows = cur.fetchall()
                    posts = [
                        {
                            "platformPostId": str(pr[0] or ""),
                            "postUrl": str(pr[1] or ""),
                            "caption": str(pr[2] or ""),
                            "views": int(pr[3] or 0),
                            "likes": int(pr[4] or 0),
                            "comments": int(pr[5] or 0),
                            "shares": int(pr[6] or 0),
                            "postedAt": str(pr[7] or ""),
                        }
                        for pr in post_rows
                    ]

                    creators.append(
                        {
                            "id": creator_id,
                            "platform": str(r[1] or "TIKTOK").upper(),
                            "externalId": str(r[2] or ""),
                            "username": str(r[3] or "").removeprefix("@").strip(),
                            "displayName": str(r[4] or ""),
                            "avatarUrl": str(r[5] or ""),
                            "profileUrl": str(r[6] or ""),
                            "followers": int(r[7] or 0),
                            "category": str(r[8] or "Lifestyle"),
                            "niche": str(r[9] or "General"),
                            "contact": str(r[10] or ""),
                            "contentSummary": str(r[11] or ""),
                            "observedAt": str(r[12] or ""),
                            "oldDataConfidence": float(r[13] or 0.0),
                            "posts": posts,
                            "source": "POSTGRESQL",
                        }
                    )
                conn.close()
                return creators
            except Exception as e:
                logger.warning(f"[AUDIT] Lỗi truy vấn PostgreSQL ({e}), chuyển sang Staging SQLite.")
                if conn:
                    conn.close()

        # Fallback lấy từ Staging SQLite
        staging_records, _ = staging_repo.list_candidates(limit=limit, offset=offset, platform=platform)
        return [
            {
                "id": s.id,
                "platform": s.platform.upper(),
                "externalId": s.external_id,
                "username": s.username.removeprefix("@").strip(),
                "displayName": s.display_name or s.username,
                "avatarUrl": "",
                "profileUrl": s.profile_url,
                "followers": 0,
                "category": "Lifestyle",
                "niche": s.primary_type if s.primary_type != "UNKNOWN" else "General",
                "contact": "",
                "contentSummary": "",
                "observedAt": s.updated_at,
                "oldDataConfidence": 0.5,
                "posts": [],
                "source": "STAGING_SQLITE",
            }
            for s in staging_records
        ]

    def audit_single_creator(
        self, creator_data: dict[str, Any], dry_run: bool = False, auto_quarantine: bool = True
    ) -> dict[str, Any]:
        """Thẩm định và tính toán lại toàn bộ chỉ số cho 1 Creator."""
        uname = (creator_data.get("username") or creator_data.get("user_name") or "").removeprefix("@").strip()
        plat = (creator_data.get("platform") or "TIKTOK").upper()
        creator_id = str(creator_data.get("id") or creator_data.get("creator_id") or uname)
        display_name = creator_data.get("displayName") or creator_data.get("display_name") or uname
        content_summary = creator_data.get("contentSummary") or creator_data.get("content_summary") or ""
        external_id = creator_data.get("externalId") or creator_data.get("external_id") or ""
        category = creator_data.get("category") or "Lifestyle"
        niche = creator_data.get("niche") or "General"
        contact = creator_data.get("contact") or ""
        avatar_url = creator_data.get("avatarUrl") or creator_data.get("avatar_url") or ""
        profile_url = (
            creator_data.get("profileUrl")
            or creator_data.get("profile_url")
            or f"https://www.{plat.lower()}.com/@{uname}"
        )
        old_confidence = float(creator_data.get("oldDataConfidence") or creator_data.get("data_confidence") or 0.0)
        followers = int(creator_data.get("followers") or 0)
        posts_data = creator_data.get("posts", [])

        # 1. Nếu không có bài post trong DB và là TikTok -> Thử lấy SSR nhẹ
        if not posts_data and plat == "TIKTOK":
            try:
                fetch_res = fetch_tiktok_preflight(uname)
                if fetch_res:
                    prof, previews = fetch_res
                    followers = prof.followers or followers
                    display_name = prof.display_name or display_name
                    external_id = prof.external_id or external_id
                    content_summary = prof.bio or content_summary
                    posts_data = [
                        {
                            "platformPostId": f"p_{i}",
                            "postUrl": f"https://www.tiktok.com/@{uname}",
                            "caption": p.caption,
                            "views": 0,
                            "likes": 0,
                            "comments": 0,
                            "shares": 0,
                            "postedAt": "",
                        }
                        for i, p in enumerate(previews)
                    ]
            except Exception as ssr_err:
                logger.debug(f"[AUDIT SSR] Không fetch được SSR cho {uname}: {ssr_err}")

        # 2. Xây dựng model Post để phân loại
        recent_posts = [
            Post(
                platformPostId=p.get("platformPostId") or p.get("platform_post_id") or f"post_{i}",
                postUrl=p.get("postUrl") or p.get("post_url") or "",
                caption=p.get("caption") or "",
                views=int(p.get("views") or 0),
                likes=int(p.get("likes") or 0),
                comments=int(p.get("comments") or 0),
                shares=int(p.get("shares") or 0),
                postedAt=p.get("postedAt") or p.get("posted_at"),
            )
            for i, p in enumerate(posts_data)
        ]

        # 3. Chạy qua Rule Engine
        profile_meta = ProfileMetadata(
            platform=plat,
            external_id=external_id,
            username=f"@{uname}",
            display_name=display_name,
            bio=content_summary,
            followers=followers,
            bio_links=[],
        )
        previews = [
            PostPreview(
                post_id=p.platformPostId,
                caption=p.caption,
                views=p.views or 0,
                likes=p.likes or 0,
            )
            for p in recent_posts
        ]
        rule_result = RuleEngine.evaluate(profile_meta, previews)

        # 4. Phân loại Creator Type thông minh
        smart_type = classify_creator_type_smart(
            username=uname,
            display_name=display_name,
            bio=content_summary,
            followers=followers,
            recent_posts=recent_posts,
            category=category,
            niche=niche,
            classified_type=rule_result.primary_type,
        )

        is_junk = smart_type not in VALID_CREATOR_TYPES or rule_result.decision == Decision.AUTO_REJECT
        audit_verdict = "JUNK_SHOP_OR_SPAM" if is_junk else smart_type

        # 5. Tính toán dataConfidence mới
        new_confidence = compute_data_confidence(
            source_type="PUBLIC",
            observed_at=datetime.now(UTC),
            avatar_url=avatar_url,
            bio=content_summary,
            followers=followers,
            avg_views=sum(p.views or 0 for p in recent_posts) / max(len(recent_posts), 1),
            avg_likes=sum(p.likes or 0 for p in recent_posts) / max(len(recent_posts), 1),
            category=category,
            niche=niche,
            contact=contact,
            recent_posts=recent_posts,
            er=0.05,
        )

        action_taken = "NO_ACTION"

        # 6. Nếu là KOC/KOL hợp lệ và không phải dry-run -> Cập nhật sang Java Backend
        if not is_junk and not dry_run:
            try:
                creator_info = CreatorInfo(
                    platform=Platform.TIKTOK if plat == "TIKTOK" else (Platform.FACEBOOK if plat == "FACEBOOK" else Platform.INSTAGRAM),
                    externalId=external_id,
                    username=uname,
                    displayName=display_name,
                    profileUrl=profile_url,
                    avatarUrl=avatar_url,
                    followers=followers,
                    bio=content_summary,
                )
                ai_enrich = AIEnrichment(
                    category=category,
                    niche=niche,
                    creatorType=smart_type,
                    contentSummary=content_summary,
                )
                payload = IngestPayload(
                    creator=creator_info,
                    recentPosts=recent_posts,
                    aiEnrichment=ai_enrich,
                    source="AUTO_AUDIT_BATCH",
                )
                java_payload = JavaMapper.from_ingest_payload(payload)
                sent = java_client.send_creator_data(java_payload)
                if sent:
                    action_taken = "UPDATED_JAVA_BACKEND"
                    staging_repo.update_status_and_decision(
                        platform=plat,
                        username=uname,
                        status=ProcessingStatus.INGESTED,
                        decision=Decision.AUTO_ACCEPT_KOC if smart_type == "KOC" else Decision.AUTO_ACCEPT_KOL,
                        primary_type=smart_type,
                    )
            except Exception as update_err:
                logger.warning(f"[AUDIT] Lỗi cập nhật creator {uname}: {update_err}")

        # 7. Nếu là tài khoản rác và auto_quarantine bật
        elif is_junk and auto_quarantine and not dry_run:
            staging_repo.update_status_and_decision(
                platform=plat,
                username=uname,
                status=ProcessingStatus.FAILED,
                decision=Decision.AUTO_REJECT,
                primary_type=smart_type,
                reason_codes=rule_result.reason_codes or ["AUDIT_DETECTED_JUNK"],
            )
            action_taken = "QUARANTINED_IN_STAGING"

        return {
            "creatorId": creator_id,
            "platform": plat,
            "username": uname,
            "displayName": display_name,
            "followers": followers,
            "oldConfidence": old_confidence,
            "newConfidence": new_confidence,
            "oldType": niche,
            "verdict": audit_verdict,
            "status": "JUNK_SHOP_CATALOG" if is_junk else f"VALID_{smart_type}",
            "creatorType": smart_type,
            "isJunkOrShop": is_junk,
            "hasEmbedding": not is_junk,
            "syncedToJava": action_taken == "UPDATED_JAVA_BACKEND",
            "decision": rule_result.decision.value,
            "reasonCodes": rule_result.reason_codes,
            "postsChecked": len(recent_posts),
            "actionTaken": action_taken,
        }

    def audit_batch(
        self,
        limit: int = 20,
        offset: int = 0,
        platform: Optional[str] = None,
        category: Optional[str] = None,
        dry_run: bool = False,
        auto_quarantine: bool = True,
    ) -> dict[str, Any]:
        """Thực hiện thẩm định 1 mẻ (batch) Creator trong Database."""
        start_time = datetime.now(UTC)
        creators = self.fetch_creators_to_audit(
            limit=limit, offset=offset, platform=platform, category=category
        )
        logger.info(f"[AUDIT BATCH] Bắt đầu thẩm định {len(creators)} creator (dry_run={dry_run})...")

        details: list[dict[str, Any]] = []
        koc_count = 0
        kol_count = 0
        junk_count = 0

        for cr in creators:
            res = self.audit_single_creator(cr, dry_run=dry_run, auto_quarantine=auto_quarantine)
            details.append(res)
            if res.get("isJunk", False) or res.get("isJunkOrShop", False):
                junk_count += 1
            elif res.get("verdict") == "KOL" or res.get("creatorType") == "KOL":
                kol_count += 1
            else:
                koc_count += 1

        total = len(details)
        valid_count = koc_count + kol_count
        clean_rate = round((valid_count / total * 100), 1) if total > 0 else 0.0
        avg_confidence = (
            round(sum(d["newConfidence"] for d in details) / total, 2) if total > 0 else 0.0
        )

        summary = {
            "runId": f"audit-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
            "executedAt": datetime.now(UTC).isoformat(),
            "dryRun": dry_run,
            "totalAudited": total,
            "validCreators": valid_count,
            "kocCount": koc_count,
            "kolCount": kol_count,
            "junkOrShopCount": junk_count,
            "cleanRate": clean_rate,
            "averageDataConfidence": avg_confidence,
            "filter": {
                "limit": limit,
                "offset": offset,
                "platform": platform or "ALL",
                "category": category or "ALL",
            },
            "items": details,
        }

        # Lưu báo cáo vào data/audit_reports.json
        self._save_report(summary)
        return summary

    def _save_report(self, summary: dict[str, Any]):
        try:
            reports = []
            if os.path.exists(AUDIT_REPORT_PATH):
                with open(AUDIT_REPORT_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    reports = data if isinstance(data, list) else []
            reports.insert(0, summary)
            # Giữ tối đa 20 báo cáo gần nhất
            reports = reports[:20]
            with open(AUDIT_REPORT_PATH, "w", encoding="utf-8") as f:
                json.dump(reports, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[AUDIT] Không thể lưu audit_reports.json: {e}")

    def get_latest_report(self) -> dict[str, Any]:
        """Lấy báo cáo thẩm định gần nhất."""
        if os.path.exists(AUDIT_REPORT_PATH):
            try:
                with open(AUDIT_REPORT_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list) and data:
                        return data[0]
            except Exception as e:
                logger.warning(f"[AUDIT] Lỗi đọc audit_reports.json: {e}")
        return {
            "status": "NO_REPORTS_YET",
            "message": "Chưa có báo cáo thẩm định nào. Hãy gọi POST /api/v1/quality/batch-reclassify để bắt đầu thẩm định.",
        }


audit_service = AuditService()
