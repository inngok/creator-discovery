import asyncio
import json
import os
import re
import sys
import time
from typing import List, Optional

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from app.core.models import AIEnrichment, CreatorInfo, IngestPayload, Platform, Post

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


def _crawl_instagram_profile_sync(username: str) -> Optional[IngestPayload]:
    username = username.strip()
    if "instagram.com" in username:
        match = re.search(r"instagram\.com/([^/?&#]+)", username)
        if match:
            username = match.group(1)

    if username.startswith("@"):
        username = username[1:]

    url = f"https://www.instagram.com/{username}/"
    print(f"[Instagram Crawler] Bat dau cao profile: @{username} ({url})")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        state_path = os.path.join("data", "playwright_ig_state.json")
        context_args = {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "viewport": {"width": 1280, "height": 800},
        }
        if os.path.exists(state_path):
            context_args["storage_state"] = state_path

        context = browser.new_context(**context_args)
        # Chặn toàn bộ chuyển hướng (redirect) sang trang login
        context.route("**/accounts/login/**", lambda route: route.abort())

        # Nap cookie json neu co
        cookie_path = os.path.join("data", "ig_cookies.json")
        if os.path.exists(cookie_path) and not os.path.exists(state_path):
            try:
                with open(cookie_path, "r", encoding="utf-8") as f:
                    cookies = json.load(f)
                    valid_cookies = []
                    for c in cookies:
                        if "sameSite" in c and c["sameSite"] not in ["Strict", "Lax", "None"]:
                            del c["sameSite"]
                        valid_cookies.append(c)
                    context.add_cookies(valid_cookies)
                print(f"[Instagram] Da nap cookie tu {cookie_path}")
            except Exception as e:
                print(f"[Instagram] Khong the nap cookie: {e}")

        page = context.new_page()
        stealth = Stealth()
        stealth.apply_stealth_sync(page)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(1)
        except Exception as e:
            browser.close()
            raise ValueError(f"Khong the truy cap Instagram @{username}: {e}")

        content = page.content()
        soup = BeautifulSoup(content, "html.parser")

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

        # Kiem tra neu bi Instagram chan bat dang nhap
        if (
            "login" in page.url.lower()
            or (title_text.strip().lower() == "instagram" and "log in" in desc_text.lower())
        ):
            browser.close()
            raise ValueError(
                f"Profile @{username} yeu cau dang nhap (Co the do tai khoan rieng tu hoac bi han che). "
                f"Vui long them cookie vao data/ig_cookies.json de cao tai khoan nay."
            )

        # Parse Followers
        followers = 0
        f_match = re.search(r"([\d.,]+[KMBkmb]?)\s+Followers", desc_text, re.IGNORECASE)
        if f_match:
            followers = parse_instagram_number(f_match.group(1))

        # Parse Display Name
        display_name = username
        dn_match = re.match(r"^(.*?)\s*\(@", title_text)
        if dn_match:
            display_name = dn_match.group(1).strip()

        # Parse Bio
        bio = ""
        if meta_desc:
            bio_match = re.search(r':\s*"(.*)"\s*$', meta_desc.get("content", ""))
            if bio_match:
                bio = bio_match.group(1).strip()

        # Email & Phone từ Bio
        email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", bio)
        public_email = email_match.group(0) if email_match else None

        phone_match = re.search(
            r"(?:\+?84|0)(?:\s|\.)?\d{2,3}(?:\s|\.)?\d{3}(?:\s|\.)?\d{3,4}", bio
        )
        public_phone = phone_match.group(0) if phone_match else None

        # 2. Trích xuất bài đăng gần đây từ Relay / embedded script
        recent_posts: List[Post] = []
        user_pk = ""

        for s in soup.find_all("script"):
            stext = s.string or ""
            if "polaris_ordered_timeline_connection" in stext or "XIGPolaris" in stext:
                try:
                    # Trích xuất user pk nếu có
                    pk_m = re.search(r'"pk":"(\d+)"', stext)
                    if pk_m and not user_pk:
                        user_pk = pk_m.group(1)

                    # Trích xuất các bài viết
                    raw_edges = re.findall(
                        r'\{"node":\{"__typename":"[^"]+","__isXIGPolarisMedia":"[^"]+","pk":"(\d+)","code":"([^"]+)",(?:.*?"caption":\{"text":"([^"]*)")?',
                        stext,
                    )
                    for edge in raw_edges:
                        if len(recent_posts) >= 5:
                            break
                        p_pk, p_code, p_caption = edge
                        # Unescape unicode trong caption an toan (bao gom emoji)
                        try:
                            clean_cap = json.loads(f'"{p_caption}"')
                        except Exception:
                            clean_cap = p_caption.replace("\\n", "\n")

                        recent_posts.append(
                            Post(
                                platformPostId=p_code,
                                postUrl=f"https://www.instagram.com/p/{p_code}/",
                                caption=clean_cap,
                                views=0,
                                likes=0,
                                comments=0,
                                shares=0,
                                postedAt="",
                            )
                        )
                except Exception as parse_err:
                    print(f"[Instagram] Lỗi đọc post data: {parse_err}")

        # Fallback lấy post URLs từ DOM nếu script parse không ra
        if not recent_posts:
            seen_codes = set()
            for a in soup.find_all("a", href=True):
                if len(recent_posts) >= 5:
                    break
                m = re.search(r"/(?:p|reel)/([A-Za-z0-9_-]+)/", a["href"])
                if m:
                    code = m.group(1)
                    if code not in seen_codes:
                        seen_codes.add(code)
                        # Lay alt text cua img lam caption
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
                                postedAt="",
                            )
                        )

        browser.close()

        creator_info = CreatorInfo(
            platform=Platform.INSTAGRAM,
            externalId=user_pk or username,
            username=f"@{username}",
            displayName=display_name,
            profileUrl=f"https://www.instagram.com/{username}",
            avatarUrl=avatar_url,
            followers=followers,
            bio=bio,
            publicEmail=public_email,
            publicPhone=public_phone,
        )

        print(
            f"[Instagram Crawler] Thanh cong: @{username} | Followers: {followers:,} | Posts: {len(recent_posts)}"
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
