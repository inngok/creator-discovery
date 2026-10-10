from app.core.models import IngestPayload


class JavaMapper:
    @staticmethod
    def from_ingest_payload(result: IngestPayload) -> dict:
        recent_posts_list = []
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

        creator_type = (
            getattr(result.aiEnrichment, "creatorType", "UNKNOWN")
            if result.aiEnrichment
            else "UNKNOWN"
        )

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
            "niche": result.aiEnrichment.niche if result.aiEnrichment else "General",
            "contentSummary": result.creator.bio or "Creator",
            "contact": getattr(result.creator, "publicEmail", None) or "",
            "category": (
                result.aiEnrichment.category if result.aiEnrichment else "Lifestyle"
            ),
            "freshnessStatus": "FRESH",
            "dataConfidence": 0.95,
            "recentPosts": recent_posts_list,
        }
