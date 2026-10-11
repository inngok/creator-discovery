import asyncio
import re

import undetected_chromedriver as uc
from bs4 import BeautifulSoup

from app.core.logger import setup_logger
from app.core.models import CreatorInfo, IngestPayload, Platform, Post

logger = setup_logger("facebook_crawler")


def get_uc_options():
    options = uc.ChromeOptions()
    options.headless = True
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    return options


def _init_webdriver() -> uc.Chrome:
    driver = None
    try:
        driver = uc.Chrome(options=get_uc_options())
    except Exception as e:
        if (
            "version_main" not in str(e).lower()
            and "supports chrome version" in str(e).lower()
        ):
            match = re.search(r"Current browser version is (\d+)", str(e))
            if match:
                version = int(match.group(1))
                driver = uc.Chrome(options=get_uc_options(), version_main=version)
            else:
                driver = uc.Chrome(options=get_uc_options(), version_main=152)
        else:
            raise e
    driver.set_page_load_timeout(30)
    return driver


def _extract_user_info_from_dom(
    driver: uc.Chrome, username: str, url: str
) -> CreatorInfo:
    content = driver.page_source
    soup = BeautifulSoup(content, "html.parser")

    title_text = soup.title.text if soup.title else username
    display_name = title_text.replace(" | Facebook", "").strip()

    followers = 0
    followers_str = ""

    for t in soup.find_all("a"):
        if t.text and (
            "follower" in t.text.lower() or "người theo dõi" in t.text.lower()
        ):
            match = re.search(
                r"([\d,\.]+[KMB]?)\s+(followers?|người theo dõi)", t.text, re.IGNORECASE
            )
            if match:
                followers_str = match.group(1).upper()
                break

    if not followers_str:
        text_content = soup.get_text(separator=" ")
        followers_match = re.search(
            r"([\d,\.]+[KMB]?)\s+(followers?|người theo dõi)",
            text_content,
            re.IGNORECASE,
        )
        if followers_match:
            followers_str = followers_match.group(1).upper()

    if followers_str:
        multiplier = 1
        if "K" in followers_str:
            multiplier = 1000
        elif "M" in followers_str:
            multiplier = 1000000
        elif "B" in followers_str:
            multiplier = 1000000000
        try:
            num_val = float(re.sub(r"[^0-9\.]", "", followers_str))
            followers = int(num_val * multiplier)
        except Exception as e:
            logger.warning(f"Lỗi khi lấy Followers: {e}")

    if followers < 1000:
        logger.info(
            f"Skipping {username}: only {followers} followers found or cannot determine"
        )

    return CreatorInfo(
        platform=Platform.FACEBOOK,
        externalId=username,
        username=username,
        displayName=display_name,
        profileUrl=url,
        followers=followers,
        bio="",
    )


def _crawl_facebook_profile_sync(username: str) -> IngestPayload | None:
    username = username.strip()
    if "facebook.com" in username:
        match = re.search(r"facebook\.com/([^/?&#]+)", username)
        if match:
            username = match.group(1)

    url = f"https://www.facebook.com/{username}"

    driver = None
    try:
        driver = _init_webdriver()
        driver.get(url)

        # Thêm explicit wait
        import time

        time.sleep(5)

        for _ in range(3):
            driver.execute_script("window.scrollBy(0, 1500);")
            time.sleep(1.5)

        creator_info = _extract_user_info_from_dom(driver, username, url)
        recent_posts: list[Post] = []

        # Logic AI Enrichment đã được tách ra khỏi Crawler
        return IngestPayload(
            creator=creator_info,
            recentPosts=recent_posts,
            aiEnrichment=None,
            source="UNDETECTED_CHROMEDRIVER_FACEBOOK",
        )

    except Exception as e:
        logger.error(f"Error crawling Facebook for {username}: {e}")
        return None
    finally:
        if driver:
            try:
                driver.quit()
            except Exception as e:
                logger.warning(f"Error closing driver: {e}")


async def crawl_facebook_profile(username: str) -> IngestPayload | None:
    return await asyncio.to_thread(_crawl_facebook_profile_sync, username)
