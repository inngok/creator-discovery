from app.ai.services.embedding_service import embedding_service
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
        niche = result.aiEnrichment.niche if result.aiEnrichment else "General"
        category = result.aiEnrichment.category if result.aiEnrichment else "Lifestyle"
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

        return {
            "platform": result.creator.platform.value,
            "externalId": result.creator.externalId,
            "userName": result.creator.username,
            "displayName": result.creator.displayName,
            "avatarUrl": result.creator.avatarUrl,
            "creatorType": creator_type,
            "location": "Vietnam",
            "profileUrl": result.creator.profileUrl,
            "followers": result.creator.followers,
            "avgViews": result.avg_views,
            "avgLikes": result.avg_likes,
            "avgComments": result.avg_comments,
            "avgShares": result.avg_shares,
            "engagementRate": result.engagement_rate,
            "niche": niche,
            "contentSummary": content_summary,
            "contact": getattr(result.creator, "publicEmail", None) or "",
            "category": category,
            "freshnessStatus": "FRESH",
            "dataConfidence": 0.95,
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
