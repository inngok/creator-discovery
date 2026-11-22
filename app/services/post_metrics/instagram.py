import urllib.request
from typing import Any, Optional

from bs4 import BeautifulSoup

from app.core.logger import setup_logger
from app.core.post_metrics_models import MetricStatusEnum
from app.services.post_metrics.builder import build_success_payload
from app.services.post_metrics.parsers import extract_meta_content, parse_regex_int, parse_regex_str
from app.services.post_metrics.url_validator import PostMetricsException

logger = setup_logger("instagram_post_tracker")
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _fetch_playwright_instagram(canonical_url: str) -> str:
    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth
        from app.crawlers.instagram_crawler import _init_instagram_playwright_context

        with sync_playwright() as p:
            context, _ = _init_instagram_playwright_context(p)
            page = context.new_page()
            Stealth().apply_stealth_sync(page)
            page.goto(canonical_url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
            content = page.content()
            context.browser.close()
            return content
    except Exception as e:
        logger.debug(f"[TRACKER] Playwright Instagram fallback failed: {e}")
        return ""


def fetch_instagram_post(url: str, canonical_url: str, post_id: str, author_hint: str) -> dict[str, Any]:
    """Cào số liệu bài đăng / Reel Instagram sạch và phẳng (0 if-else lồng nhau)."""
    likes: Optional[int] = None
    comments: Optional[int] = None
    caption = ""
    author = author_hint
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
            raise PostMetricsException("POST_NOT_FOUND", "Bài đăng Instagram không tồn tại", 404)
        if e.code in (401, 403):
            # Fallback sang Playwright với cookie/session đã lưu
            pw_content = _fetch_playwright_instagram(canonical_url)
            if pw_content:
                content = pw_content
                method = "PLAYWRIGHT"
            else:
                raise PostMetricsException("UPSTREAM_UNAVAILABLE", "Instagram yêu cầu đăng nhập hoặc chặn crawler công khai", 503, retryable=True, retryAfterSeconds=60)
    except Exception as e:
        logger.debug(f"[TRACKER] Instagram fetch exception: {e}")

    # Bóc tách metadata phẳng không lồng nhau
    if content:
        soup = BeautifulSoup(content, "html.parser")
        desc = extract_meta_content(soup, 'meta[property="og:description"], meta[name="description"]')
        caption = extract_meta_content(soup, 'meta[property="og:title"]')

        likes = parse_regex_int(r"([\d.,]+[kmb]?)\s*likes?", desc)
        comments = parse_regex_int(r"([\d.,]+[kmb]?)\s*comments?", desc)
        author = parse_regex_str(r"-\s*([a-zA-Z0-9._]+)\s*on Instagram", desc) or author_hint

    likes_status = MetricStatusEnum.AVAILABLE if likes is not None else MetricStatusEnum.FETCH_FAILED
    comments_status = MetricStatusEnum.AVAILABLE if comments is not None else MetricStatusEnum.FETCH_FAILED
    views_status = MetricStatusEnum.NOT_AVAILABLE  # Instagram không cung cấp public views trong thẻ meta
    shares_status = MetricStatusEnum.NOT_SUPPORTED  # Instagram không hỗ trợ public share count

    return build_success_payload(
        platform="INSTAGRAM",
        post_url=url,
        canonical_post_url=canonical_url,
        post_id=post_id,
        author={"username": author, "displayName": author, "externalId": None},
        views=None,
        likes=likes,
        comments=comments,
        shares=None,
        views_status=views_status,
        likes_status=likes_status,
        comments_status=comments_status,
        shares_status=shares_status,
        caption=caption,
        method=method,
    )
