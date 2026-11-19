import json
import re
import urllib.parse
from datetime import UTC, datetime
from typing import Any, Optional
import urllib.request

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from app.core.logger import setup_logger
from app.crawlers.search_extractor import parse_follower_string

logger = setup_logger("single_post_tracker")


class SinglePostTrackerService:
    """
    Dịch vụ cào thông số bài đăng đơn lẻ (Single Post Tracking):
    - Nhận URL bài viết cụ thể của TikTok, YouTube, Facebook, Instagram.
    - Tự động bóc tách: Views, Likes, Comments, Shares, Caption, Thumbnail, Tác giả.
    - Phục vụ việc theo dõi KPI & ROI chiến dịch (Campaign Performance Tracking).
    """

    def detect_platform(self, url: str) -> str:
        clean = (url or "").lower()
        if "tiktok.com" in clean:
            return "TIKTOK"
        if "youtube.com" in clean or "youtu.be" in clean:
            return "YOUTUBE"
        if "facebook.com" in clean or "fb.watch" in clean:
            return "FACEBOOK"
        if "instagram.com" in clean:
            return "INSTAGRAM"
        return "UNKNOWN"

    def fetch_post_metrics(self, post_url: str) -> dict[str, Any]:
        url = post_url.strip()
        platform = self.detect_platform(url)

        if platform == "UNKNOWN":
            return {
                "status": "error",
                "message": f"Không nhận diện được nền tảng từ URL: {url}. Hỗ trợ: TikTok, YouTube, Facebook, Instagram.",
            }

        if platform == "TIKTOK":
            return self._fetch_tiktok_post(url)
        if platform == "YOUTUBE":
            return self._fetch_youtube_post(url)
        if platform == "INSTAGRAM":
            return self._fetch_instagram_post(url)
        return self._fetch_facebook_post(url)

    def _fetch_tiktok_post(self, url: str) -> dict[str, Any]:
        """Cào số liệu bài đăng TikTok qua oEmbed + SSR nhẹ."""
        # 1. Trích xuất Post ID và Author từ URL
        match_id = re.search(r"/video/(\d+)", url)
        post_id = match_id.group(1) if match_id else ""
        match_author = re.search(r"/@([\w.]+)", url)
        username = match_author.group(1) if match_author else ""

        # Gọi oEmbed chính thức của TikTok (cực nhanh, không cần headless browser)
        oembed_data = {}
        try:
            oembed_url = f"https://www.tiktok.com/oembed?url={urllib.parse.quote(url)}"
            req = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    oembed_data = json.loads(resp.read().decode("utf-8"))
                    username = oembed_data.get("author_unique_id") or username
        except Exception as e:
            logger.debug(f"[TRACKER] TikTok oEmbed call failed: {e}")

        views = 0
        likes = 0
        comments = 0
        shares = 0
        caption = oembed_data.get("title", "")
        thumbnail = oembed_data.get("thumbnail_url", "")
        display_name = oembed_data.get("author_name", username)
        posted_at = ""

        # 2. Mở DOM nhẹ qua Playwright để lấy stats (views, likes, comments, shares)
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"],
                )
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    viewport={"width": 1280, "height": 800},
                )
                page = context.new_page()
                Stealth().apply_stealth_sync(page)

                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                try:
                    page.wait_for_selector('[data-e2e="browse-like-count"], strong[data-e2e="like-count"]', timeout=4000)
                except Exception:
                    pass

                content = page.content()

                # Parse Universal data nếu có
                rehydrate_match = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', content)
                if rehydrate_match:
                    try:
                        data = json.loads(rehydrate_match.group(1))
                        item_struct = (
                            data.get("__DEFAULT_SCOPE__", {})
                            .get("webapp.video-detail", {})
                            .get("itemInfo", {})
                            .get("itemStruct", {})
                        )
                        if item_struct:
                            stats = item_struct.get("stats", {})
                            views = int(stats.get("playCount") or 0)
                            likes = int(stats.get("diggCount") or 0)
                            comments = int(stats.get("commentCount") or 0)
                            shares = int(stats.get("shareCount") or 0)
                            caption = item_struct.get("desc") or caption
                            posted_at = (
                                datetime.fromtimestamp(int(item_struct.get("createTime", 0)), UTC).isoformat()
                                if item_struct.get("createTime")
                                else ""
                            )
                            author = item_struct.get("author", {})
                            username = author.get("uniqueId") or username
                            display_name = author.get("nickname") or display_name
                    except Exception as parse_err:
                        logger.debug(f"[TRACKER] Error parsing TikTok SSR JSON: {parse_err}")

                # Fallback parse HTML nếu JSON không đủ
                if views == 0 and likes == 0:
                    soup = BeautifulSoup(content, "html.parser")
                    like_elem = soup.select_one('[data-e2e="browse-like-count"], [data-e2e="like-count"]')
                    if like_elem:
                        likes = parse_follower_string(like_elem.get_text())
                    comment_elem = soup.select_one('[data-e2e="browse-comment-count"], [data-e2e="comment-count"]')
                    if comment_elem:
                        comments = parse_follower_string(comment_elem.get_text())
                    share_elem = soup.select_one('[data-e2e="browse-share-count"], [data-e2e="share-count"]')
                    if share_elem:
                        shares = parse_follower_string(share_elem.get_text())

                browser.close()
            except Exception as e:
                logger.warning(f"[TRACKER] Playwright TikTok scrape error: {e}")

        engagement_rate = round((likes + comments + shares) / max(views, 1) * 100, 2) if views > 0 else 0.0

        return {
            "status": "success",
            "platform": "TIKTOK",
            "postUrl": url,
            "postId": post_id,
            "author": {
                "username": username,
                "displayName": display_name,
            },
            "metrics": {
                "views": views,
                "likes": likes,
                "comments": comments,
                "shares": shares,
                "engagementRate": engagement_rate,
            },
            "content": {
                "caption": caption,
                "thumbnailUrl": thumbnail,
                "postedAt": posted_at,
            },
            "trackedAt": datetime.now(UTC).isoformat(),
        }

    def _fetch_youtube_post(self, url: str) -> dict[str, Any]:
        """Cào số liệu video / Short YouTube."""
        # 1. oEmbed YouTube
        oembed_data = {}
        try:
            oembed_url = f"https://www.youtube.com/oembed?url={urllib.parse.quote(url)}&format=json"
            req = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    oembed_data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            pass

        title = oembed_data.get("title", "")
        author_name = oembed_data.get("author_name", "")
        thumbnail = oembed_data.get("thumbnail_url", "")
        views = 0
        likes = 0
        comments = 0

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"],
                )
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                )
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2000)

                content = page.content()
                # Extract view count regex
                view_match = re.search(r'"viewCount":\s*"(\d+)"', content)
                if view_match:
                    views = int(view_match.group(1))

                like_match = re.search(r'"defaultText":\s*\{\s*"accessibility":\s*\{\s*"accessibilityData":\s*\{\s*"label":\s*"([\d.,]+)\s*likes?"', content, re.IGNORECASE)
                if like_match:
                    likes = parse_follower_string(like_match.group(1))

                browser.close()
            except Exception as e:
                logger.warning(f"[TRACKER] YouTube scrape error: {e}")

        return {
            "status": "success",
            "platform": "YOUTUBE",
            "postUrl": url,
            "postId": url.split("v=")[-1].split("&")[0] if "v=" in url else url.split("/")[-1],
            "author": {
                "username": author_name,
                "displayName": author_name,
            },
            "metrics": {
                "views": views,
                "likes": likes,
                "comments": comments,
                "shares": 0,
                "engagementRate": round((likes + comments) / max(views, 1) * 100, 2) if views > 0 else 0.0,
            },
            "content": {
                "caption": title,
                "thumbnailUrl": thumbnail,
                "postedAt": "",
            },
            "trackedAt": datetime.now(UTC).isoformat(),
        }

    def _fetch_instagram_post(self, url: str) -> dict[str, Any]:
        """Cào số liệu bài đăng / Reel Instagram."""
        views = 0
        likes = 0
        comments = 0
        caption = ""
        author = ""

        with sync_playwright() as p:
            try:
                from app.crawlers.instagram_crawler import _init_instagram_playwright_context
                context, _ = _init_instagram_playwright_context(p)
                page = context.new_page()
                Stealth().apply_stealth_sync(page)

                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2500)
                content = page.content()

                # Bóc tách metadata từ thẻ meta description
                soup = BeautifulSoup(content, "html.parser")
                meta_desc = soup.select_one('meta[property="og:description"], meta[name="description"]')
                if meta_desc and meta_desc.get("content"):
                    desc_text = meta_desc["content"]
                    # Format: "1,234 likes, 56 comments - Username on Instagram: 'Caption...'"
                    likes_m = re.search(r"([\d.,]+[kmb]?)\s*likes?", desc_text, re.IGNORECASE)
                    if likes_m:
                        likes = parse_follower_string(likes_m.group(1))
                    comm_m = re.search(r"([\d.,]+[kmb]?)\s*comments?", desc_text, re.IGNORECASE)
                    if comm_m:
                        comments = parse_follower_string(comm_m.group(1))
                    user_m = re.search(r"-\s*([a-zA-Z0-9._]+)\s*on Instagram", desc_text, re.IGNORECASE)
                    if user_m:
                        author = user_m.group(1)

                meta_title = soup.select_one('meta[property="og:title"]')
                if meta_title and meta_title.get("content"):
                    caption = meta_title["content"]

                browser = page.context.browser
                if browser:
                    browser.close()
            except Exception as e:
                logger.warning(f"[TRACKER] Instagram post scrape error: {e}")

        return {
            "status": "success",
            "platform": "INSTAGRAM",
            "postUrl": url,
            "postId": url.split("/")[-2] if url.endswith("/") else url.split("/")[-1],
            "author": {
                "username": author,
                "displayName": author,
            },
            "metrics": {
                "views": views or (likes * 10), # Ước lượng nếu reel ẩn views công khai
                "likes": likes,
                "comments": comments,
                "shares": 0,
                "engagementRate": 0.0,
            },
            "content": {
                "caption": caption,
                "thumbnailUrl": "",
                "postedAt": "",
            },
            "trackedAt": datetime.now(UTC).isoformat(),
        }

    def _fetch_facebook_post(self, url: str) -> dict[str, Any]:
        """Cào số liệu bài đăng / Reel Facebook."""
        likes = 0
        comments = 0
        shares = 0
        caption = ""

        with sync_playwright() as p:
            try:
                from app.crawlers.facebook_crawler import _init_facebook_playwright_context
                context, _ = _init_facebook_playwright_context(p)
                page = context.new_page()
                Stealth().apply_stealth_sync(page)

                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2000)

                content = page.content()
                soup = BeautifulSoup(content, "html.parser")

                meta_desc = soup.select_one('meta[name="description"], meta[property="og:description"]')
                if meta_desc and meta_desc.get("content"):
                    caption = meta_desc["content"]

                browser = page.context.browser
                if browser:
                    browser.close()
            except Exception as e:
                logger.warning(f"[TRACKER] Facebook post scrape error: {e}")

        return {
            "status": "success",
            "platform": "FACEBOOK",
            "postUrl": url,
            "postId": "",
            "author": {
                "username": "",
                "displayName": "",
            },
            "metrics": {
                "views": 0,
                "likes": likes,
                "comments": comments,
                "shares": shares,
                "engagementRate": 0.0,
            },
            "content": {
                "caption": caption,
                "thumbnailUrl": "",
                "postedAt": "",
            },
            "trackedAt": datetime.now(UTC).isoformat(),
        }


single_post_tracker = SinglePostTrackerService()
