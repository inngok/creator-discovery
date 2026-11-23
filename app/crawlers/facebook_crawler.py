import asyncio
import json
import os
import re
import unicodedata
from typing import List, Optional

from bs4 import BeautifulSoup
from playwright.sync_api import BrowserContext, Page, sync_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth

from app.core.logger import setup_logger
from app.core.post_dates import normalize_post_date
from app.core.models import AIEnrichment, CreatorInfo, IngestPayload, Platform, Post

logger = setup_logger("facebook_crawler")


def parse_stat_number(val_str: str) -> int:
    """Chuyển đổi chuỗi số có định dạng K, M, Tr, B hoặc dấu chấm/phẩy sang số nguyên (Đa ngôn ngữ VN/EN)."""
    if not val_str:
        return 0
    normalized = "".join(c for c in unicodedata.normalize("NFKD", val_str.casefold())
                         if not unicodedata.combining(c))
    match = re.search(r"([0-9]+(?:[.,][0-9]+)*)\s*(?:(ty|trieu|tr|nghin|ngan|[kmb])\b)?", normalized)
    if not match:
        return 0
    number, unit = match.groups()
    multipliers = {"ty": 10**9, "b": 10**9, "trieu": 10**6, "tr": 10**6,
                   "m": 10**6, "nghin": 1000, "ngan": 1000, "k": 1000}
    if unit:
        if "." in number and "," in number:
            decimal = max(number.rfind("."), number.rfind(","))
            number = re.sub(r"[.,]", "", number[:decimal]) + "." + number[decimal+1:]
        else:
            number = number.replace(",", ".")
    elif re.fullmatch(r"[0-9]{1,3}(?:[.,][0-9]{3})+", number):
        number = re.sub(r"[.,]", "", number)
    else:
        number = number.replace(",", ".")
    try:
        return int(float(number) * multipliers.get(unit, 1))
    except ValueError:
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


def _init_facebook_playwright_context(p) -> tuple[BrowserContext, None]:
    from app.crawlers.public_browser import create_public_context
    return create_public_context(p, "FACEBOOK"), None


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
    number = r"([0-9]+(?:[.,][0-9]+)*(?:\s*(?:k|m|b|tr|triệu|nghìn|ngàn|tỷ))?)"
    patterns = [
        number + r"\s*(?:người theo dõi|lượt theo dõi|followers?)\b",
        r"(?:followed by)\s*" + number + r"(?:\s*people)?",
    ]
    for text in (raw_bio, page_text):
        for pattern in patterns:
            match = re.search(pattern, text or "", re.IGNORECASE)
            if match:
                return parse_stat_number(match.group(1))
    # Page likes and post reactions are not follower counts.
    return 0


def extract_facebook_posts(soup: BeautifulSoup, clean_slug: str, max_posts: int = 8) -> List[Post]:
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
                postedAt=next((date for element in art.select("time[datetime], [data-utime], [itemprop='datePublished'][content]")
                               if (date := normalize_post_date(element.get("datetime")
                                   or element.get("data-utime") or element.get("content")))), None),
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
            avatar_url = og_image.get("content") if og_image else None

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

            # Fallback sang m.facebook.com nếu trang desktop bị chặn hoặc không bóc được followers
            if followers == 0 or "login" in page.url.lower():
                logger.info(f"[Facebook Crawler] Fallback mở mobile URL: https://m.facebook.com/{clean_slug}")
                m_url = f"https://m.facebook.com/profile.php?id={clean_slug}" if clean_slug.isdigit() else f"https://m.facebook.com/{clean_slug}"
                try:
                    page.goto(m_url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(2000)
                    m_soup = BeautifulSoup(page.content(), "html.parser")
                    m_og_desc = m_soup.find("meta", property="og:description") or m_soup.find("meta", attrs={"name": "description"})
                    if m_og_desc and m_og_desc.get("content"):
                        m_bio = m_og_desc.get("content")
                        m_followers = extract_facebook_followers(m_bio, m_soup.get_text(separator=" "))
                        if m_followers > 0:
                            followers = m_followers
                            raw_bio = m_bio
                    m_og_title = m_soup.find("meta", property="og:title")
                    if m_og_title and m_og_title.get("content") and display_name == clean_slug:
                        display_name = m_og_title.get("content")
                    m_og_img = m_soup.find("meta", property="og:image")
                    if m_og_img and m_og_img.get("content") and not avatar_url:
                        avatar_url = m_og_img.get("content")
                except Exception as m_err:
                    logger.warning(f"[Facebook Crawler] Lỗi khi fallback sang mobile: {m_err}")

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
            recent_posts = extract_facebook_posts(soup, clean_slug, max_posts=8)
            from app.crawlers.meta_public_data import facebook_structured_posts, fetch_facebook_public_html
            structured_posts = facebook_structured_posts(soup, clean_slug)
            if len(structured_posts) < 3:
                try:
                    public_soup = fetch_facebook_public_html(clean_slug)
                    if public_soup is not None:
                        structured_posts = list({p.platformPostId: p for p in [
                            *structured_posts, *facebook_structured_posts(public_soup, clean_slug)]}.values())
                except Exception as error:
                    logger.warning("[Facebook Crawler] Public HTML unavailable: %s", type(error).__name__)
            if structured_posts:
                recent_posts = structured_posts[:12]
            if not recent_posts and "m_soup" in locals():
                recent_posts = extract_facebook_posts(m_soup, clean_slug, max_posts=8)

            if followers == 0 and not recent_posts:
                logger.warning("[Facebook Crawler] No follower or post evidence; refusing an empty profile.")
                return None

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

