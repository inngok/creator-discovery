from datetime import UTC, datetime
from enum import Enum
import uuid

from pydantic import BaseModel, Field, field_validator
from app.core.post_dates import normalize_post_date


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

    # Optional public commercial contact info
    publicEmail: str | None = None
    publicPhone: str | None = None
    bioCrossLinks: list[str] = Field(default_factory=list)


class Post(BaseModel):
    platformPostId: str
    postUrl: str
    caption: str
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    coverUrl: str | None = ""
    postedAt: str | None = None

    @field_validator("postedAt", mode="before")
    @classmethod
    def normalize_publication_date(cls, value):
        return normalize_post_date(value)


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

    def _avg_observed(self, field: str) -> float | None:
        valid_vals = [getattr(p, field) for p in self.recentPosts if getattr(p, field) is not None]
        return sum(valid_vals) / len(valid_vals) if valid_vals else None

    @property
    def avg_views(self) -> float | None:
        return self._avg_observed("views")

    @property
    def avg_likes(self) -> float | None:
        return self._avg_observed("likes")

    @property
    def avg_comments(self) -> float | None:
        return self._avg_observed("comments")

    @property
    def avg_shares(self) -> float | None:
        return self._avg_observed("shares")

    @property
    def engagement_rate(self) -> float | None:
        followers = self.creator.followers
        if followers <= 0:
            return None
        observed = [v for v in (self.avg_likes, self.avg_comments, self.avg_shares) if v is not None]
        if not observed:
            return None
        return (sum(observed) / followers) * 100

    @property
    def engagement_rate_views(self) -> float | None:
        """
        View-based Engagement Rate cho video ngắn (TikTok, Reels, Shorts):
        ER_views = (avgLikes + avgComments + avgShares) / avgViews * 100%
        """
        views = self.avg_views
        if views is None or views <= 0:
            return None
        observed = [v for v in (self.avg_likes, self.avg_comments, self.avg_shares) if v is not None]
        if not observed:
            return None
        return (sum(observed) / views) * 100.0

