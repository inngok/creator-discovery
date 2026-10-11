import json
import re
import asyncio
import time
import os
from typing import Optional, List
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth
from app.core.models import IngestPayload, CreatorInfo, Post, AIEnrichment, Platform

def parse_stat_number(val_str: str) -> int:
    """Chuyển đổi chuỗi số có định dạng K, M, Tr, B hoặc dấu chấm/phẩy sang số nguyên."""
    if not val_str:
        return 0
    val_str = val_str.upper().strip()
    multiplier = 1
    if 'B' in val_str or 'TỶ' in val_str:
        multiplier = 1000000000
    elif 'M' in val_str or 'TR' in val_str:
        multiplier = 1000000
    elif 'K' in val_str:
        multiplier = 1000

    clean_str = re.sub(r'[^0-9\.]', '', val_str.replace(',', '.'))
    try:
        return int(float(clean_str) * multiplier)
    except Exception:
        return 0

def clean_facebook_username(username: str) -> str:
    """Chuẩn hóa đường link hoặc username Facebook."""
    username = username.strip()
    if "facebook.com" in username:
        match = re.search(r'facebook\.com/(?:profile\.php\?id=(\d+)|([^/?&#]+))', username)
        if match:
            username = match.group(1) if match.group(1) else match.group(2)
    return username.strip().rstrip('/')

def _crawl_facebook_profile_sync(username: str) -> Optional[IngestPayload]:
    clean_slug = clean_facebook_username(username)
    if not clean_slug:
        return None

    if clean_slug.isdigit():
        url = f"https://www.facebook.com/profile.php?id={clean_slug}"
    else:
        url = f"https://www.facebook.com/{clean_slug}"

    recent_posts: List[Post] = []
    seen_post_ids = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-gpu',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled'
            ]
        )

        state_path = os.path.join("data", "playwright_fb_state.json")
        context_args = {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "viewport": {'width': 1280, 'height': 800},
            "locale": 'vi-VN'
        }
        if os.path.exists(state_path):
            context_args["storage_state"] = state_path

        context = browser.new_context(**context_args)

        # Hỗ trợ nạp cookies Facebook nếu người dùng có file data/facebook_cookies.json
        cookie_path = os.path.join("data", "facebook_cookies.json")
        if os.path.exists(cookie_path) and not os.path.exists(state_path):
            try:
                with open(cookie_path, 'r', encoding='utf-8') as f:
                    cookies = json.load(f)
                    valid_cookies = []
                    for c in cookies:
                        if 'sameSite' in c and c['sameSite'] not in ["Strict", "Lax", "None"]:
                            del c['sameSite']
                        valid_cookies.append(c)
                    context.add_cookies(valid_cookies)
                print(f"[Facebook Crawler] Đã nạp cookies từ {cookie_path}")
            except Exception as e:
                print(f"[Facebook Crawler] Lỗi nạp cookies: {e}")

        page = context.new_page()
        stealth = Stealth()
        stealth.apply_stealth_sync(page)

        try:
            print(f"[Facebook Crawler] Đang mở trang: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3500)

            # Tự động đóng popup / dialog yêu cầu đăng nhập nếu xuất hiện
            try:
                page.keyboard.press("Escape")
                page.evaluate("""() => {
                    const closeBtns = document.querySelectorAll('div[aria-label="Close"], div[aria-label="Đóng"], [aria-label="close"]');
                    closeBtns.forEach(b => b.click());
                }""")
            except Exception:
                pass

            # Cuộn trang nhẹ để tải thêm bài viết
            for _ in range(2):
                page.evaluate("window.scrollBy(0, 1200);")
                page.wait_for_timeout(2000)

            html_content = page.content()
            soup = BeautifulSoup(html_content, 'html.parser')

            # 1. Trích xuất thông tin cơ bản của Page / Creator
            og_title = soup.find('meta', property='og:title')
            og_desc = soup.find('meta', property='og:description')
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            og_image = soup.find('meta', property='og:image')

            page_title = soup.title.text if soup.title else clean_slug
            display_name = og_title.get('content') if og_title else page_title.replace(" | Facebook", "").strip()
            
            raw_bio = ""
            if og_desc and og_desc.get('content'):
                raw_bio = og_desc.get('content')
            elif meta_desc and meta_desc.get('content'):
                raw_bio = meta_desc.get('content')

            # 2. Bóc tách số lượng Followers
            followers = 0
            follower_match = re.search(r'([\d\.,]+[KMBTr]?)\s*(?:người theo dõi|followers|lượt thích|likes)', raw_bio, re.IGNORECASE)
            if follower_match:
                f_str = follower_match.group(1)
                if any(unit in f_str.upper() for unit in ['K', 'M', 'B', 'TR']):
                    followers = parse_stat_number(f_str)
                else:
                    clean_num = re.sub(r'[^0-9]', '', f_str)
                    followers = int(clean_num) if clean_num else 0
            else:
                # Tìm trong toàn bộ text trang nếu trong meta chưa có
                text_content = soup.get_text(separator=' ')
                fallback_match = re.search(r'([\d\.,]+[KMBTr]?)\s*(?:người theo dõi|followers)', text_content, re.IGNORECASE)
                if fallback_match:
                    followers = parse_stat_number(fallback_match.group(1))

            # 3. Bóc tách Email & SĐT liên hệ Booking từ Bio
            email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', raw_bio)
            public_email = email_match.group(0) if email_match else None

            phone_match = re.search(r'(?:\+?84|0)(?:\s|\.)?\d{2,3}(?:\s|\.)?\d{3}(?:\s|\.)?\d{3,4}', raw_bio)
            public_phone = phone_match.group(0) if phone_match else None

            # 4. Bóc tách các bài viết gần nhất (Recent Posts)
            articles = soup.find_all('div', attrs={'role': 'article'})
            for art in articles:
                if len(recent_posts) >= 5:
                    break

                # Trích xuất Caption
                captions = [d.get_text(strip=True) for d in art.find_all(attrs={'dir': 'auto'}) if len(d.get_text(strip=True)) > 25]
                caption = captions[0] if captions else ''
                if not caption:
                    continue

                # Trích xuất Post URL & Post ID
                post_url = f"https://www.facebook.com/{clean_slug}"
                post_id = f"fb_{abs(hash(caption))}"
                for a in art.find_all('a', href=True):
                    href = a.get('href')
                    if any(k in href for k in ['/posts/', '/videos/', '/reel/', 'fbid=']):
                        clean_href = href.split('?')[0]
                        post_url = clean_href
                        id_match = re.search(r'(?:posts|videos|reel)/([^/?&#]+)', clean_href)
                        if id_match:
                            post_id = id_match.group(1)
                        break

                if post_id in seen_post_ids:
                    continue
                seen_post_ids.add(post_id)

                # Trích xuất Lượt Like, Bình luận, Chia sẻ
                lines = [l.strip() for l in art.get_text('\n', strip=True).split('\n') if l.strip()]
                likes, comments, shares = 0, 0, 0
                for idx, l in enumerate(lines):
                    if 'tất cả cảm xúc' in l.lower() or 'all reactions' in l.lower():
                        sub_numbers = []
                        for j in range(idx + 1, min(idx + 5, len(lines))):
                            val = lines[j]
                            if re.match(r'^[\d,\.]+[KMBTr]?$', val, re.IGNORECASE):
                                sub_numbers.append(parse_stat_number(val))
                            elif any(w in val.lower() for w in ['thích', 'like', 'bình luận', 'comment']):
                                break
                        if len(sub_numbers) >= 1: likes = sub_numbers[0]
                        if len(sub_numbers) >= 2: comments = sub_numbers[1]
                        if len(sub_numbers) >= 3: shares = sub_numbers[2]
                        break

                recent_posts.append(Post(
                    platformPostId=post_id,
                    postUrl=post_url,
                    caption=caption,
                    views=0, # Facebook web không hiển thị views cho bài viết thường
                    likes=likes,
                    comments=comments,
                    shares=shares,
                    postedAt=None
                ))

            avatar_url = og_image.get('content') if og_image else None

            creator_info = CreatorInfo(
                platform=Platform.FACEBOOK,
                externalId=clean_slug,
                username=clean_slug,
                displayName=display_name,
                profileUrl=url,
                avatarUrl=avatar_url,
                followers=followers,
                bio=raw_bio,
                publicEmail=public_email,
                publicPhone=public_phone
            )

            # Lưu lại trạng thái trình duyệt
            try:
                if not os.path.exists("data"):
                    os.makedirs("data")
                context.storage_state(path=state_path)
            except Exception:
                pass

        except Exception as e:
            print(f"[Facebook Crawler] Lỗi khi cào trang {clean_slug}: {e}")
            return None
        finally:
            browser.close()

    # Chạy phân tích AI (Niche, CreatorType, Bóc tách chiến dịch review)
    print(f"[Facebook Crawler] Đang chạy phân tích AI cho KOC Facebook: {display_name}...")
    ai_enrichment = None
    try:
        from app.ai.analyzer import process_ai_enrichment
        ai_enrichment = process_ai_enrichment(creator_info, recent_posts)
    except Exception as ai_err:
        print(f"[Facebook Crawler] Lỗi AI Enrichment: {ai_err}")

    return IngestPayload(
        creator=creator_info,
        recentPosts=recent_posts,
        aiEnrichment=ai_enrichment,
        source="PLAYWRIGHT_STEALTH_FACEBOOK"
    )

async def crawl_facebook_profile(username: str) -> Optional[IngestPayload]:
    return await asyncio.to_thread(_crawl_facebook_profile_sync, username)
