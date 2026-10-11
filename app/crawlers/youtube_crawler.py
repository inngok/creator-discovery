import os

import httpx

from app.core.models import CreatorInfo, IngestPayload, Platform


async def crawl_youtube_channel(handle: str) -> IngestPayload | None:
    handle = handle.removeprefix("@")

    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        raise ValueError("YOUTUBE_API_KEY environment variable is not set")

    base_url = "https://www.googleapis.com/youtube/v3/channels"

    async with httpx.AsyncClient() as client:
        response = await client.get(
            base_url,
            params={
                "part": "snippet,statistics,brandingSettings",
                "forHandle": f"@{handle}",
                "key": api_key,
            },
        )

        data = response.json()

        if not data.get("items"):
            return None

        channel = data["items"][0]
        snippet = channel.get("snippet", {})
        statistics = channel.get("statistics", {})

        followers = int(statistics.get("subscriberCount", 0))

        creator_info = CreatorInfo(
            platform=Platform.YOUTUBE,
            externalId=channel["id"],
            username=f"@{handle}",
            displayName=snippet.get("title", handle),
            profileUrl=f"https://www.youtube.com/@{handle}",
            avatarUrl=snippet.get("thumbnails", {}).get("high", {}).get("url"),
            followers=followers,
            bio=snippet.get("description"),
        )

        return IngestPayload(
            creator=creator_info,
            recentPosts=[],  # Would need another API call to activities/search endpoint to get recent videos
            aiEnrichment=None,  # To be integrated with Gemini
            source="YOUTUBE_API_V3",
        )
