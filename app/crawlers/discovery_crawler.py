"""
Module Discovery Crawler (Orchestrator).
Tách riêng logic tìm kiếm theo từng nền tảng:
- TikTok: app.crawlers.tiktok_discovery
- Facebook: app.crawlers.facebook_discovery
- Instagram: app.crawlers.instagram_discovery
"""

from app.crawlers.tiktok_discovery import (
    TikTokDiscoveryError,
    search_new_tiktok_candidates,
    _extract_trending_authors,
    _trending_matches_topic,
    _verify_tiktok_candidate_followers,
    _init_discovery_context,
)
from app.crawlers.facebook_discovery import (
    FacebookDiscoveryError,
    search_new_facebook_candidates,
)
from app.crawlers.instagram_discovery import (
    InstagramDiscoveryError,
    search_new_instagram_candidates,
)

__all__ = [
    "TikTokDiscoveryError",
    "search_new_tiktok_candidates",
    "FacebookDiscoveryError",
    "search_new_facebook_candidates",
    "InstagramDiscoveryError",
    "search_new_instagram_candidates",
]
