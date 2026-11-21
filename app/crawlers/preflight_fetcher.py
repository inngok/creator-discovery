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
from app.crawlers.facebook_crawler import (
    _init_facebook_playwright_context,
    dismiss_facebook_dialogs,
    extract_facebook_followers,
    extract_facebook_posts,
)
from app.crawlers.instagram_crawler import (
    _init_instagram_playwright_context,
    extract_instagram_posts_from_soup,
)
from app.crawlers.tiktok_crawler import _init_playwright_context

logger = setup_logger("preflight_fetcher")


def fetch_tiktok_ssr_raw_data(username: str) -> dict | None:
    """
    Lightweight SSR Fetcher chung cho TikTok:
    - Tải DOM (`domcontentloaded`) với timeout ngắn.
    - Đón bắt đồng bộ API post/item_list để lấy danh sách bài đăng gần nhất.
    - Dự phòng bóc tách link video từ DOM nếu API bị chặn.
    - Chờ thẻ <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"> chứa dữ liệu SSR profile.
    - Trả về dictionary dữ liệu SSR đã được parse kèm mảng itemList video.
    """
    clean_user = username.strip().removeprefix("@")
    if "tiktok.com" in clean_user:
        match = re.search(r"tiktok\.com/@?([^/?&#]+)", clean_user)
        if match:
            clean_user = match.group(1)

    url = f"https://www.tiktok.com/@{clean_user}"
    captured_posts: list[dict] = []

    with sync_playwright() as p:
        context, _ = _init_playwright_context(p)
        page = context.new_page()
        stealth = Stealth()
        stealth.apply_stealth_sync(page)

        def handle_response(res):
            if "post/item_list" in res.url and res.status == 200:
                try:
                    text = res.text()
                    if text and text.strip().startswith("{"):
                        body = json.loads(text)
                        items = body.get("itemList", [])
                        for item in items:
                            author = item.get("author") or {}
                            author_name = author.get("uniqueId", "") if isinstance(author, dict) else ""
                            if author_name and author_name.casefold() != clean_user.casefold():
                                continue
                            if (item.get("id") and len(captured_posts) < 10
                                    and not any(str(p.get("id")) == str(item["id"]) for p in captured_posts)):
                                captured_posts.append(item)
                except Exception:
                    pass

        page.on("response", handle_response)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            try:
                page.wait_for_selector("#__UNIVERSAL_DATA_FOR_REHYDRATION__", timeout=5000)
            except Exception:
                pass

            # Cuộn xuống nhẹ để kích hoạt tải lazy video grid và gọi API post/item_list
            try:
                page.evaluate("window.scrollBy(0, 800);")
            except Exception:
                pass

            # Chờ tối đa 3s để API post/item_list kịp phản hồi
            for _ in range(6):
                if len(captured_posts) >= 10:
                    break
                page.wait_for_timeout(500)

            content = page.content()
        except Exception as e:
            logger.warning(f"[PREFLIGHT] Không thể tải trang TikTok cho @{clean_user}: {e}")
            return None
        finally:
            context.browser.close()

    soup = BeautifulSoup(content, "html.parser")
    script_tag = soup.find("script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__")
    if not script_tag or not script_tag.string:
        logger.warning(f"[PREFLIGHT] Không tìm thấy __UNIVERSAL_DATA_FOR_REHYDRATION__ cho @{clean_user}")
        return None

    try:
        data = json.loads(script_tag.string)
    except Exception as e:
        logger.error(f"[PREFLIGHT] Lỗi parse JSON SSR cho @{clean_user}: {e}")
        return None

    # Dự phòng bóc video trực tiếp từ DOM nếu API post/item_list chưa bắt được
    # Preserve SSR posts and supplement partial API responses with owned DOM cards.
    ssr_posts = data.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {}).get("itemList", []) or data.get("itemList", [])
    for item in ssr_posts:
        if item.get("id") and not any(str(p.get("id")) == str(item["id"]) for p in captured_posts):
            captured_posts.append(item)
    if len(captured_posts) < 10:
        for a in soup.select("a[href*='/video/'], a[href*='/photo/']"):
            if len(captured_posts) >= 10:
                break
            href = a.get("href", "")
            if not re.search(r"/@" + re.escape(clean_user) + r"/(?:video|photo)/", href, re.IGNORECASE):
                continue
            text = a.get_text(strip=True)
            img = a.find("img")
            alt = img.get("alt", "") if img else ""
            desc = text or alt
            pattern = "/video/" if "/video/" in href else "/photo/"
            pid = href.split(pattern)[-1].split("?")[0] if pattern in href else ""
            if pid and not any(str(p.get("id")) == pid for p in captured_posts):
                captured_posts.append({
                    "id": pid,
                    "desc": desc,
                    "stats": {"playCount": 0, "diggCount": 0, "commentCount": 0, "shareCount": 0},
                    "video": {"cover": img.get("src", "") if img else ""},
                })

    if captured_posts:
        data["itemList"] = captured_posts
        if "__DEFAULT_SCOPE__" in data and "webapp.user-detail" in data["__DEFAULT_SCOPE__"]:
            data["__DEFAULT_SCOPE__"]["webapp.user-detail"]["itemList"] = captured_posts

    return data


def fetch_tiktok_author_info(username: str) -> dict | None:
    """
    Lightweight Preflight SSR Fetcher để bóc Author ID (dãy số cố định) của kênh TikTok:
    - Phục vụ đối soát danh tính KOC khi KOC đổi username (ví dụ: mixigaming -> domixi)
      trước khi xác thực qua TikTok OAuth.
    - Trả về Author ID cố định (external_id), uniqueId, nickname, avatar, followerCount, secUid, v.v.
    """
    data = fetch_tiktok_ssr_raw_data(username)
    if not data:
        return None

    user_module = data.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {}).get("userInfo", {})
    user_info = user_module.get("user", {})
    stats_module = user_module.get("stats", {})

    if not user_info:
        clean_user = username.strip().removeprefix("@")
        logger.warning(f"[PREFLIGHT] Không tìm thấy user_info trong SSR cho @{clean_user}")
        return None

    external_id = str(user_info.get("id", "")).strip()
    unique_id = str(user_info.get("uniqueId", username.strip().removeprefix("@"))).strip()
    display_name = user_info.get("nickname", unique_id)
    sec_uid = str(user_info.get("secUid", "")).strip()
    followers = int(stats_module.get("followerCount", 0))
    following = int(stats_module.get("followingCount", 0))
    heart_count = int(stats_module.get("heartCount", 0))
    video_count = int(stats_module.get("videoCount", 0))
    signature = user_info.get("signature", "")
    avatar_url = (
        user_info.get("avatarLarger")
        or user_info.get("avatarMedium")
        or user_info.get("avatarThumb")
        or ""
    )

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

    return {
        "author_id": external_id,
        "unique_id": unique_id,
        "display_name": display_name,
        "sec_uid": sec_uid,
        "avatar_url": avatar_url,
        "followers": followers,
        "following": following,
        "heart_count": heart_count,
        "video_count": video_count,
        "signature": signature,
        "bio": full_bio,
        "bio_link": bio_link,
        "is_verified": bool(user_info.get("verified", False)),
        "profile_url": f"https://www.tiktok.com/@{unique_id}",
    }


def fetch_tiktok_preflight(username: str) -> tuple[ProfileMetadata, list[PostPreview]] | None:
    """
    Lightweight Preflight Fetcher cho TikTok:
    - Tải DOM (`domcontentloaded`) với timeout ngắn và parse thẻ __UNIVERSAL_DATA_FOR_REHYDRATION__.
    - Bóc tách ProfileMetadata và 5 bài đăng gần nhất cho Rule Engine.
    """
    clean_user = username.strip().removeprefix("@")
    if "tiktok.com" in clean_user:
        match = re.search(r"tiktok\.com/@?([^/?&#]+)", clean_user)
        if match:
            clean_user = match.group(1)

    data = fetch_tiktok_ssr_raw_data(clean_user)
    if not data:
        return None

    user_module = data.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {}).get("userInfo", {})
    user_info = user_module.get("user", {})
    stats_module = user_module.get("stats", {})

    if not user_info:
        logger.warning(f"[PREFLIGHT] Không tìm thấy user_info trong SSR cho @{clean_user}")
        return None

    external_id = str(user_info.get("id", ""))
    followers = int(stats_module.get("followerCount", 0))
    signature = user_info.get("signature", "")
    display_name = user_info.get("nickname", clean_user)

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
        username=f"@{clean_user}",
        display_name=display_name,
        bio=full_bio,
        followers=followers,
        bio_links=bio_links,
        public_email=public_email,
        public_phone=public_phone,
        is_verified=bool(user_info.get("verified", False)),
    )

    # Bóc tách tối đa 10 bài đăng đầu tiên từ mảng itemList trong SSR (nếu có)
    posts: list[PostPreview] = []
    item_list = data.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {}).get("itemList", [])
    if not item_list:
        # Thử tìm trong các scope khác của SSR
        item_list = data.get("itemList", [])

    for item in item_list[:10]:
        pid = str(item.get("id", ""))
        caption = item.get("desc", "")
        stats = item.get("stats", {}) or {}
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
            views=int(stats.get("playCount", 0) or 0),
            likes=int(stats.get("diggCount", 0) or 0),
        ))

    return profile, posts


def fetch_facebook_preflight(username: str) -> tuple[ProfileMetadata, list[PostPreview]] | None:
    """Lightweight Preflight cho Facebook Fanpage (Bóc tách Profile & 3 bài đăng gần nhất qua DOM/Meta)."""
    clean_user = username.removeprefix("@").strip()
    url = f"https://www.facebook.com/{clean_user}"

    with sync_playwright() as p:
        context, _ = _init_facebook_playwright_context(p)
        page = context.new_page()
        stealth = Stealth()
        stealth.apply_stealth_sync(page)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=12000)
            page.wait_for_timeout(2000)
            dismiss_facebook_dialogs(page)
            content = page.content()
        except Exception as e:
            logger.warning(f"[PREFLIGHT] Không thể tải FB {username}: {e}")
            return None
        finally:
            context.browser.close()

    soup = BeautifulSoup(content, "html.parser")
    og_title = soup.find("meta", property="og:title")
    og_desc = soup.find("meta", property="og:description")
    meta_desc = soup.find("meta", attrs={"name": "description"})

    title_val = og_title.get("content", "") if og_title else clean_user
    desc_val = (og_desc.get("content") if og_desc else "") or (
        meta_desc.get("content") if meta_desc else ""
    )

    page_text = soup.get_text(separator=" ")
    followers = extract_facebook_followers(desc_val, page_text)

    # Bóc tách ít nhất 3 bài đăng gần nhất cho Rule Engine thẩm định KOC
    raw_posts = extract_facebook_posts(soup, clean_user, max_posts=3)
    post_previews: list[PostPreview] = [
        PostPreview(
            post_id=p.platformPostId,
            caption=p.caption,
            created_at=p.postedAt,
            views=p.views,
            likes=p.likes,
        )
        for p in raw_posts
    ]

    profile = ProfileMetadata(
        platform="FACEBOOK",
        external_id=clean_user,
        username=clean_user,
        display_name=title_val,
        bio=desc_val,
        followers=followers,
    )
    return profile, post_previews


def fetch_instagram_preflight(username: str) -> tuple[ProfileMetadata, list[PostPreview]] | None:
    """Lightweight Preflight cho Instagram (Bóc tách Profile & 3 bài đăng gần nhất từ script nhúng)."""
    clean_user = username.removeprefix("@").strip()
    url = f"https://www.instagram.com/{clean_user}/"

    with sync_playwright() as p:
        context, _ = _init_instagram_playwright_context(p)
        page = context.new_page()
        stealth = Stealth()
        stealth.apply_stealth_sync(page)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=12000)
            page.wait_for_timeout(1500)
            content = page.content()
        except Exception as e:
            logger.warning(f"[PREFLIGHT] Không thể tải Instagram @{username}: {e}")
            return None
        finally:
            context.browser.close()

    soup = BeautifulSoup(content, "html.parser")
    og_desc = soup.find("meta", property="og:description")
    og_title = soup.find("meta", property="og:title")
    meta_desc = soup.find("meta", attrs={"name": "description"})

    desc_val = (og_desc.get("content") if og_desc else "") or (
        meta_desc.get("content") if meta_desc else ""
    )
    title_val = og_title.get("content", "") if og_title else clean_user
    dn_match = re.match(r"^(.*?)\s*\(@", title_val)
    display_name = dn_match.group(1).strip() if dn_match else clean_user

    followers = 0
    f_match = re.search(r"([\d.,]+[KMBkmb]?)\s+Followers", desc_val, re.IGNORECASE)
    if f_match:
        from app.crawlers.search_extractor import parse_follower_string
        followers = parse_follower_string(f_match.group(1))

    # Bóc tách 3 bài đăng gần nhất trực tiếp từ script GraphQL của Instagram
    raw_posts, _ = extract_instagram_posts_from_soup(soup, max_posts=3)
    post_previews: list[PostPreview] = [
        PostPreview(
            post_id=p.platformPostId,
            caption=p.caption,
            created_at=p.postedAt,
            views=p.views,
            likes=p.likes,
        )
        for p in raw_posts
    ]

    profile = ProfileMetadata(
        platform="INSTAGRAM",
        external_id=clean_user,
        username=f"@{clean_user}",
        display_name=display_name,
        bio=desc_val,
        followers=followers,
    )
    return profile, post_previews
