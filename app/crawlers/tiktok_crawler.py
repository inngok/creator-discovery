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

def get_uc_options():
    options = uc.ChromeOptions()
    options.headless = True
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    return options

def _cleanup_driver(driver):
    if not driver:
        return
    user_data_dir = getattr(driver, 'user_data_dir', None)
    try:
        driver.quit() # Cho phep ChromeDriver tu dong xoa thu muc Temp truoc
    except:
        pass
    
    # Xoa buoc (Force delete) thu muc Temp neu uc.Chrome quen hoac bi taskkill cat ngang
    if user_data_dir:
        import os
        if os.path.exists(user_data_dir):
            import shutil
            try:
                shutil.rmtree(user_data_dir, ignore_errors=True)
            except:
                pass

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
    max_init_retries = 3
    for init_attempt in range(max_init_retries):
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
            break # Success, exit retry loop
        except Exception as e:
            print(f"Failed to initialize Chrome on attempt {init_attempt + 1}: {e}")
            time.sleep(2)
            
    if not driver:
        print(f"Could not initialize Chrome after {max_init_retries} attempts.")
        raise ValueError(f"Could not initialize Chrome after {max_init_retries} attempts.")

    try:
                
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
            
        driver.execute_cdp_cmd('Network.enable', {})
            
        for _ in range(3):
            driver.execute_script("window.scrollBy(0, 1500);")
            time.sleep(2)
            
        content = driver.page_source
        soup = BeautifulSoup(content, 'html.parser')
        script_tag = soup.find('script', id='__UNIVERSAL_DATA_FOR_REHYDRATION__')
        
        if not script_tag:
            raise ValueError("script_tag __UNIVERSAL_DATA_FOR_REHYDRATION__ not found in page source. Possibly blocked by TikTok CAPTCHA.")
            
        raw_data = script_tag.string
        if not raw_data:
            raise ValueError("script_tag found but contains no data.")
        data = json.loads(raw_data)
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

        recent_posts: List[Post] = []
        recent_videos_for_ai: List[str] = []
        
        # 1. Thử lấy trực tiếp từ Network Logs thay vì ItemModule
        try:
            target_req_id = None
            found_posts = False
            for poll_attempt in range(5):
                logs = driver.get_log('performance')
                for entry in logs:
                    log = json.loads(entry['message'])['message']
                    
                    if log['method'] == 'Network.responseReceived':
                        url = log['params']['response']['url']
                        if '/api/post/item_list' in url:
                            target_req_id = log['params']['requestId']
                    
                    if log['method'] == 'Network.loadingFinished' and target_req_id:
                        req_id = log['params']['requestId']
                        if req_id == target_req_id:
                            res = driver.execute_cdp_cmd('Network.getResponseBody', {'requestId': req_id})
                            if res and 'body' in res and res['body']:
                                api_data = json.loads(res['body'])
                                itemList = api_data.get('itemList', [])
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
                                found_posts = True
                                break # Found and parsed!
                if found_posts:
                    break
                time.sleep(1)
        except Exception as e:
            print(f"Error fetching posts from Network Logs: {e}")
            
        # Đóng trình duyệt ngay sau khi lấy được thông tin profile và network logs để giải phóng RAM
        _cleanup_driver(driver)
        driver = None
            
        # 2. Nếu không lấy được, fallback về RapidAPI với retry
        if not recent_posts:
            print(f"No posts in Network Logs, falling back to RapidAPI for {username}")
            try:
                import requests
                from datetime import datetime, timezone
                rapid_url = "https://tiktok-scraper7.p.rapidapi.com/user/posts"
                querystring = {"cursor":"0","user_id":external_id,"count":"3","sort_type":"0"}
                rapidapi_key = os.environ.get("RAPIDAPI_KEY")
                if not rapidapi_key:
                    print("RAPIDAPI_KEY is missing from environment variables.")
                    rapidapi_key = "" # Fail gracefully
                    
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
                            break # Thoát nếu thành công
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

        # Fallback neu khong co video nao (tu mang/rapidapi)
        if not recent_posts and creator_info.bio:
            print("No recent videos found, AI will fallback to using bio only.")
            
        print(f"Running AI enrichment for {username} in Uvicorn...")
        from app.ai.analyzer import process_ai_enrichment
        ai_enrichment = process_ai_enrichment(creator_info, recent_posts)

        return IngestPayload(
            creator=creator_info,
            recentPosts=recent_posts,
            aiEnrichment=ai_enrichment,
            source="UNDETECTED_CHROMEDRIVER_TIKTOK"
        )

    except Exception as e:
        import traceback
        print(f"Error crawling TikTok for {username}: {e}")
        traceback.print_exc()
        raise e
    finally:
        if driver:
            _cleanup_driver(driver)

async def crawl_tiktok_profile(username: str) -> Optional[IngestPayload]:
    return await asyncio.to_thread(_crawl_tiktok_profile_sync, username)
