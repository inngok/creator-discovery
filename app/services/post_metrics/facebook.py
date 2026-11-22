import urllib.request
from typing import Any, Optional

from bs4 import BeautifulSoup

from app.core.logger import setup_logger
from app.core.post_metrics_models import MetricStatusEnum
from app.services.post_metrics.builder import build_success_payload
from app.services.post_metrics.parsers import extract_meta_content, parse_regex_int
from app.services.post_metrics.url_validator import PostMetricsException

logger = setup_logger("facebook_post_tracker")
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _fetch_playwright_facebook(canonical_url: str) -> str:
    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth
        from app.crawlers.facebook_crawler import _init_facebook_playwright_context

        with sync_playwright() as p:
            context, _ = _init_facebook_playwright_context(p)
            page = context.new_page()
            Stealth().apply_stealth_sync(page)
            page.goto(canonical_url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
            content = page.content()
            context.browser.close()
            return content
    except Exception as e:
        logger.debug(f"[TRACKER] Playwright Facebook fallback failed: {e}")
        return ""


def fetch_facebook_post(url: str, canonical_url: str, post_id: str, author_hint: str) -> dict[str, Any]:
    """Cào số liệu bài đăng / Reel Facebook phẳng và sạch (0 if-else lồng nhau)."""
    caption = ""
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    views: Optional[int] = None
    method = "SSR"

    content = ""
    try:
        req = urllib.request.Request(
            canonical_url,
            headers={"User-Agent": DEFAULT_USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            if resp.status == 200:
                content = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise PostMetricsException("POST_NOT_FOUND", "Bài đăng Facebook không tồn tại", 404)
        if e.code in (401, 403):
            pw_content = _fetch_playwright_facebook(canonical_url)
            if pw_content:
                content = pw_content
                method = "PLAYWRIGHT"
            else:
                raise PostMetricsException("UPSTREAM_UNAVAILABLE", "Facebook yêu cầu đăng nhập hoặc chặn truy cập", 503, retryable=True, retryAfterSeconds=60)
    except Exception as e:
        logger.debug(f"[TRACKER] Facebook fetch exception: {e}")

    if content:
        soup = BeautifulSoup(content, "html.parser")
        caption = extract_meta_content(soup, 'meta[name="description"], meta[property="og:description"]')
        desc_text = caption
        likes = parse_regex_int(r"([\d.,]+[kmb]?)\s*(?:likes|thích|reactions)", desc_text)
        comments = parse_regex_int(r"([\d.,]+[kmb]?)\s*(?:comments|bình luận)", desc_text)
        shares = parse_regex_int(r"([\d.,]+[kmb]?)\s*(?:shares|chia sẻ)", desc_text)

    likes_status = MetricStatusEnum.AVAILABLE if likes is not None else MetricStatusEnum.FETCH_FAILED
    comments_status = MetricStatusEnum.AVAILABLE if comments is not None else MetricStatusEnum.FETCH_FAILED
    shares_status = MetricStatusEnum.AVAILABLE if shares is not None else MetricStatusEnum.FETCH_FAILED
    views_status = MetricStatusEnum.NOT_AVAILABLE

    return build_success_payload(
        platform="FACEBOOK",
        post_url=url,
        canonical_post_url=canonical_url,
        post_id=post_id,
        author={"username": author_hint, "displayName": author_hint, "externalId": None},
        views=views,
        likes=likes,
        comments=comments,
        shares=shares,
        views_status=views_status,
        likes_status=likes_status,
        comments_status=comments_status,
        shares_status=shares_status,
        caption=caption,
        method=method,
    )
