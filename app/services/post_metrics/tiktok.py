import json
import re
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any, Optional

from bs4 import BeautifulSoup

from app.core.logger import setup_logger
from app.core.post_metrics_models import MetricStatusEnum
from app.crawlers.search_extractor import parse_follower_string
from app.services.post_metrics.builder import build_success_payload
from app.services.post_metrics.url_validator import PostMetricsException

logger = setup_logger("tiktok_post_tracker")
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _fetch_oembed(url: str) -> dict[str, Any]:
    try:
        oembed_url = f"https://www.tiktok.com/oembed?url={urllib.parse.quote(url)}"
        req = urllib.request.Request(oembed_url, headers={"User-Agent": DEFAULT_USER_AGENT})
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise PostMetricsException("POST_NOT_FOUND", "Bài đăng TikTok không tồn tại hoặc đã bị xóa", 404)
    except Exception as e:
        logger.debug(f"[TRACKER] TikTok oEmbed failed: {e}")
    return {}


def _fetch_playwright_content(canonical_url: str) -> str:
    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth
        from app.crawlers.tiktok_crawler import _init_playwright_context

        with sync_playwright() as p:
            context, _ = _init_playwright_context(p)
            page = context.new_page()
            Stealth().apply_stealth_sync(page)
            page.goto(canonical_url, wait_until="domcontentloaded", timeout=15000)
            try:
                page.wait_for_selector('[data-e2e="browse-like-count"], strong[data-e2e="like-count"]', timeout=3000)
            except Exception:
                pass
            content = page.content()
            context.browser.close()
            return content
    except Exception as e:
        logger.debug(f"[TRACKER] Playwright TikTok error: {e}")
        return ""


def fetch_tiktok_post(url: str, canonical_url: str, post_id: str, author_hint: str) -> dict[str, Any]:
    """Cào số liệu bài đăng TikTok thời gian thực."""
    oembed_data = _fetch_oembed(url)
    username = oembed_data.get("author_unique_id") or author_hint
    display_name = oembed_data.get("author_name") or username
    caption = oembed_data.get("title") or ""
    thumbnail = oembed_data.get("thumbnail_url") or ""
    posted_at = None
    external_id = None

    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    views_status = MetricStatusEnum.FETCH_FAILED
    likes_status = MetricStatusEnum.FETCH_FAILED
    comments_status = MetricStatusEnum.FETCH_FAILED
    shares_status = MetricStatusEnum.FETCH_FAILED
    method = "SSR"

    # 1. Thử HTTP GET nhẹ trước
    content = ""
    try:
        fetch_req = urllib.request.Request(
            canonical_url,
            headers={"User-Agent": DEFAULT_USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        )
        with urllib.request.urlopen(fetch_req, timeout=6) as resp:
            if resp.status == 200:
                content = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise PostMetricsException("POST_NOT_FOUND", "Bài đăng TikTok không tồn tại", 404)
        if e.code == 429:
            raise PostMetricsException("RATE_LIMITED", "Bị giới hạn tần suất truy cập từ TikTok", 429, retryable=True, retryAfterSeconds=30)
    except Exception:
        pass

    # 2. Nếu thiếu content hoặc không có Universal Data, dùng Playwright với cookies & anti-bot stealth
    if not content or "__UNIVERSAL_DATA_FOR_REHYDRATION__" not in content:
        pw_content = _fetch_playwright_content(canonical_url)
        if pw_content:
            content = pw_content
            method = "PLAYWRIGHT"

    # 3. Phân tích Universal Data
    rehydrate_match = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', content)
    if rehydrate_match:
        try:
            data = json.loads(rehydrate_match.group(1))
            vd = data.get("__DEFAULT_SCOPE__", {}).get("webapp.video-detail", {})
            if vd.get("statusCode") in (10204, 10205) or vd.get("statusMsg") == "item doesn't exist":
                raise PostMetricsException("POST_NOT_FOUND", "Bài đăng TikTok không tồn tại hoặc đã bị xóa", 404)

            item_struct = vd.get("itemInfo", {}).get("itemStruct", {})
            if item_struct:
                stats = item_struct.get("stats", {})
                if "playCount" in stats:
                    views, views_status = int(stats["playCount"]), MetricStatusEnum.AVAILABLE
                if "diggCount" in stats:
                    likes, likes_status = int(stats["diggCount"]), MetricStatusEnum.AVAILABLE
                if "commentCount" in stats:
                    comments, comments_status = int(stats["commentCount"]), MetricStatusEnum.AVAILABLE
                if "shareCount" in stats:
                    shares, shares_status = int(stats["shareCount"]), MetricStatusEnum.AVAILABLE

                caption = item_struct.get("desc") or caption
                author_obj = item_struct.get("author", {})
                username = author_obj.get("uniqueId") or username
                display_name = author_obj.get("nickname") or display_name
                external_id = author_obj.get("id") or None
                video_info = item_struct.get("video", {})
                thumbnail = video_info.get("cover") or video_info.get("originCover") or thumbnail
                if item_struct.get("createTime"):
                    posted_at = datetime.fromtimestamp(int(item_struct["createTime"]), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        except PostMetricsException:
            raise
        except Exception as parse_err:
            logger.debug(f"[TRACKER] TikTok JSON parse error: {parse_err}")

    # 4. Fallback DOM nếu Universal Data không đủ
    if content and (views_status != MetricStatusEnum.AVAILABLE or likes_status != MetricStatusEnum.AVAILABLE):
        soup = BeautifulSoup(content, "html.parser")
        for sel, field in (
            ('[data-e2e="browse-like-count"], [data-e2e="like-count"]', "likes"),
            ('[data-e2e="browse-comment-count"], [data-e2e="comment-count"]', "comments"),
            ('[data-e2e="browse-share-count"], [data-e2e="share-count"]', "shares"),
        ):
            elem = soup.select_one(sel)
            if elem:
                val = parse_follower_string(elem.get_text())
                if field == "likes":
                    likes, likes_status = val, MetricStatusEnum.AVAILABLE
                elif field == "comments":
                    comments, comments_status = val, MetricStatusEnum.AVAILABLE
                elif field == "shares":
                    shares, shares_status = val, MetricStatusEnum.AVAILABLE

    return build_success_payload(
        platform="TIKTOK",
        post_url=url,
        canonical_post_url=canonical_url,
        post_id=post_id,
        author={"username": username, "displayName": display_name, "externalId": external_id},
        views=views,
        likes=likes,
        comments=comments,
        shares=shares,
        views_status=views_status,
        likes_status=likes_status,
        comments_status=comments_status,
        shares_status=shares_status,
        caption=caption,
        thumbnail_url=thumbnail,
        posted_at=posted_at,
        method=method,
    )
