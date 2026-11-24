import asyncio
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from typing import List, Optional

from bs4 import BeautifulSoup
from playwright.sync_api import BrowserContext, Page, sync_playwright
from playwright_stealth import Stealth

from app.core.logger import setup_logger
from app.core.models import AIEnrichment, CreatorInfo, IngestPayload, Platform, Post

logger = setup_logger("instagram_crawler")

# Configure standard encoding for Windows console
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def parse_instagram_number(val: str) -> int:
    """Chuyển đổi chuỗi số dạng '8M', '173K', '1,235' thành số nguyên int."""
    if not val:
        return 0
    val = val.strip().replace(",", "")
    multiplier = 1
    if val.endswith(("k", "K")):
        multiplier = 1_000
        val = val[:-1]
    elif val.endswith(("m", "M")):
        multiplier = 1_000_000
        val = val[:-1]
    elif val.endswith(("b", "B")):
        multiplier = 1_000_000_000
        val = val[:-1]
    try:
        return int(float(val) * multiplier)
    except ValueError:
        return 0


def _init_instagram_playwright_context(p) -> tuple[BrowserContext, None]:
    from app.crawlers.public_browser import create_public_context
    return create_public_context(p, "INSTAGRAM"), None


def extract_instagram_posts_from_soup(soup: BeautifulSoup, max_posts: int = 5) -> tuple[List[Post], str]:
    """
    Trích xuất toàn bộ bài đăng kèm Likes, Comments, Views, Timestamp từ embedded JSON scripts
    của Instagram mà KHÔNG CẦN gọi page.goto() từng post.
    """
    recent_posts: List[Post] = []
    seen_codes = set()
    user_pk = ""

    for s in soup.find_all("script"):
        stext = s.string or ""
        if not stext:
            continue

        if "pk" in stext and not user_pk:
            pk_m = re.search(r'"pk":"(\d+)"', stext)
            if pk_m:
                user_pk = pk_m.group(1)

        # Tìm các block media trong Polaris / GraphQL / timeline connection
        if any(k in stext for k in ["polaris_ordered_timeline_connection", "XIGPolaris", "user_timeline_graphql_connection", "edge_owner_to_timeline_media"]):
            code_matches = list(re.finditer(r'"code":"([A-Za-z0-9_-]+)"', stext))
            if not code_matches:
                code_matches = list(re.finditer(r'"shortcode":"([A-Za-z0-9_-]+)"', stext))

            for m in code_matches:
                p_code = m.group(1)
                if p_code in seen_codes:
                    continue
                seen_codes.add(p_code)

                # Lấy đoạn text JSON xung quanh bài viết này (±1000 ký tự) để bóc tương tác
                start_idx = max(0, m.start() - 200)
                end_idx = min(len(stext), m.end() + 1200)
                chunk = stext[start_idx:end_idx]

                # Caption
                cap_m = re.search(r'"caption":\{"text":"((?:[^"\\]|\\.)*)"\}', chunk)
                clean_cap = ""
                if cap_m:
                    try:
                        clean_cap = json.loads(f'"{cap_m.group(1)}"')
                    except Exception:
                        clean_cap = cap_m.group(1).replace("\\n", "\n")

                # Likes
                like_m = re.search(r'"(?:like_count|edge_media_preview_like|edge_liked_by)"\s*:\s*(?:\{"count"\s*:\s*)?(\d+)', chunk)
                likes = int(like_m.group(1)) if like_m else 0

                # Comments
                comm_m = re.search(r'"(?:comment_count|edge_media_to_comment)"\s*:\s*(?:\{"count"\s*:\s*)?(\d+)', chunk)
                comments = int(comm_m.group(1)) if comm_m else 0

                # Views
                view_m = re.search(r'"(?:play_count|video_view_count)"\s*:\s*(\d+)', chunk)
                views = int(view_m.group(1)) if view_m else 0

                # Timestamp
                time_m = re.search(r'"(?:taken_at|taken_at_timestamp)"\s*:\s*(\d+)', chunk)
                posted_at = None
                if time_m:
                    try:
                        dt = datetime.fromtimestamp(int(time_m.group(1)), tz=UTC)
                        posted_at = dt.isoformat()
                    except Exception:
                        pass

                recent_posts.append(
                    Post(
                        platformPostId=p_code,
                        postUrl=f"https://www.instagram.com/p/{p_code}/",
                        caption=clean_cap,
                        views=views,
                        likes=likes,
                        comments=comments,
                        shares=0,
                        postedAt=posted_at,
                    )
                )
                if len(recent_posts) >= max_posts:
                    break

        if len(recent_posts) >= max_posts:
            break

    # Fallback lấy post URLs và Alt text từ DOM nếu script parse không ra
    if not recent_posts:
        for a in soup.find_all("a", href=True):
            if len(recent_posts) >= max_posts:
                break
            m = re.search(r"/(?:p|reel)/([A-Za-z0-9_-]+)/", a["href"])
            if m:
                code = m.group(1)
                if code not in seen_codes:
                    seen_codes.add(code)
                    img = a.find("img")
                    cap = img.get("alt", "") if img else ""
                    recent_posts.append(
                        Post(
                            platformPostId=code,
                            postUrl=f"https://www.instagram.com/p/{code}/",
                            caption=cap,
                            views=0,
                            likes=0,
                            comments=0,
                            shares=0,
                            postedAt=None,
                        )
                    )

    return recent_posts, user_pk


def _crawl_instagram_profile_sync(username: str) -> Optional[IngestPayload]:
    username = username.strip()
    if "instagram.com" in username:
        match = re.search(r"instagram\.com/([^/?&#]+)", username)
        if match:
            username = match.group(1)

    if username.startswith("@"):
        username = username[1:]

    url = f"https://www.instagram.com/{username}/"
    logger.info(f"[Instagram Crawler] Bắt đầu cào profile: @{username} ({url})")

    with sync_playwright() as p:
        context, state_path = _init_instagram_playwright_context(p)
        page = context.new_page()
        stealth = Stealth()
        stealth.apply_stealth_sync(page)

        from app.crawlers.instagram_timeline import extract_owned_media
        captured_posts = {}

        def capture_timeline(response):
            if response.status != 200 or not any(part in response.url for part in ("graphql", "/api/v1/feed/")):
                return
            try:
                for post in extract_owned_media(response.json(), username):
                    captured_posts[post.platformPostId] = post
            except Exception:
                pass

        page.on("response", capture_timeline)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1500)
        except Exception as e:
            context.browser.close()
            raise ValueError(f"Không thể truy cập Instagram @{username}: {e}")

        content = page.content()
        soup = BeautifulSoup(content, "html.parser")
        from app.crawlers.meta_public_data import instagram_profile
        structured_profile = instagram_profile(soup, username)

        # 1. Trích xuất Metadata Profile
        meta_desc = soup.find("meta", attrs={"name": "description"})
        og_desc = soup.find("meta", attrs={"property": "og:description"})
        og_title = soup.find("meta", attrs={"property": "og:title"})
        og_image = soup.find("meta", attrs={"property": "og:image"})

        desc_text = (og_desc.get("content") if og_desc else "") or (
            meta_desc.get("content") if meta_desc else ""
        )
        title_text = og_title.get("content") if og_title else page.title()
        avatar_url = og_image.get("content") if og_image else None

        # Parse Followers trước khi kiểm tra login wall
        followers = 0
        f_match = re.search(r"([\d.,]+[KMBkmb]?)\s+(?:Followers|người theo dõi)", desc_text, re.IGNORECASE)
        if f_match:
            followers = parse_instagram_number(f_match.group(1))
        if structured_profile:
            followers = structured_profile.get("follower_count", (structured_profile.get("edge_followed_by") or {}).get("count", followers))
            avatar_url = structured_profile.get("profile_pic_url_hd") or structured_profile.get("profile_pic_url") or avatar_url

        # Kiểm tra nếu bị Instagram chặn bắt đăng nhập hoàn toàn (không có cả meta OG followers)
        if followers == 0 and (
            "login" in page.url.lower()
            or (title_text.strip().lower() == "instagram" and "log in" in desc_text.lower())
        ):
            context.browser.close()
            raise ValueError(
                f"Instagram không cho khách chưa đăng nhập xem profile @{username}. Không lấy được dữ liệu công khai; thử lại sau."
            )

        # Parse Display Name
        display_name = username
        dn_match = re.match(r"^(.*?)\s*\(@", title_text)
        if dn_match:
            display_name = dn_match.group(1).strip()
        display_name = structured_profile.get("full_name") or display_name

        # Parse Bio
        bio = ""
        if meta_desc:
            bio_match = re.search(r':\s*"(.*)"\s*$', meta_desc.get("content", ""))
            if bio_match:
                bio = bio_match.group(1).strip()
        bio = structured_profile.get("biography") or bio

        # Email & Phone từ Bio
        email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", bio)
        public_email = email_match.group(0) if email_match else None

        phone_match = re.search(
            r"(?:\+?84|0)(?:\s|\.)?\d{2,3}(?:\s|\.)?\d{3}(?:\s|\.)?\d{3,4}", bio
        )
        public_phone = phone_match.group(0) if phone_match else None

        # 2. Trích xuất bài đăng nhanh từ Relay / Script (Không cần mở từng post)
        recent_posts, user_pk = extract_instagram_posts_from_soup(soup, max_posts=5)
        user_pk = str(structured_profile.get("pk") or structured_profile.get("id") or user_pk)
        for script in soup.find_all("script", attrs={"type": "application/json"}):
            try:
                for post in extract_owned_media(json.loads(script.string or "{}"), username):
                    captured_posts[post.platformPostId] = post
            except (ValueError, TypeError):
                pass
        if captured_posts:
            by_id = {p.platformPostId: p for p in recent_posts}
            by_id.update(captured_posts)
            recent_posts = list(by_id.values())[:12]
        if followers == 0 and not recent_posts and not user_pk:
            logger.warning("[Instagram Crawler] No public profile or post evidence; refusing an empty result.")
            context.browser.close()
            return None

        # 3. Chỉ Fallback mở từng post NẾU toàn bộ bài đăng đều không bóc được tương tác (likes=0, comments=0)
        has_stats = any((p.likes or 0) > 0 or (p.comments or 0) > 0 or (p.views or 0) > 0 for p in recent_posts)
        if not has_stats and recent_posts:
            logger.info(f"[Instagram Crawler] Fallback mở bài đăng chi tiết để bóc tương tác cho @{username}...")
            for post in recent_posts[:3]:
                try:
                    page.goto(post.postUrl, wait_until="domcontentloaded", timeout=12000)
                    p_soup = BeautifulSoup(page.content(), "html.parser")
                    p_meta = p_soup.find("meta", attrs={"property": "og:description"}) or p_soup.find(
                        "meta", attrs={"name": "description"}
                    )
                    p_desc = p_meta.get("content", "") if p_meta else ""

                    l_match = re.search(r"([\d.,]+[KMBkmb]?)\s+likes?", p_desc, re.IGNORECASE)
                    if l_match:
                        post.likes = parse_instagram_number(l_match.group(1))

                    c_match = re.search(r"([\d.,]+[KMBkmb]?)\s+comments?", p_desc, re.IGNORECASE)
                    if c_match:
                        post.comments = parse_instagram_number(c_match.group(1))

                    v_match = re.search(r"([\d.,]+[KMBkmb]?)\s+views?", p_desc, re.IGNORECASE)
                    if v_match:
                        post.views = parse_instagram_number(v_match.group(1))

                    time_tag = p_soup.find("time")
                    if time_tag and time_tag.get("datetime"):
                        post.postedAt = time_tag.get("datetime")
                except Exception as post_err:
                    logger.warning(f"[Instagram] Lỗi đọc chi tiết bài viết {post.platformPostId}: {post_err}")
        else:
            logger.info(f"[Instagram Crawler] ✅ Đã bóc tương tác tức thì từ script GraphQL cho {len(recent_posts)} bài đăng (Bỏ qua page.goto)!")

        context.browser.close()

        cross_links = re.findall(r"https?://[^\s\"'>]+", bio) if bio else []
        creator_info = CreatorInfo(
            platform=Platform.INSTAGRAM,
            externalId=user_pk or username,
            isStableExternalId=bool(user_pk and user_pk.isdigit()),
            username=f"@{username}",
            displayName=display_name,
            profileUrl=f"https://www.instagram.com/{username}",
            avatarUrl=avatar_url,
            followers=followers,
            isVerified=bool(structured_profile.get("is_verified", False)),
            bio=bio,
            publicEmail=public_email,
            publicPhone=public_phone,
            bioCrossLinks=cross_links,
        )

        logger.info(
            f"[Instagram Crawler] Hoàn tất: @{username} | Followers: {followers:,} | Posts: {len(recent_posts)}"
        )

        return IngestPayload(
            creator=creator_info,
            recentPosts=recent_posts,
            aiEnrichment=None,
            source="PLAYWRIGHT_INSTAGRAM_STEALTH",
        )


async def crawl_instagram_profile(username: str) -> Optional[IngestPayload]:
    """Hàm bọc bất đồng bộ cho FastAPI."""
    return await asyncio.to_thread(_crawl_instagram_profile_sync, username)
