from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class Platform(str, Enum):
    TIKTOK = "TIKTOK"
    YOUTUBE = "YOUTUBE"
    INSTAGRAM = "INSTAGRAM"
    FACEBOOK = "FACEBOOK"


class CreatorInfo(BaseModel):
    platform: Platform
    externalId: str = Field(..., description="ID gốc từ platform cấp, NOT NULL")
    username: str = Field(..., description="handle account, NOT NULL")
    displayName: str = Field(..., description="NOT NULL")
    profileUrl: str = Field(..., description="NOT NULL")
    avatarUrl: str | None = None
    followers: int = Field(..., description="NOT NULL")
    bio: str | None = None

    # Optional commercial contact info (extra fields not in example but useful)
    publicEmail: str | None = None
    publicPhone: str | None = None


class Post(BaseModel):
    platformPostId: str
    postUrl: str
    caption: str
    views: int | None = 0
    likes: int | None = 0
    comments: int | None = 0
    shares: int | None = 0
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
