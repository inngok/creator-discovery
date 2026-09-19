from enum import Enum
from typing import List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field

class Platform(str, Enum):
    TIKTOK = "TIKTOK"
    YOUTUBE = "YOUTUBE"
    INSTAGRAM = "INSTAGRAM"

class CreatorInfo(BaseModel):
    platform: Platform
    externalId: str = Field(..., description="ID gốc từ platform cấp, NOT NULL")
    username: str = Field(..., description="handle account, NOT NULL")
    displayName: str = Field(..., description="NOT NULL")
    profileUrl: str = Field(..., description="NOT NULL")
    avatarUrl: Optional[str] = None
    followers: int = Field(..., description="NOT NULL")
    bio: Optional[str] = None
    
    # Optional commercial contact info (extra fields not in example but useful)
    publicEmail: Optional[str] = None
    publicPhone: Optional[str] = None

class Post(BaseModel):
    platformPostId: str
    postUrl: str
    caption: str
    views: Optional[int] = 0
    likes: Optional[int] = 0
    comments: Optional[int] = 0
    shares: Optional[int] = 0
    postedAt: Optional[str] = None

class AIEnrichment(BaseModel):
    niche: Optional[str] = None
    category: Optional[str] = None
    creatorType: Optional[str] = None
    contentSummary: Optional[str] = None
    tags: List[str] = Field(default_factory=list) # Raw AI tags if needed

class IngestPayload(BaseModel):
    creator: CreatorInfo
    recentPosts: List[Post] = Field(default_factory=list)
    aiEnrichment: Optional[AIEnrichment] = None
    source: str
    collectedAt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
