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

def _crawl_tiktok_profile_sync(username: str) -> Optional[IngestPayload]:
    username = username.strip()
    if "tiktok.com" in username:
        match = re.search(r'tiktok\.com/@?([^/?&#]+)', username)
        if match:
            username = match.group(1)
            
    if username.startswith('@'):
        username = username[1:]
        
    url = f"https://www.tiktok.com/@{username}"
    
    recent_posts: List[Post] = []
    recent_videos_for_ai: List[str] = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--disable-gpu', '--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled'])
        state_path = os.path.join("data", "playwright_state.json")
        context_args = {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "viewport": {'width': 1280, 'height': 720}
        }
        if os.path.exists(state_path):
            context_args["storage_state"] = state_path
            
        context = browser.new_context(**context_args)
        
        # Khởi tạo cookie nếu có
        cookie_path = os.path.join("data", "tiktok_cookies.json")
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
                print(f"Loaded cookies from {cookie_path}")
            except Exception as e:
                print(f"Error loading cookies: {e}")

        page = context.new_page()
        stealth = Stealth()
        stealth.apply_stealth_sync(page)

        # Chặn bắt (Intercept) request API để lấy danh sách video nhanh gọn
        # Dùng set để tránh trùng lặp
        seen_post_ids = set()
        
        def handle_response(response):
            if 'api' in response.url and 'item_list' in response.url and response.status == 200:
                try:
                    text = response.text()
                    if not text:
                        return
                    body = json.loads(text)
                    itemList = body.get('itemList', [])
                    for v in itemList:
                        if len(recent_posts) >= 3:
                            break
                        post_id = str(v.get('id', ''))
                        if post_id in seen_post_ids:
                            continue
                        seen_post_ids.add(post_id)
                        title = v.get('desc', '')
                        if title:
                            recent_videos_for_ai.append(title)
                        
                        create_time = v.get('createTime')
                        posted_at = None
                        if create_time:
                            from datetime import datetime, timezone
                            dt = datetime.fromtimestamp(int(create_time), tz=timezone.utc)
                            posted_at = dt.isoformat()
                            
                        stats = v.get('stats', {})
                        recent_posts.append(Post(
                            platformPostId=post_id,
                            postUrl=f"https://www.tiktok.com/@{username}/video/{post_id}",
                            caption=title,
                            views=int(stats.get('playCount', 0)),
                            likes=int(stats.get('diggCount', 0)),
                            comments=int(stats.get('commentCount', 0)),
                            shares=int(stats.get('shareCount', 0)),
                            postedAt=posted_at
                        ))
                except Exception as e:
                    print(f"Error parsing intercept response: {e}")

        page.on("response", handle_response)
        
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            
            try:
                page.wait_for_selector('#__UNIVERSAL_DATA_FOR_REHYDRATION__, [data-e2e="user-title"]', timeout=10000)
            except PlaywrightTimeoutError:
                pass
                
            page.wait_for_timeout(3000) # Cho API kịp phản hồi
                
            # Scroll để trigger fetch list video API
            import random
            for _ in range(3):
                if len(recent_posts) >= 3:
                    break
                scroll_amount = random.randint(800, 1500)
                page.evaluate(f"window.scrollBy(0, {scroll_amount});")
                page.wait_for_timeout(random.randint(2000, 4000))
                
            content = page.content()
            soup = BeautifulSoup(content, 'html.parser')
            script_tag = soup.find('script', id='__UNIVERSAL_DATA_FOR_REHYDRATION__')
            
            if not script_tag:
                raise ValueError("script_tag __UNIVERSAL_DATA_FOR_REHYDRATION__ not found in page source. Possibly blocked by TikTok CAPTCHA.")
                
            raw_data = script_tag.string
            if not raw_data:
                raise ValueError("script_tag found but contains no data.")
            data = json.loads(raw_data)
            
            if not os.path.exists("data"):
                os.makedirs("data")
            with open("data/dump.json", "w", encoding="utf-8") as f:
                f.write(json.dumps(data, indent=2))
            
            user_module = data.get('__DEFAULT_SCOPE__', {}).get('webapp.user-detail', {}).get('userInfo', {})
            stats_module = user_module.get('stats', {})
            user_info = user_module.get('user', {})

            if not user_info:
                 raise ValueError("user_info not found in __DEFAULT_SCOPE__.webapp.user-detail.userInfo")

            external_id = str(user_info.get('id', ''))
            followers = int(stats_module.get('followerCount', 0))
            
            if followers < 7000:
                print(f"Skipping {username}: only {followers} followers (needs >= 7000)")
                raise ValueError(f"Skipping {username}: only {followers} followers (needs >= 7000)")
            
            signature = user_info.get('signature', '')
            
            email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', signature)
            public_email = email_match.group(0) if email_match else None
            
            phone_match = re.search(r'(?:\+?84|0)(?:\s|\.)?\d{2,3}(?:\s|\.)?\d{3}(?:\s|\.)?\d{3,4}', signature)
            public_phone = phone_match.group(0) if phone_match else None

            creator_info = CreatorInfo(
                platform=Platform.TIKTOK,
                externalId=external_id,
                username=f"@{username}",
                displayName=user_info.get('nickname', username),
                profileUrl=url,
                avatarUrl=user_info.get('avatarLarger', user_info.get('avatarMedium')),
                followers=followers,
                bio=signature,
                publicEmail=public_email,
                publicPhone=public_phone
            )
            
            # Lưu lại trạng thái trình duyệt (Cookies, Token) để lần sau không bị coi là Bot mới
            try:
                context.storage_state(path=state_path)
            except Exception:
                pass
                
        finally:
            browser.close()
            
        # Thêm thời gian nghỉ ngẫu nhiên từ 30 đến 60 giây giữa các lần cào để giãn cách tối đa, tránh bị Rate Limit/Block IP
        import random
        time.sleep(random.uniform(30.0, 60.0))

    # Đã bỏ phần RapidAPI theo yêu cầu để tập trung giải quyết bằng công cụ cào (Playwright)
    if not recent_posts:
        print(f"No posts in Network Interception for {username}. Might be blocked or no posts exist.")

    if not recent_posts and creator_info.bio:
        print("No recent videos found, AI will fallback to using bio only.")
        
    print(f"Running AI enrichment for {username} in Uvicorn...")
    from app.ai.analyzer import process_ai_enrichment
    ai_enrichment = process_ai_enrichment(creator_info, recent_posts)

    return IngestPayload(
        creator=creator_info,
        recentPosts=recent_posts,
        aiEnrichment=ai_enrichment,
        source="PLAYWRIGHT_STEALTH_TIKTOK"
    )

async def crawl_tiktok_profile(username: str) -> Optional[IngestPayload]:
    return await asyncio.to_thread(_crawl_tiktok_profile_sync, username)
