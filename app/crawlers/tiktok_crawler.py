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
        browser = p.chromium.launch(headless=True, args=['--disable-gpu', '--no-sandbox', '--disable-dev-shm-usage'])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        # Khởi tạo cookie nếu có
        cookie_path = os.path.join("data", "tiktok_cookies.json")
        if os.path.exists(cookie_path):
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
        def handle_response(response):
            if '/api/post/item_list' in response.url and response.status == 200:
                try:
                    body = response.json()
                    itemList = body.get('itemList', [])
                    for v in itemList[:3]:
                        post_id = str(v.get('id', ''))
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
                
            # Scroll để trigger fetch list video API
            for _ in range(3):
                page.evaluate("window.scrollBy(0, 1500);")
                page.wait_for_timeout(2000)
                
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
            
        finally:
            browser.close()

    # 2. Nếu không lấy được qua Playwright interception, fallback về RapidAPI với retry
    if not recent_posts:
        print(f"No posts in Network Interception, falling back to RapidAPI for {username}")
        try:
            import requests
            from datetime import datetime, timezone
            rapid_url = "https://tiktok-scraper7.p.rapidapi.com/user/posts"
            querystring = {"cursor":"0","user_id":external_id,"count":"3","sort_type":"0"}
            rapidapi_key = os.environ.get("RAPIDAPI_KEY")
            if not rapidapi_key:
                print("RAPIDAPI_KEY is missing from environment variables.")
                rapidapi_key = ""
                
            headers = {
                "x-rapidapi-key": rapidapi_key,
                "x-rapidapi-host": "tiktok-scraper7.p.rapidapi.com"
            }
            
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    response = requests.get(rapid_url, headers=headers, params=querystring, timeout=15)
                    if response.status_code == 200:
                        api_data = response.json()
                        videos = api_data.get("data", {}).get("videos", [])
                        for v in videos[:3]:
                            title = v.get("title", "")
                            if title:
                                recent_videos_for_ai.append(title)
                                
                            create_time = v.get("create_time")
                            posted_at = None
                            if create_time:
                                dt = datetime.fromtimestamp(int(create_time), tz=timezone.utc)
                                posted_at = dt.isoformat()
                                
                            post_id = v.get("video_id", "")
                            post_url = f"https://www.tiktok.com/@{username}/video/{post_id}"
                            
                            recent_posts.append(Post(
                                platformPostId=post_id,
                                postUrl=post_url,
                                caption=title,
                                views=int(v.get("play_count", 0)),
                                likes=int(v.get("digg_count", 0)),
                                comments=int(v.get("comment_count", 0)),
                                shares=int(v.get("share_count", 0)),
                                postedAt=posted_at
                            ))
                        break
                    else:
                        print(f"RapidAPI error {response.status_code}, retrying {attempt+1}/{max_retries}")
                        time.sleep(2)
                except requests.exceptions.RequestException as req_err:
                    print(f"Network error calling RapidAPI: {req_err}")
                    time.sleep(2)
                    
        except Exception as e:
            print(f"General error fetching from RapidAPI: {e}")
            with open("data/rapidapi_error.log", "w") as f:
                f.write(str(e))

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
