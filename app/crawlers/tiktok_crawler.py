import json
import re
import asyncio
import time
from typing import Optional, List
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from app.core.models import IngestPayload, CreatorInfo, Post, AIEnrichment, Platform
from app.ai.analyzer import categorize_creator

def get_uc_options():
    options = uc.ChromeOptions()
    options.headless = True
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    return options

def _crawl_tiktok_profile_sync(username: str) -> Optional[IngestPayload]:
    username = username.strip()
    if "tiktok.com" in username:
        match = re.search(r'tiktok\.com/@?([^/?&#]+)', username)
        if match:
            username = match.group(1)
            
    if username.startswith('@'):
        username = username[1:]
        
    url = f"https://www.tiktok.com/@{username}"
    
    driver = None
    try:
        try:
            driver = uc.Chrome(options=get_uc_options())
        except Exception as e:
            if "version_main" not in str(e).lower() and "supports chrome version" in str(e).lower():
                match = re.search(r'Current browser version is (\d+)', str(e))
                if match:
                    version = int(match.group(1))
                    driver = uc.Chrome(options=get_uc_options(), version_main=version)
                else:
                    driver = uc.Chrome(options=get_uc_options(), version_main=152)
            else:
                raise e
                
        driver.set_page_load_timeout(30)
        # Khởi tạo cookie nếu có
        try:
            import os
            cookie_path = os.path.join("data", "tiktok_cookies.json")
            if os.path.exists(cookie_path):
                # Phải truy cập domain trước khi set cookie
                driver.get("https://www.tiktok.com")
                time.sleep(2)
                with open(cookie_path, 'r', encoding='utf-8') as f:
                    cookies = json.load(f)
                    for cookie in cookies:
                        # Chuẩn hóa cookie cho selenium
                        if 'sameSite' in cookie:
                            if cookie['sameSite'] not in ["Strict", "Lax", "None"]:
                                del cookie['sameSite']
                        try:
                            driver.add_cookie(cookie)
                        except Exception as e:
                            pass
                print(f"Loaded cookies from {cookie_path}")
        except Exception as e:
            print(f"Error loading cookies: {e}")

        driver.get(url)
        
        try:
            WebDriverWait(driver, 10).until(
                lambda d: d.find_element(By.ID, '__UNIVERSAL_DATA_FOR_REHYDRATION__') or 
                          d.find_element(By.CSS_SELECTOR, '[data-e2e="user-title"]')
            )
        except Exception:
            pass
            
        for _ in range(3):
            driver.execute_script("window.scrollBy(0, 1500);")
            time.sleep(1.5)
            
        content = driver.page_source
        soup = BeautifulSoup(content, 'html.parser')
        script_tag = soup.find('script', id='__UNIVERSAL_DATA_FOR_REHYDRATION__')
        
        if not script_tag:
            return None
            
        raw_data = script_tag.string
        data = json.loads(raw_data)
        with open("data/dump.json", "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2))
        
        user_module = data.get('__DEFAULT_SCOPE__', {}).get('webapp.user-detail', {}).get('userInfo', {})
        stats_module = user_module.get('stats', {})
        user_info = user_module.get('user', {})

        if not user_info:
             return None

        external_id = str(user_info.get('id', ''))
        followers = int(stats_module.get('followerCount', 0))
        
        if followers < 7000:
            print(f"Skipping {username}: only {followers} followers (needs >= 7000)")
            return None
        
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

        recent_posts: List[Post] = []
        recent_videos_for_ai: List[str] = []
        
        # 1. Trích xuất URL và tiêu đề của 3 video gần nhất bằng RapidAPI
        try:
            import requests
            from datetime import datetime, timezone
            rapid_url = "https://tiktok-scraper7.p.rapidapi.com/user/posts"
            querystring = {"cursor":"0","user_id":external_id,"count":"3","sort_type":"0"}
            headers = {
                "x-rapidapi-key": "63ac913842msh6108843634a03a4p19f7c1jsn705e8f628b13",
                "x-rapidapi-host": "tiktok-scraper7.p.rapidapi.com"
            }
            response = requests.get(rapid_url, headers=headers, params=querystring)
            if response.status_code == 200:
                data = response.json()
                videos = data.get("data", {}).get("videos", [])
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
        except Exception as e:
            print(f"Error fetching videos from RapidAPI: {e}")
            with open("data/rapidapi_error.log", "w") as f:
                f.write(str(e))

        ai_categories = categorize_creator(signature, recent_videos_for_ai)
        ai_enrichment = None
        if ai_categories:
            creator_type = ai_categories[0].strip().upper()
            
            if creator_type in ["BRAND", "NORMAL_USER", "STORE"]:
                print(f"Skipping {username}: AI classified as {creator_type}")
                return None
                
            ai_enrichment = AIEnrichment(
                tags=ai_categories[1:],
                creatorType=creator_type,
                niche=ai_categories[1] if len(ai_categories) > 1 else None,
                category=ai_categories[2] if len(ai_categories) > 2 else None,
            )

        return IngestPayload(
            creator=creator_info,
            recentPosts=recent_posts,
            aiEnrichment=ai_enrichment,
            source="UNDETECTED_CHROMEDRIVER_TIKTOK"
        )

    except Exception as e:
        print(f"Error crawling TikTok for {username}: {e}")
        return None
    finally:
        if driver:
            try:
                import os
                if hasattr(driver, 'browser_pid'):
                    os.system(f"taskkill /F /PID {driver.browser_pid} /T >nul 2>&1")
            except:
                pass
            try:
                driver.quit()
            except:
                pass

async def crawl_tiktok_profile(username: str) -> Optional[IngestPayload]:
    return await asyncio.to_thread(_crawl_tiktok_profile_sync, username)
