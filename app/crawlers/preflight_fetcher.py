import json
import os
import re
import urllib.parse
from datetime import UTC, datetime
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from app.ai.rules.signal_extractor import PostPreview, ProfileMetadata
from app.core.logger import setup_logger
from app.crawlers.tiktok_crawler import _init_playwright_context

logger = setup_logger("preflight_fetcher")


def fetch_tiktok_preflight(username: str) -> tuple[ProfileMetadata, list[PostPreview]] | None:
    """
    Lightweight Preflight Fetcher cho TikTok:
    - Chỉ tải DOM (`domcontentloaded`) với timeout ngắn (8 giây).
    - Parse thẻ <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"> chứa dữ liệu SSR.
    - TUYỆT ĐỐI KHÔNG: Cuộn trang, không mở network interceptor, không sleep(10-30s).
    - Thời gian thực thi: ~1.5 - 3.0 giây / candidate.
    """
    username = username.strip().removeprefix("@")
    if "tiktok.com" in username:
        match = re.search(r"tiktok\.com/@?([^/?&#]+)", username)
        if match:
            username = match.group(1)

    url = f"https://www.tiktok.com/@{username}"

    with sync_playwright() as p:
        context, _ = _init_playwright_context(p)
        page = context.new_page()
        stealth = Stealth()
        stealth.apply_stealth_sync(page)

        try:
            # Tải nhanh chỉ chờ DOM, không chờ load toàn bộ ảnh/video
            page.goto(url, wait_until="domcontentloaded", timeout=12000)

            # Chờ script rehydration xuất hiện
            try:
                page.wait_for_selector("#__UNIVERSAL_DATA_FOR_REHYDRATION__", timeout=5000)
            except Exception:
                pass

            content = page.content()
        except Exception as e:
            logger.warning(f"[PREFLIGHT] Không thể tải trang TikTok cho @{username}: {e}")
            return None
        finally:
            context.browser.close()

    # Bóc tách DOM bằng BeautifulSoup
    soup = BeautifulSoup(content, "html.parser")
    script_tag = soup.find("script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__")
    if not script_tag or not script_tag.string:
        logger.warning(f"[PREFLIGHT] Không tìm thấy __UNIVERSAL_DATA_FOR_REHYDRATION__ cho @{username}")
        return None

    try:
        data = json.loads(script_tag.string)
    except Exception as e:
        logger.error(f"[PREFLIGHT] Lỗi parse JSON SSR cho @{username}: {e}")
        return None

    user_module = data.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {}).get("userInfo", {})
    user_info = user_module.get("user", {})
    stats_module = user_module.get("stats", {})

    if not user_info:
        logger.warning(f"[PREFLIGHT] Không tìm thấy user_info trong SSR cho @{username}")
        return None

    external_id = str(user_info.get("id", ""))
    followers = int(stats_module.get("followerCount", 0))
    signature = user_info.get("signature", "")
    display_name = user_info.get("nickname", username)

    bio_link = ""
    if "bioLink" in user_info and "link" in user_info["bioLink"]:
        bio_link = user_info["bioLink"]["link"]
        if "target=" in bio_link:
            try:
                raw_target = bio_link.split("target=")[1].split("&")[0]
                bio_link = urllib.parse.unquote(raw_target)
            except Exception:
                pass

    full_bio = f"{signature} {bio_link}".strip()
    email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", full_bio)
    public_email = email_match.group(0) if email_match else None

    phone_match = re.search(r"(?:\+?84|0)(?:\s|\.)?\d{2,3}(?:\s|\.)?\d{3}(?:\s|\.)?\d{3,4}", full_bio)
    public_phone = phone_match.group(0) if phone_match else None

    bio_links = []
    if bio_link:
        bio_links.append(bio_link)
    for l in re.findall(r"https?://[^\s\"'>]+", full_bio):
        if l not in bio_links:
            bio_links.append(l)

    profile = ProfileMetadata(
        platform="TIKTOK",
        external_id=external_id,
        username=f"@{username}",
        display_name=display_name,
        bio=full_bio,
        followers=followers,
        bio_links=bio_links,
        public_email=public_email,
        public_phone=public_phone,
        is_verified=bool(user_info.get("verified", False)),
    )

    # Bóc tách 5 bài đăng đầu tiên từ mảng itemList trong SSR (nếu có)
    posts: list[PostPreview] = []
    item_list = data.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {}).get("itemList", [])
    if not item_list:
        # Thử tìm trong các scope khác của SSR
        item_list = data.get("itemList", [])

    for item in item_list[:5]:
        pid = str(item.get("id", ""))
        caption = item.get("desc", "")
        stats = item.get("stats", {})
        create_time = item.get("createTime")
        posted_at = None
        if create_time:
            try:
                dt = datetime.fromtimestamp(int(create_time), tz=UTC)
                posted_at = dt.isoformat()
            except Exception:
                pass

        posts.append(PostPreview(
            post_id=pid,
            caption=caption,
            created_at=posted_at,
            views=int(stats.get("playCount", 0)),
            likes=int(stats.get("diggCount", 0)),
        ))

    return profile, posts


def fetch_facebook_preflight(username: str) -> tuple[ProfileMetadata, list[PostPreview]] | None:
    """Lightweight Preflight cho Facebook Fanpage (chỉ bóc tách OpenGraph tags & 3 bài đầu)."""
    clean_user = username.removeprefix("@").strip()
    url = f"https://www.facebook.com/{clean_user}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=12000)
            content = page.content()
        except Exception as e:
            logger.warning(f"[PREFLIGHT] Không thể tải FB {username}: {e}")
            return None
        finally:
            browser.close()

    soup = BeautifulSoup(content, "html.parser")
    og_title = soup.find("meta", property="og:title")
    og_desc = soup.find("meta", property="og:description")

    title_val = og_title.get("content", "") if og_title else clean_user
    desc_val = og_desc.get("content", "") if og_desc else ""

    profile = ProfileMetadata(
        platform="FACEBOOK",
        external_id=clean_user,
        username=clean_user,
        display_name=title_val,
        bio=desc_val,
        followers=0,
    )
    return profile, []


def fetch_instagram_preflight(username: str) -> tuple[ProfileMetadata, list[PostPreview]] | None:
    """Lightweight Preflight cho Instagram (chỉ bóc tách meta description)."""
    clean_user = username.removeprefix("@").strip()
    url = f"https://www.instagram.com/{clean_user}/"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=12000)
            content = page.content()
        except Exception as e:
            logger.warning(f"[PREFLIGHT] Không thể tải Instagram @{username}: {e}")
            return None
        finally:
            browser.close()

    soup = BeautifulSoup(content, "html.parser")
    og_desc = soup.find("meta", property="og:description")
    desc_val = og_desc.get("content", "") if og_desc else ""

    followers = 0
    f_match = re.search(r"([\d.,]+[KMBkmb]?)\s+Followers", desc_val)
    if f_match:
        from app.crawlers.search_extractor import parse_follower_string
        followers = parse_follower_string(f_match.group(1))

    profile = ProfileMetadata(
        platform="INSTAGRAM",
        external_id=clean_user,
        username=f"@{clean_user}",
        display_name=clean_user,
        bio=desc_val,
        followers=followers,
    )
    return profile, []
