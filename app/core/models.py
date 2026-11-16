from datetime import UTC, datetime
from enum import Enum
import uuid

from pydantic import BaseModel, Field


class Platform(str, Enum):
    TIKTOK = "TIKTOK"
    YOUTUBE = "YOUTUBE"
    INSTAGRAM = "INSTAGRAM"
    FACEBOOK = "FACEBOOK"


class CreatorInfo(BaseModel):
    platform: Platform
    externalId: str = Field(..., description="ID gốc từ platform cấp, NOT NULL")
    isStableExternalId: bool = True  # Facebook slug = False, TikTok/YouTube = True
    username: str = Field(..., description="handle account, NOT NULL")
    displayName: str = Field(..., description="NOT NULL")
    profileUrl: str = Field(..., description="NOT NULL")
    avatarUrl: str | None = None
    followers: int = Field(..., description="NOT NULL")
    bio: str | None = None
    isVerified: bool = False

    # Optional commercial contact info (extra fields not in example but useful)
    publicEmail: str | None = None
    publicPhone: str | None = None
    bioCrossLinks: list[str] = Field(default_factory=list)


class Post(BaseModel):
    platformPostId: str
    postUrl: str
    caption: str
    views: int | None = 0
    likes: int | None = 0
    comments: int | None = 0
    shares: int | None = 0
    coverUrl: str | None = ""
    postedAt: str | None = None


class AIEnrichment(BaseModel):
    niche: str | None = None
    category: str | None = None
    creatorType: str | None = None
    contentSummary: str | None = None
    tags: list[str] = Field(default_factory=list)  # Raw AI tags if needed
    suggestedTopic: str | None = None
    semanticTags: list[str] = Field(default_factory=list)


class IngestPayload(BaseModel):
    creator: CreatorInfo
    recentPosts: list[Post] = Field(default_factory=list)
    aiEnrichment: AIEnrichment | None = None
    source: str
    crawlRunId: str = Field(default_factory=lambda: f"CR-{uuid.uuid4().hex[:12].upper()}")
    observedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    collectedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def avg_views(self) -> float:
        valid_vals = [p.views for p in self.recentPosts if p.views is not None]
        return sum(valid_vals) / len(valid_vals) if valid_vals else 0.0

    @property
    def avg_likes(self) -> float:
        valid_vals = [p.likes for p in self.recentPosts if p.likes is not None]
        return sum(valid_vals) / len(valid_vals) if valid_vals else 0.0

    @property
    def avg_comments(self) -> float:
        valid_vals = [p.comments for p in self.recentPosts if p.comments is not None]
        return sum(valid_vals) / len(valid_vals) if valid_vals else 0.0

    @property
    def avg_shares(self) -> float:
        valid_vals = [p.shares for p in self.recentPosts if p.shares is not None]
        return sum(valid_vals) / len(valid_vals) if valid_vals else 0.0

    @property
    def engagement_rate(self) -> float:
        followers = self.creator.followers
        if followers <= 0:
            return 0.0
        return (
            (self.avg_likes + self.avg_comments + self.avg_shares) / followers
        ) * 100

    @property
    def engagement_rate_views(self) -> float:
        """
        View-based Engagement Rate cho video ngắn (TikTok, Reels, Shorts):
        ER_views = (avgLikes + avgComments + avgShares) / avgViews * 100%
        """
        views = self.avg_views
        if views <= 0:
            return 0.0
        return (
            (self.avg_likes + self.avg_comments + self.avg_shares) / views
        ) * 100.0

