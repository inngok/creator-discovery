import asyncio
import json
import os
import re
from typing import List, Optional

from bs4 import BeautifulSoup
from playwright.sync_api import BrowserContext, Page, sync_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth

from app.core.logger import setup_logger
from app.core.models import AIEnrichment, CreatorInfo, IngestPayload, Platform, Post

logger = setup_logger("facebook_crawler")


def parse_stat_number(val_str: str) -> int:
    """Chuyển đổi chuỗi số có định dạng K, M, Tr, B hoặc dấu chấm/phẩy sang số nguyên (Đa ngôn ngữ VN/EN)."""
    if not val_str:
        return 0
    val_str = val_str.upper().strip()
    multiplier = 1
    if any(k in val_str for k in ["B", "TỶ", "TY"]):
        multiplier = 1_000_000_000
    elif any(k in val_str for k in ["M", "TR", "TRIỆU", "TRIEU"]):
        multiplier = 1_000_000
    elif any(k in val_str for k in ["K", "NGHÌN", "NGHIN"]):
        multiplier = 1_000

    clean_str = re.sub(r"[^0-9\.]", "", val_str.replace(",", "."))
    try:
        return int(float(clean_str) * multiplier)
    except Exception:
        return 0


def clean_facebook_username(username: str) -> str:
    """Chuẩn hóa đường link hoặc username Facebook."""
    username = username.strip()
    if "facebook.com" in username:
        match = re.search(
            r"facebook\.com/(?:profile\.php\?id=(\d+)|([^/?&#]+))", username
        )
        if match:
            username = match.group(1) if match.group(1) else match.group(2)
    return username.strip().rstrip("/")


def _init_facebook_playwright_context(p) -> tuple[BrowserContext, str]:
    """Khởi tạo context Playwright cho Facebook với Anti-bot và nạp Cookie chuẩn hóa."""
    browser = p.chromium.launch(
        headless=True,
        args=[
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
    )

    state_path = os.path.join("data", "playwright_fb_state.json")
    context_args = {
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 800},
        "locale": "vi-VN",
    }
    if os.path.exists(state_path):
        context_args["storage_state"] = state_path

    context = browser.new_context(**context_args)

    # Nạp cookies từ data/facebook_cookies.json nếu có
    cookie_path = os.path.join("data", "facebook_cookies.json")
    if os.path.exists(cookie_path) and not os.path.exists(state_path):
        try:
            with open(cookie_path, "r", encoding="utf-8") as f:
                raw_cookies = json.load(f)
                if isinstance(raw_cookies, dict) and "cookies" in raw_cookies:
                    raw_cookies = raw_cookies["cookies"]
                valid_cookies = []
                for c in raw_cookies:
                    if not isinstance(c, dict) or "name" not in c or "value" not in c:
                        continue
                    cookie_obj = {
                        "name": str(c["name"]),
                        "value": str(c["value"]),
                        "domain": str(c.get("domain", ".facebook.com")),
                        "path": str(c.get("path", "/")),
                    }
                    same_site = str(c.get("sameSite", "")).capitalize()
                    if same_site in ["Strict", "Lax", "None"]:
                        cookie_obj["sameSite"] = same_site
                    if "secure" in c:
                        cookie_obj["secure"] = bool(c["secure"])
                    if "httpOnly" in c:
                        cookie_obj["httpOnly"] = bool(c["httpOnly"])
                    valid_cookies.append(cookie_obj)

                if valid_cookies:
                    context.add_cookies(valid_cookies)
                    logger.info(f"[Facebook Crawler] Đã nạp thành công {len(valid_cookies)} cookies từ {cookie_path}")
        except Exception as e:
            logger.warning(f"[Facebook Crawler] Lỗi nạp cookies từ {cookie_path}: {e}")

    return context, state_path


def dismiss_facebook_dialogs(page: Page) -> None:
    """Tự động ẩn và đóng popup/dialog yêu cầu đăng nhập của Facebook."""
    try:
        page.keyboard.press("Escape")
        page.evaluate(
            """() => {
            const style = document.createElement('style');
            style.innerHTML = 'div[role="dialog"], #login_popup_cta_form, div[data-nosnippet="true"] { display: none !important; } body { overflow: auto !important; }';
            document.head.appendChild(style);
            const closeBtns = document.querySelectorAll('div[aria-label="Close"], div[aria-label="Đóng"], [aria-label="close"]');
            closeBtns.forEach(b => { try { b.click(); } catch(e){} });
        }"""
        )
    except Exception:
        pass


def extract_facebook_followers(raw_bio: str, page_text: str) -> int:
    """Trích xuất Followers từ bio hoặc text trang với regex đa ngôn ngữ (EN/VN)."""
    patterns = [
        r"([\d\.,]+[KMBTr]?)\s*(?:người theo dõi|người thích|lượt thích|followers?|likes?)",
        r"(?:followed by|có)\s*([\d\.,]+[KMBTr]?)\s*(?:people|người)?",
        r"([\d\.,]+[KMBTr]?)\s+Followers",
    ]
    for pat in patterns:
        m = re.search(pat, raw_bio, re.IGNORECASE)
        if m:
            return parse_stat_number(m.group(1))

    for pat in patterns:
        m = re.search(pat, page_text, re.IGNORECASE)
        if m:
            return parse_stat_number(m.group(1))

    return 0


def extract_facebook_posts(soup: BeautifulSoup, clean_slug: str, max_posts: int = 5) -> List[Post]:
    """Bóc tách bài viết gần nhất với selector đa ngôn ngữ."""
    recent_posts: List[Post] = []
    seen_post_ids = set()

    articles = soup.find_all("div", attrs={"role": "article"})
    for art in articles:
        if len(recent_posts) >= max_posts:
            break

        # Trích xuất Caption
        captions = [
            d.get_text(strip=True)
            for d in art.find_all(attrs={"dir": "auto"})
            if len(d.get_text(strip=True)) > 20
        ]
        caption = captions[0] if captions else ""
        if not caption:
            continue

        # Trích xuất Post URL & Post ID
        post_url = f"https://www.facebook.com/{clean_slug}"
        post_id = f"fb_{abs(hash(caption))}"
        for a in art.find_all("a", href=True):
            href = a.get("href")
            if any(k in href for k in ["/posts/", "/videos/", "/reel/", "fbid="]):
                clean_href = href.split("?")[0]
                post_url = clean_href
                id_match = re.search(r"(?:posts|videos|reel)/([^/?&#]+)", clean_href)
                if id_match:
                    post_id = id_match.group(1)
                break

        if post_id in seen_post_ids:
            continue
        seen_post_ids.add(post_id)

        # Trích xuất tương tác đa ngôn ngữ (Thích, Bình luận, Chia sẻ)
        lines = [
            l.strip()
            for l in art.get_text("\n", strip=True).split("\n")
            if l.strip()
        ]
        likes, comments, shares = 0, 0, 0

        # Cách 1: Quét cụm phản ứng
        for idx, l in enumerate(lines):
            low = l.lower()
            if any(k in low for k in ["tất cả cảm xúc", "all reactions", "reactions"]):
                sub_numbers = []
                for j in range(idx + 1, min(idx + 5, len(lines))):
                    val = lines[j]
                    if re.match(r"^[\d,\.]+[KMBTr]?$", val, re.IGNORECASE):
                        sub_numbers.append(parse_stat_number(val))
                    elif any(w in val.lower() for w in ["thích", "like", "bình luận", "comment"]):
                        break
                if len(sub_numbers) >= 1:
                    likes = sub_numbers[0]
                if len(sub_numbers) >= 2:
                    comments = sub_numbers[1]
                if len(sub_numbers) >= 3:
                    shares = sub_numbers[2]
                break

        # Cách 2: Regex quét trực tiếp từng dòng (Đa ngôn ngữ)
        if likes == 0:
            for l in lines:
                m_like = re.search(r"([\d\.,]+[KMBTr]?)\s+(?:likes?|lượt thích|cảm xúc|reactions?)", l, re.I)
                if m_like:
                    likes = parse_stat_number(m_like.group(1))
                    break
        if comments == 0:
            for l in lines:
                m_comm = re.search(r"([\d\.,]+[KMBTr]?)\s+(?:comments?|bình luận)", l, re.I)
                if m_comm:
                    comments = parse_stat_number(m_comm.group(1))
                    break
        if shares == 0:
            for l in lines:
                m_share = re.search(r"([\d\.,]+[KMBTr]?)\s+(?:shares?|lượt chia sẻ)", l, re.I)
                if m_share:
                    shares = parse_stat_number(m_share.group(1))
                    break

        recent_posts.append(
            Post(
                platformPostId=post_id,
                postUrl=post_url,
                caption=caption,
                views=0,
                likes=likes,
                comments=comments,
                shares=shares,
                postedAt=None,
            )
        )

    return recent_posts


def _crawl_facebook_profile_sync(username: str) -> Optional[IngestPayload]:
    clean_slug = clean_facebook_username(username)
    if not clean_slug:
        return None

    if clean_slug.isdigit():
        url = f"https://www.facebook.com/profile.php?id={clean_slug}"
    else:
        url = f"https://www.facebook.com/{clean_slug}"

    with sync_playwright() as p:
        context, state_path = _init_facebook_playwright_context(p)
        page = context.new_page()
        stealth = Stealth()
        stealth.apply_stealth_sync(page)

        try:
            logger.info(f"[Facebook Crawler] Đang mở trang: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            # Đóng popup login
            dismiss_facebook_dialogs(page)

            # Cuộn trang nhẹ để tải thêm bài viết
            for _ in range(2):
                page.evaluate("window.scrollBy(0, 1200);")
                page.wait_for_timeout(1800)

            html_content = page.content()
            soup = BeautifulSoup(html_content, "html.parser")

            # 1. Trích xuất thông tin cơ bản của Page / Creator
            og_title = soup.find("meta", property="og:title")
            og_desc = soup.find("meta", property="og:description")
            meta_desc = soup.find("meta", attrs={"name": "description"})
            og_image = soup.find("meta", property="og:image")

            page_title = soup.title.text if soup.title else clean_slug
            display_name = (
                og_title.get("content")
                if og_title
                else page_title.replace(" | Facebook", "").strip()
            )

            raw_bio = ""
            if og_desc and og_desc.get("content"):
                raw_bio = og_desc.get("content")
            elif meta_desc and meta_desc.get("content"):
                raw_bio = meta_desc.get("content")

            # 2. Bóc tách số lượng Followers đa ngôn ngữ
            page_text = soup.get_text(separator=" ")
            followers = extract_facebook_followers(raw_bio, page_text)

            # Loại bỏ tiền tố thống kê tự sinh của Facebook
            clean_bio = re.sub(
                r"^.*?[\d\.,]+[KMBTr]?\s*(?:người theo dõi|followers|lượt thích|likes).*?(?:đang nói về điều này|talking about this)[\.\s·-]*",
                "",
                raw_bio,
                flags=re.IGNORECASE,
            ).strip()

            # 3. Bóc tách Email & SĐT liên hệ Booking từ Bio
            email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", raw_bio)
            public_email = email_match.group(0) if email_match else None

            phone_match = re.search(
                r"(?:\+?84|0)(?:\s|\.)?\d{2,3}(?:\s|\.)?\d{3}(?:\s|\.)?\d{3,4}",
                raw_bio,
            )
            public_phone = phone_match.group(0) if phone_match else None

            # 4. Bóc tách bài viết gần nhất qua hàm modular
            recent_posts = extract_facebook_posts(soup, clean_slug, max_posts=5)

            avatar_url = og_image.get("content") if og_image else None
            cross_links = re.findall(r"https?://[^\s\"'>]+", clean_bio) if clean_bio else []

            creator_info = CreatorInfo(
                platform=Platform.FACEBOOK,
                externalId=clean_slug,
                isStableExternalId=clean_slug.isdigit(),
                username=clean_slug,
                displayName=display_name,
                profileUrl=url,
                avatarUrl=avatar_url,
                followers=followers,
                bio=clean_bio,
                publicEmail=public_email,
                publicPhone=public_phone,
                bioCrossLinks=cross_links,
            )

            # Lưu lại trạng thái trình duyệt
            try:
                if not os.path.exists("data"):
                    os.makedirs("data")
                context.storage_state(path=state_path)
            except Exception:
                pass

        except Exception as e:
            logger.error(f"[Facebook Crawler] Lỗi khi cào trang {clean_slug}: {e}")
            return None
        finally:
            context.browser.close()

    return IngestPayload(
        creator=creator_info,
        recentPosts=recent_posts,
        aiEnrichment=None,
        source="PLAYWRIGHT_STEALTH_FACEBOOK",
    )


async def crawl_facebook_profile(username: str) -> Optional[IngestPayload]:
    return await asyncio.to_thread(_crawl_facebook_profile_sync, username)

