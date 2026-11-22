import json
import re
import urllib.parse
import urllib.request
from typing import Any, Optional

from app.core.logger import setup_logger
from app.core.post_metrics_models import MetricStatusEnum
from app.crawlers.search_extractor import parse_follower_string
from app.services.post_metrics.builder import build_success_payload
from app.services.post_metrics.parsers import parse_regex_int, parse_regex_str
from app.services.post_metrics.url_validator import PostMetricsException

logger = setup_logger("youtube_post_tracker")
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _fetch_youtube_oembed(url: str) -> dict[str, Any]:
    try:
        oembed_url = f"https://www.youtube.com/oembed?url={urllib.parse.quote(url)}&format=json"
        req = urllib.request.Request(oembed_url, headers={"User-Agent": DEFAULT_USER_AGENT})
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise PostMetricsException("POST_NOT_FOUND", "Video YouTube không tồn tại hoặc đã bị xóa", 404)
    except Exception:
        pass
    return {}


def _extract_youtube_likes(content: str) -> Optional[int]:
    """Trích xuất lượt thích YouTube qua nhiều biến thể format giao diện."""
    # Pattern 1: desktop text "like this video along with X other" (+1 cho chính người bấm)
    m1 = re.search(r'like this video along with ([\d.,]+) other', content, re.IGNORECASE)
    if m1:
        val = parse_follower_string(m1.group(1))
        return val + 1 if val is not None else None

    # Pattern 2: accessibilityText
    m2 = re.search(r'"accessibilityText":\s*"like this video along with ([\d.,]+) other', content, re.IGNORECASE)
    if m2:
        val = parse_follower_string(m2.group(1))
        return val + 1 if val is not None else None

    # Pattern 3 & 4: nút Like tiêu chuẩn
    for pat in (
        r'"defaultText":\s*\{\s*"accessibility":\s*\{\s*"accessibilityData":\s*\{\s*"label":\s*"([\d.,]+[kmb]?)\s*likes?"',
        r'"label":\s*"([\d.,]+[kmb]?)\s*likes?"',
        r'"iconName":\s*"LIKE",\s*"title":\s*"([\d.,]+[kmb]?)"',
    ):
        val = parse_regex_int(pat, content)
        if val is not None:
            return val
    return None


def fetch_youtube_post(url: str, canonical_url: str, post_id: str, author_hint: str) -> dict[str, Any]:
    """Cào số liệu video / Short YouTube thời gian thực."""
    oembed_data = _fetch_youtube_oembed(url)
    title = oembed_data.get("title", "")
    author_name = oembed_data.get("author_name") or author_hint
    thumbnail = oembed_data.get("thumbnail_url", "")

    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    channel_id: Optional[str] = None
    posted_at: Optional[str] = None

    views_status = MetricStatusEnum.FETCH_FAILED
    likes_status = MetricStatusEnum.FETCH_FAILED
    comments_status = MetricStatusEnum.FETCH_FAILED
    shares_status = MetricStatusEnum.NOT_SUPPORTED  # YouTube không hỗ trợ counter share công khai

    content = ""
    try:
        fetch_req = urllib.request.Request(
            canonical_url,
            headers={"User-Agent": DEFAULT_USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        )
        with urllib.request.urlopen(fetch_req, timeout=8) as resp:
            if resp.status == 200:
                content = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise PostMetricsException("POST_NOT_FOUND", "Video YouTube không tồn tại", 404)
    except Exception as e:
        logger.debug(f"[TRACKER] YouTube fetch exception: {e}")

    if content:
        # Kiểm tra playabilityStatus của YouTube
        play_status = parse_regex_str(r'"playabilityStatus":\s*\{\s*"status":\s*"([^"]+)"', content).upper()
        if play_status in ("ERROR", "UNPLAYABLE", "LOGIN_REQUIRED"):
            raise PostMetricsException("POST_PRIVATE_OR_UNAVAILABLE", f"Video YouTube ở chế độ riêng tư hoặc không khả dụng ({play_status})", 404)

        # Trích xuất views
        view_cnt = parse_regex_str(r'"viewCount":\s*"(\d+)"', content)
        if view_cnt.isdigit():
            views = int(view_cnt)
            views_status = MetricStatusEnum.AVAILABLE

        # Trích xuất likes
        parsed_likes = _extract_youtube_likes(content)
        if parsed_likes is not None:
            likes = parsed_likes
            likes_status = MetricStatusEnum.AVAILABLE

        # Trích xuất thời điểm đăng
        date_str = parse_regex_str(r'"(uploadDate|publishDate)":\s*"([^"]+)"', content, group_idx=2)
        if date_str:
            posted_at = date_str

        # Trích xuất Channel ID thật
        ext_channel = parse_regex_str(r'"externalChannelId":\s*"([^"]+)"', content)
        channel_id = ext_channel or None

        # Trích xuất comments
        comm_cnt = parse_regex_str(r'"totalComments":\s*"(\d+)"', content)
        if comm_cnt.isdigit():
            comments = int(comm_cnt)
            comments_status = MetricStatusEnum.AVAILABLE
        else:
            comm_alt = parse_regex_int(r'([\d.,]+)\s*Comments', content)
            if comm_alt is not None:
                comments = comm_alt
                comments_status = MetricStatusEnum.AVAILABLE

    return build_success_payload(
        platform="YOUTUBE",
        post_url=url,
        canonical_post_url=canonical_url,
        post_id=post_id,
        author={"username": author_name, "displayName": author_name, "externalId": channel_id},
        views=views,
        likes=likes,
        comments=comments,
        shares=None,
        views_status=views_status,
        likes_status=likes_status,
        comments_status=comments_status,
        shares_status=shares_status,
        caption=title,
        thumbnail_url=thumbnail,
        posted_at=posted_at,
        method="SSR",
    )
