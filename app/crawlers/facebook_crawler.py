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

def _crawl_facebook_profile_sync(username: str) -> Optional[IngestPayload]:
    username = username.strip()
    if "facebook.com" in username:
        match = re.search(r'facebook\.com/([^/?&#]+)', username)
        if match:
            username = match.group(1)
            
    url = f"https://www.facebook.com/{username}"
    
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
                    driver = uc.Chrome(options=get_uc_options(), version_main=152) # adjust default version as needed
            else:
                raise e
                
        driver.set_page_load_timeout(30)
        driver.get(url)
        
        # Give Facebook some time to load
        time.sleep(5)
            
        for _ in range(3):
            driver.execute_script("window.scrollBy(0, 1500);")
            time.sleep(1.5)
            
        content = driver.page_source
        soup = BeautifulSoup(content, 'html.parser')
        
        # Facebook structure is heavily obfuscated.
        # This is a basic approach to extract title (name).
        title_text = soup.title.text if soup.title else username
        display_name = title_text.replace(" | Facebook", "").strip()
        
        # Try to find followers (very basic regex on text content, may need refinement)
        followers = 0
        followers_str = ""
        
        # Method 1: Look in 'a' tags
        for t in soup.find_all('a'):
            if t.text and ('follower' in t.text.lower() or 'người theo dõi' in t.text.lower()):
                match = re.search(r'([\d,\.]+[KMB]?)\s+(followers?|người theo dõi)', t.text, re.IGNORECASE)
                if match:
                    followers_str = match.group(1).upper()
                    break
        
        # Method 2: Look in full text if Method 1 fails
        if not followers_str:
            text_content = soup.get_text(separator=' ')
            followers_match = re.search(r'([\d,\.]+[KMB]?)\s+(followers?|người theo dõi)', text_content, re.IGNORECASE)
            if followers_match:
                followers_str = followers_match.group(1).upper()
                
        if followers_str:
            multiplier = 1
            if 'K' in followers_str: multiplier = 1000
            elif 'M' in followers_str: multiplier = 1000000
            elif 'B' in followers_str: multiplier = 1000000000
            try:
                num_val = float(re.sub(r'[^0-9\.]', '', followers_str))
                followers = int(num_val * multiplier)
            except:
                pass
                
        # If we can't find followers or it's below a threshold (optional)
        if followers < 1000:
            print(f"Skipping {username}: only {followers} followers found or cannot determine")
            # Might still want to return data for testing, so we don't abort immediately.
        
        creator_info = CreatorInfo(
            platform=Platform.FACEBOOK,
            externalId=username, # Using username as externalId since true ID might be hidden
            username=username,
            displayName=display_name,
            profileUrl=url,
            followers=followers,
            bio="" # Bio extraction requires specific targeting
        )

        recent_posts: List[Post] = []
        recent_posts_for_ai: List[str] = []
        
        # We try to extract text from generic post-like elements.
        # Facebook uses classes like x1yztavo, etc. It's better to look for generic links or text.
        # For now, just a placeholder.
        
        # TODO: parse specific posts if possible.
        
        # Basic AI enrichment
        ai_enrichment = None
        if recent_posts_for_ai:
            ai_categories = categorize_creator(display_name, recent_posts_for_ai)
            if ai_categories:
                creator_type = ai_categories[0].strip().upper()
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
            source="UNDETECTED_CHROMEDRIVER_FACEBOOK"
        )

    except Exception as e:
        print(f"Error crawling Facebook for {username}: {e}")
        return None
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

async def crawl_facebook_profile(username: str) -> Optional[IngestPayload]:
    return await asyncio.to_thread(_crawl_facebook_profile_sync, username)
