import asyncio
import json
import os
import random
import re
import time
import urllib.parse
from datetime import UTC, datetime

from bs4 import BeautifulSoup
from playwright.sync_api import (
    BrowserContext,
    Page,
    sync_playwright,
)
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
)
from playwright_stealth import Stealth

from app.core.logger import setup_logger
from app.core.post_dates import normalize_post_date
from app.core.models import CreatorInfo, IngestPayload, Platform, Post

logger = setup_logger("tiktok_crawler")


def _init_playwright_context(p) -> tuple[BrowserContext, None]:
    from app.crawlers.tiktok_session import create_tiktok_context
    return create_tiktok_context(p), None


def _setup_network_interception(page: Page, username: str) -> list[Post]:
    recent_posts: list[Post] = []
    seen_post_ids: set[str] = set()

    def handle_response(response):
        if (
            "api" in response.url
            and "post/item_list" in response.url
            and response.status == 200
        ):
            try:
                text = response.text()
                if not text:
                    return
                body = json.loads(text)
                itemList = body.get("itemList", [])
                for v in itemList:
                    if len(recent_posts) >= 12:
                        break
                    post_id = str(v.get("id", ""))
                    author = v.get("author") or {}
                    author_name = author.get("uniqueId", "") if isinstance(author, dict) else ""
                    if author_name and author_name.casefold() != username.lstrip("@").casefold():
                        continue
                    if not post_id or post_id in seen_post_ids:
                        continue
                    seen_post_ids.add(post_id)
                    title = v.get("desc", "")

                    create_time = v.get("createTime")
                    posted_at = normalize_post_date(create_time)

                    stats = v.get("stats", {})
                    recent_posts.append(
                        Post(
                            platformPostId=post_id,
                            postUrl=f"https://www.tiktok.com/@{username}/video/{post_id}",
                            caption=title,
                            views=int(stats.get("playCount", 0)),
                            likes=int(stats.get("diggCount", 0)),
                            comments=int(stats.get("commentCount", 0)),
                            shares=int(stats.get("shareCount", 0)),
                            postedAt=posted_at,
                        )
                    )
            except Exception as e:
                logger.warning(f"Error parsing intercept response: {e}")

    page.on("response", handle_response)
    return recent_posts


def _extract_user_info_from_dom(page: Page, username: str, url: str) -> CreatorInfo:
    content = page.content()
    soup = BeautifulSoup(content, "html.parser")
    script_tag = soup.find("script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__")

    if not script_tag:
        raise ValueError(
            "script_tag __UNIVERSAL_DATA_FOR_REHYDRATION__ not found in page source. Possibly blocked by TikTok CAPTCHA."
        )

    raw_data = script_tag.string
    if not raw_data:
        raise ValueError("script_tag found but contains no data.")
    data = json.loads(raw_data)

    if not os.path.exists("data"):
        os.makedirs("data")
    with open("data/dump.json", "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2))

    user_module = (
        data.get("__DEFAULT_SCOPE__", {})
        .get("webapp.user-detail", {})
        .get("userInfo", {})
    )
    stats_module = user_module.get("stats", {})
    user_info = user_module.get("user", {})

    if not user_info:
        raise ValueError(
            "user_info not found in __DEFAULT_SCOPE__.webapp.user-detail.userInfo"
        )

    external_id = str(user_info.get("id", ""))
    followers = int(stats_module.get("followerCount", 0))

    if followers < 7000:
        logger.warning(
            f"Skipping {username}: only {followers} followers (needs >= 7000)"
        )
        raise ValueError(
            f"Skipping {username}: only {followers} followers (needs >= 7000)"
        )

    signature = user_info.get("signature", "")

    bio_link = ""
    if "bioLink" in user_info and "link" in user_info["bioLink"]:
        bio_link = user_info["bioLink"]["link"]
        if "target=" in bio_link:
            try:
                raw_target = bio_link.split("target=")[1].split("&")[0]
                bio_link = urllib.parse.unquote(raw_target)
            except Exception as e:
                logger.warning(f"Failed to unquote bioLink target: {e}")

    full_bio = f"{signature} {bio_link}".strip()

    email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", full_bio)
    public_email = email_match.group(0) if email_match else None

    phone_match = re.search(
        r"(?:\+?84|0)(?:\s|\.)?\d{2,3}(?:\s|\.)?\d{3}(?:\s|\.)?\d{3,4}", full_bio
    )
    public_phone = phone_match.group(0) if phone_match else None

    cross_links = re.findall(r"https?://[^\s\"'>]+", full_bio) if full_bio else []
    if bio_link and bio_link not in cross_links:
        cross_links.append(bio_link)

    return CreatorInfo(
        platform=Platform.TIKTOK,
        externalId=external_id,
        isStableExternalId=True,  # TikTok authorId là dãy số cố định không bao giờ đổi
        username=f"@{username}",
        displayName=user_info.get("nickname", username),
        profileUrl=url,
        avatarUrl=user_info.get("avatarLarger", user_info.get("avatarMedium")),
        followers=followers,
        bio=full_bio,
        publicEmail=public_email,
        publicPhone=public_phone,
        bioCrossLinks=cross_links,
    )


def _crawl_tiktok_profile_sync(username: str) -> IngestPayload | None:
    username = username.strip()
    if "tiktok.com" in username:
        match = re.search(r"tiktok\.com/@?([^/?&#]+)", username)
        if match:
            username = match.group(1)

    username = username.removeprefix("@")

    url = f"https://www.tiktok.com/@{username}"

    with sync_playwright() as p:
        context, state_path = _init_playwright_context(p)
        page = context.new_page()
        stealth = Stealth()
        stealth.apply_stealth_sync(page)

        recent_posts = _setup_network_interception(page, username)
        creator_info = None

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

            try:
                page.wait_for_selector(
                    '#__UNIVERSAL_DATA_FOR_REHYDRATION__, [data-e2e="user-title"]',
                    timeout=10000,
                )
            except PlaywrightTimeoutError:
                logger.warning(
                    f"Timeout waiting for universal data selector for {username}"
                )

            # Scroll để trigger fetch list video API
            for _ in range(5):
                if len(recent_posts) >= 12:
                    break
                scroll_amount = random.randint(1000, 1800)
                page.evaluate(f"window.scrollBy(0, {scroll_amount});")
                page.wait_for_timeout(1000)

            # Dự phòng bóc trực tiếp từ DOM nếu network interception chưa kịp lấy
            if len(recent_posts) < 12:
                content = page.content()
                soup = BeautifulSoup(content, "html.parser")
                for a in soup.select("a[href*='/video/']"):
                    if len(recent_posts) >= 12:
                        break
                    href = a.get("href", "")
                    if not re.search(r"/@" + re.escape(username) + r"/video/", href, re.IGNORECASE):
                        continue
                    text = a.get_text(strip=True)
                    img = a.find("img")
                    alt = img.get("alt", "") if img else ""
                    desc = text or alt
                    pid = href.split("/video/")[-1].split("?")[0] if "/video/" in href else ""
                    if pid and not any(p.platformPostId == pid for p in recent_posts):
                        recent_posts.append(
                            Post(
                                platformPostId=pid,
                                postUrl=f"https://www.tiktok.com/@{username}/video/{pid}",
                                caption=desc,
                                views=0,
                                likes=0,
                                comments=0,
                                shares=0,
                                postedAt=None,
                            )
                        )

            creator_info = _extract_user_info_from_dom(page, username, url)

        finally:
            context.browser.close()

        time.sleep(random.uniform(10.0, 30.0))

    if not recent_posts:
        logger.info(
            f"No posts in Network Interception for {username}. Might be blocked or no posts exist."
        )
        if not creator_info or not creator_info.bio or not creator_info.bio.strip():
            logger.warning(f"Skipping {username}: No posts and empty bio, skipping empty account.")
            return None
        logger.info("No recent videos found, AI will fallback to using bio only.")

    if not creator_info:
        return None

    # AI Enrichment đã được tách ra khỏi Crawler và được gọi ở Worker

    return IngestPayload(
        creator=creator_info,
        recentPosts=recent_posts,
        aiEnrichment=None,
        source="PLAYWRIGHT_STEALTH_TIKTOK",
    )


async def crawl_tiktok_profile(username: str) -> IngestPayload | None:
    return await asyncio.to_thread(_crawl_tiktok_profile_sync, username)
