import re
from app.ai.services.embedding_service import embedding_service
from app.core.confidence import compute_data_confidence
from app.core.fee_benchmark import get_estimated_fee
from app.core.location_resolver import resolve_location
from app.core.models import IngestPayload


class JavaMapper:
    @staticmethod
    def from_ingest_payload(result: IngestPayload) -> dict:
        recent_posts_list = []
        valid_captions = []
        for p in result.recentPosts:
            recent_posts_list.append(
                {
                    "platformPostId": p.platformPostId,
                    "postUrl": p.postUrl,
                    "caption": p.caption,
                    "views": p.views or 0,
                    "likes": p.likes or 0,
                    "comments": p.comments or 0,
                    "shares": p.shares or 0,
                    "postedAt": p.postedAt or "",
                }
            )
            if p.caption and p.caption.strip():
                valid_captions.append(p.caption.strip())

        creator_type = (
            getattr(result.aiEnrichment, "creatorType", "UNKNOWN")
            if result.aiEnrichment
            else "UNKNOWN"
        )
        niche = (result.aiEnrichment.niche if result.aiEnrichment else None) or "UNKNOWN"
        category = (result.aiEnrichment.category if result.aiEnrichment else None) or "UNKNOWN"
        content_summary = (
            result.aiEnrichment.contentSummary
            if result.aiEnrichment and getattr(result.aiEnrichment, "contentSummary", None)
            else (result.creator.bio or "Creator")
        )

        # 1. Ghép đoạn text ngữ cảnh để mô hình BKAI Bi-Encoder hiểu sâu nhất
        text_parts = [
            f"KOL/KOC: {result.creator.displayName or result.creator.username}",
            f"Chuyên mục: {category} - {niche}",
            f"Tóm tắt nội dung: {content_summary}",
        ]
        if valid_captions:
            text_parts.append(f"Nội dung bài đăng gần đây: {' | '.join(valid_captions[:5])}")
        creator_search_text = "\n".join(text_parts)

        # 2. Sinh vector 768 chiều dùng mô hình BKAI
        try:
            embedding_vector = embedding_service.get_search_embedding(creator_search_text)
        except Exception as emb_err:
            embedding_vector = []

        # 3. Tính toán dataConfidence theo 4 thành phần quy đổi:
        # Data Confidence = (35% x Nguồn) + (30% x Độ tươi) + (20% x Độ đầy đủ) + (15% x Hợp lý)
        data_confidence = compute_data_confidence(
            source_type=getattr(result, "source", "PUBLIC"),
            observed_at=getattr(result, "observedAt", None),
            avatar_url=result.creator.avatarUrl or "",
            bio=result.creator.bio or "",
            followers=result.creator.followers or 0,
            avg_views=result.avg_views or 0.0,
            avg_likes=result.avg_likes or 0.0,
            category=category,
            niche=niche,
            contact=getattr(result.creator, "publicEmail", None) or getattr(result.creator, "publicPhone", None) or "",
            recent_posts=result.recentPosts or [],
            er=result.engagement_rate or 0.0,
        )

        # 4. Tra cứu giá ước tính tham khảo theo followers (Declarative 0 if-else)
        est_fee_min, est_fee_max = get_estimated_fee(result.creator.followers)

        return {
            "platform": result.creator.platform.value,
            "externalId": result.creator.externalId,
            "userName": result.creator.username.strip().lstrip("@").strip(),
            "displayName": result.creator.displayName,
            "avatarUrl": result.creator.avatarUrl,
            "creatorType": creator_type,
            "location": resolve_location(
                display_name=result.creator.displayName,
                username=result.creator.username,
                bio=result.creator.bio,
            ),
            "profileUrl": result.creator.profileUrl,
            "followers": result.creator.followers,
            "estimatedFeeMin": est_fee_min,
            "estimatedFeeMax": est_fee_max,
            "actualFee": None,
            "avgViews": result.avg_views,
            "avgLikes": result.avg_likes,
            "avgComments": result.avg_comments,
            "avgShares": result.avg_shares,
            "engagementRate": result.engagement_rate,
            "engagementRateViews": result.engagement_rate_views,
            "niche": niche,
            "contentSummary": content_summary,
            "contact": getattr(result.creator, "publicEmail", None) or "",
            "category": category,
            "freshnessStatus": "FRESH",
            "dataConfidence": data_confidence,
            "crawlRunId": getattr(result, "crawlRunId", ""),
            "observedAt": result.observedAt.strftime("%Y-%m-%dT%H:%M:%S") if hasattr(result, "observedAt") and result.observedAt else None,
            "isStableExternalId": getattr(result.creator, "isStableExternalId", True),
            "bioCrossLinks": getattr(result.creator, "bioCrossLinks", []),
            "sourceType": "PUBLIC",
            "source_type": "PUBLIC",
            "masterId": None,
            "master_id": None,
            "recentPosts": recent_posts_list,
            "embedding": embedding_vector,
        }
