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
from app.ai.analyzer import process_ai_enrichment

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
        
        # 1. Trích xuất URL và tiêu đề của 3 video gần nhất TỪ DỮ LIỆU JSON (Chính xác 100%)
        videos_to_fetch = []
        item_list = data.get('__DEFAULT_SCOPE__', {}).get('webapp.user-detail', {}).get('itemList', [])
        
        for idx, item in enumerate(item_list):
            if idx >= 3:
                break
                
            video_id = item.get('id', f'unknown_{idx}')
            post_url = f"{url}/video/{video_id}"
            title = item.get('desc', '')
            
            if title:
                recent_videos_for_ai.append(title)
                
            stats = item.get('stats', {})
            views = int(stats.get('playCount', 0))
            
            videos_to_fetch.append({
                "url": post_url,
                "title": title,
                "views": views,
                "id": video_id
            })
            
        # 2. Truy cập từng trang video để lấy thông tin chi tiết
        from datetime import datetime, timezone
        for video in videos_to_fetch:
            posted_at = None
            likes = 0
            comments = 0
            shares = 0
            views = video["views"] # Mặc định dùng views ước lượng từ ngoài profile
            
            try:
                if "unknown" not in video["id"]:
                    driver.get(video["url"])
                    time.sleep(2) # Chờ video page tải HTML ban đầu
                    
                    video_soup = BeautifulSoup(driver.page_source, 'html.parser')
                    script_tag_vid = video_soup.find('script', id='__UNIVERSAL_DATA_FOR_REHYDRATION__')
                    
                    if script_tag_vid:
                        vid_data = json.loads(script_tag_vid.string)
                        item_module = vid_data.get('__DEFAULT_SCOPE__', {}).get('webapp.video-detail', {}).get('itemInfo', {}).get('itemStruct', {})
                        
                        if item_module:
                            create_time = item_module.get('createTime')
                            if create_time:
                                dt = datetime.fromtimestamp(int(create_time), tz=timezone.utc)
                                posted_at = dt.isoformat()
                                
                            stats = item_module.get('stats', {})
                            likes = int(stats.get('diggCount', 0))
                            comments = int(stats.get('commentCount', 0))
                            shares = int(stats.get('shareCount', 0))
                            
                            # Số view chính xác từ API JSON
                            if stats.get('playCount'):
                                views = int(stats.get('playCount', 0))
            except Exception as e:
                print(f"Error fetching detailed video metrics for {video['url']}: {e}")
                
            recent_posts.append(Post(
                platformPostId=video["id"],
                postUrl=video["url"],
                caption=video["title"],
                views=views,
                likes=likes,
                comments=comments,
                shares=shares,
                postedAt=posted_at
            ))

        ai_enrichment = process_ai_enrichment(creator_info, recent_posts)
        
        # Tra ve nguyen ven IngestPayload chua toan bo Thong tin kenh + AI phan tich cho Backend
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
            driver.quit()

async def crawl_tiktok_profile(username: str) -> Optional[IngestPayload]:
    return await asyncio.to_thread(_crawl_tiktok_profile_sync, username)
