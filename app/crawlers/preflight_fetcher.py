import json
import os
import re
import urllib.parse
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from app.ai.rules.signal_extractor import PostPreview, ProfileMetadata
from app.core.post_dates import normalize_post_date
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
_fast_discovery_mode: ContextVar[bool] = ContextVar("fast_discovery_mode", default=False)


@contextmanager
def fast_discovery_fetch():
    """Fetch only enough recent TikTok posts for discovery verification."""
    token = _fast_discovery_mode.set(True)
    try:
        yield
    finally:
        _fast_discovery_mode.reset(token)


def normalize_tiktok_item_list(items: list, username: str) -> list[dict]:
    """Deduplicate owned TikTok posts and put the newest dated posts first."""
    if not isinstance(items, list):
        return []
    clean_user = username.strip().removeprefix("@").casefold()
    by_id: dict[str, dict] = {}
    source_order: dict[str, int] = {}

    def merge_nonempty(existing: dict, incoming: dict) -> dict:
        merged = dict(existing)
        for key, value in incoming.items():
            previous = merged.get(key)
            if isinstance(previous, dict) and isinstance(value, dict):
                merged[key] = merge_nonempty(previous, value)
            elif (key not in merged or previous in (None, "", {})
                  or (previous == 0 and value not in (None, "", {}, 0))):
                merged[key] = value
        return merged

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        post_id = str(item.get("id", "")).strip()
        if not post_id:
            continue
        author = item.get("author") or {}
        author_name = author.get("uniqueId", "") if isinstance(author, dict) else ""
        if isinstance(author_name, str) and author_name and author_name.casefold() != clean_user:
            continue
        if post_id in by_id:
            by_id[post_id] = merge_nonempty(by_id[post_id], item)
        else:
            by_id[post_id] = dict(item)
            source_order[post_id] = index

    def order(entry: tuple[str, dict]) -> tuple[bool, float, int]:
        post_id, item = entry
        published = normalize_post_date(item.get("createTime") or item.get("postedAt"))
        if published:
            timestamp = datetime.fromisoformat(published.replace("Z", "+00:00")).timestamp()
            return False, -timestamp, source_order[post_id]
        return True, 0, source_order[post_id]

    return [item for _, item in sorted(by_id.items(), key=order)]


def fetch_tiktok_ssr_raw_data(
    username: str,
    max_posts: int | None = None,
    max_scrolls: int | None = None,
) -> dict | None:
    """
    Lightweight SSR Fetcher chung cho TikTok:
    - Tải DOM (`domcontentloaded`) với timeout ngắn.
    - Đón bắt đồng bộ API post/item_list để lấy danh sách bài đăng gần nhất.
    - Dự phòng bóc tách link video từ DOM nếu API bị chặn.
    - Chờ thẻ <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"> chứa dữ liệu SSR profile.
    - Trả về dictionary dữ liệu SSR đã được parse kèm mảng itemList video.
    """
    fast_mode = _fast_discovery_mode.get()
    target_posts = max_posts if max_posts is not None else (18 if fast_mode else 60)
    scroll_limit = max_scrolls if max_scrolls is not None else (2 if fast_mode else 8)
    clean_user = username.strip().removeprefix("@")
    if "tiktok.com" in clean_user:
        match = re.search(r"tiktok\.com/@?([^/?&#]+)", clean_user)
        if match:
            clean_user = match.group(1)

    url = f"https://www.tiktok.com/@{clean_user}"
    captured_posts: list[dict] = []
    captured_ids: set[str] = set()

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
                            if (isinstance(author_name, str) and author_name
                                    and author_name.casefold() != clean_user.casefold()):
                                continue
                            post_id = str(item.get("id", "")).strip()
                            if post_id and len(captured_posts) < target_posts and post_id not in captured_ids:
                                captured_posts.append(item)
                                captured_ids.add(post_id)
                except Exception as error:
                    logger.warning("[PREFLIGHT] TikTok post response parse failed: %s",
                                   type(error).__name__)

        page.on("response", handle_response)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            try:
                page.wait_for_selector("#__UNIVERSAL_DATA_FOR_REHYDRATION__", timeout=5000)
            except Exception:
                pass

            # Cuộn xuống từng đợt để kích hoạt lazy-load các hàng video tiếp theo (tránh chỉ dừng ở 3 bài ghim đầu)
            idle_scrolls = 0
            wait_steps = 4 if fast_mode else 6
            for _ in range(scroll_limit):
                if len(captured_posts) >= target_posts:
                    break
                previous_count = len(captured_posts)
                try:
                    page.evaluate("window.scrollBy(0, 1500);")
                except Exception:
                    break
                for _ in range(wait_steps):
                    page.wait_for_timeout(250)
                    if len(captured_posts) > previous_count:
                        break
                if len(captured_posts) == previous_count:
                    idle_scrolls += 1
                    if idle_scrolls >= (1 if fast_mode else 2):
                        break
                else:
                    idle_scrolls = 0

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

    # Dự phòng bóc video trực tiếp từ DOM nếu API post/item_list chưa bắt đủ
    ssr_posts = data.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {}).get("itemList", [])
    root_posts = data.get("itemList", [])
    if not isinstance(ssr_posts, list):
        ssr_posts = []
    if not isinstance(root_posts, list):
        root_posts = []
    dom_posts = []
    if len(captured_posts) < target_posts:
        for a in soup.select("a[href*='/video/'], a[href*='/photo/']"):
            if len(captured_posts) + len(dom_posts) >= target_posts:
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
            if pid and pid not in captured_ids:
                parent_text = a.parent.get_text(strip=True) if a.parent else ""
                is_pinned = "Ghim" in text or "Pinned" in text or "Ghim" in parent_text or "Pinned" in parent_text
                dom_posts.append({
                    "id": pid,
                    "desc": desc,
                    "stats": {"playCount": 0, "diggCount": 0, "commentCount": 0, "shareCount": 0},
                    "video": {"cover": img.get("src", "") if img else ""},
                    "isPinned": is_pinned,
                })
                captured_ids.add(pid)

    captured_posts = normalize_tiktok_item_list(
        [*captured_posts, *ssr_posts, *root_posts, *dom_posts], clean_user
    )

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
    unique_id = user_info.get("uniqueId")
    if (
        not re.fullmatch(r"[1-9][0-9]{0,19}", external_id)
        or not isinstance(unique_id, str)
        or not unique_id.strip()
    ):
        return None
    unique_id = unique_id.strip()
    observed_at = datetime.now(UTC).isoformat()
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
        "id_type": "TIKTOK_USER_ID",
        "observed_at": observed_at,
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

    for item in normalize_tiktok_item_list(item_list, clean_user)[:10]:
        pid = str(item.get("id", ""))
        caption = item.get("desc", "")
        stats = item.get("stats", {}) or {}
        posted_at = normalize_post_date(item.get("createTime") or item.get("postedAt"))

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
