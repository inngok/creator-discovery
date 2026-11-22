import ipaddress
import re
import urllib.parse
from typing import Optional, Tuple


class PostMetricsException(Exception):
    """Custom exception đại diện cho các lỗi nghiệp vụ cào số liệu bài đăng."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        retryable: bool = False,
        retryAfterSeconds: Optional[int] = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.retryAfterSeconds = retryAfterSeconds

    def to_response_dict(self) -> dict:
        return {
            "schemaVersion": "1.1",
            "status": "error",
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "retryAfterSeconds": self.retryAfterSeconds,
            },
            "trackedAt": None,
            "_http_status": self.status_code,
        }


ALLOWED_DOMAINS = {
    "TIKTOK": {"tiktok.com", "www.tiktok.com", "vt.tiktok.com", "vm.tiktok.com", "m.tiktok.com"},
    "YOUTUBE": {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"},
    "FACEBOOK": {"facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com", "fb.watch"},
    "INSTAGRAM": {"instagram.com", "www.instagram.com", "instagr.am"},
}


def is_ip_or_private_host(host: str) -> bool:
    """Kiểm tra host có phải là IP, localhost hoặc dải IP nội bộ/link-local/metadata."""
    clean_host = host.split(":")[0].strip().lower()
    if clean_host in ("localhost", "0.0.0.0", "127.0.0.1", "::1"):
        return True

    try:
        ip = ipaddress.ip_address(clean_host)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
            or str(ip).startswith("169.254.")
        )
    except ValueError:
        return False


def _normalize_tiktok(hostname: str, path: str) -> Tuple[str, str, str]:
    if hostname in ("vt.tiktok.com", "vm.tiktok.com"):
        short_code = path.strip("/").split("/")[0] if path.strip("/") else ""
        if not short_code:
            raise PostMetricsException("INVALID_POST_URL", "URL rút gọn TikTok thiếu mã video", 400)
        return f"https://{hostname}/{short_code}", short_code, ""

    match_vid = re.search(r"/video/(\d+)", path)
    if not match_vid:
        raise PostMetricsException("INVALID_POST_URL", "URL không phải bài đăng video TikTok hợp lệ (thiếu /video/{id})", 400)

    post_id = match_vid.group(1)
    match_author = re.search(r"/@([\w.]+)", path)
    username = match_author.group(1) if match_author else ""
    canonical = f"https://www.tiktok.com/@{username}/video/{post_id}" if username else f"https://www.tiktok.com/video/{post_id}"
    return canonical, post_id, username


def _normalize_youtube(hostname: str, path: str, parsed: urllib.parse.ParseResult) -> Tuple[str, str, str]:
    if hostname == "youtu.be":
        video_id = path.strip("/").split("/")[0] if path.strip("/") else ""
        if not video_id:
            raise PostMetricsException("INVALID_POST_URL", "URL youtu.be thiếu Video ID", 400)
        return f"https://www.youtube.com/watch?v={video_id}", video_id, ""

    match_shorts = re.search(r"/shorts/([a-zA-Z0-9_-]+)", path)
    if match_shorts:
        video_id = match_shorts.group(1)
        return f"https://www.youtube.com/shorts/{video_id}", video_id, ""

    qs = urllib.parse.parse_qs(parsed.query)
    video_ids = qs.get("v")
    if video_ids and video_ids[0]:
        video_id = video_ids[0]
        return f"https://www.youtube.com/watch?v={video_id}", video_id, ""

    raise PostMetricsException("INVALID_POST_URL", "URL không phải video YouTube hợp lệ", 400)


def _normalize_instagram(path: str) -> Tuple[str, str, str]:
    match_ig = re.search(r"/(p|reel|tv)/([a-zA-Z0-9_-]+)", path)
    if not match_ig:
        raise PostMetricsException("INVALID_POST_URL", "URL không phải bài đăng hoặc Reel Instagram hợp lệ", 400)
    post_type, shortcode = match_ig.group(1), match_ig.group(2)
    return f"https://www.instagram.com/{post_type}/{shortcode}/", shortcode, ""


def _normalize_facebook(hostname: str, path: str, parsed: urllib.parse.ParseResult) -> Tuple[str, str, str]:
    if hostname == "fb.watch":
        fb_code = path.strip("/").split("/")[0] if path.strip("/") else ""
        if not fb_code:
            raise PostMetricsException("INVALID_POST_URL", "URL fb.watch thiếu Video ID", 400)
        return f"https://fb.watch/{fb_code}/", fb_code, ""

    qs = urllib.parse.parse_qs(parsed.query)
    if "v" in qs and qs["v"]:
        v_id = qs["v"][0]
        return f"https://www.facebook.com/watch/?v={v_id}", v_id, ""

    match_fb = re.search(r"/(posts|reel|videos)/(\d+)", path)
    if match_fb:
        fb_type, fb_id = match_fb.group(1), match_fb.group(2)
        parts = [p for p in path.strip("/").split("/") if p]
        user_part = parts[0] if parts and parts[0] not in ("posts", "reel", "videos") else ""
        canonical = f"https://www.facebook.com/{user_part}/{fb_type}/{fb_id}" if user_part else f"https://www.facebook.com/{fb_type}/{fb_id}"
        return canonical, fb_id, user_part

    if "story_fbid" in qs and qs["story_fbid"]:
        story_id = qs["story_fbid"][0]
        return f"https://www.facebook.com/permalink.php?story_fbid={story_id}", story_id, ""

    raise PostMetricsException("INVALID_POST_URL", "URL không phải bài đăng Facebook hợp lệ", 400)


def validate_and_normalize_url(post_url: str) -> Tuple[str, str, str, str]:
    """
    Kiểm tra an toàn SSRF, xác định platform, chuẩn hóa canonicalPostUrl và postId.
    Trả về: (platform, canonical_url, post_id, author_hint)
    """
    raw_url = (post_url or "").strip()
    if not raw_url:
        raise PostMetricsException("INVALID_POST_URL", "URL bài đăng không được để trống", 400)

    try:
        parsed = urllib.parse.urlparse(raw_url)
    except Exception as e:
        raise PostMetricsException("INVALID_POST_URL", f"Cấu trúc URL không hợp lệ: {e}", 400)

    if parsed.scheme.lower() not in ("http", "https"):
        raise PostMetricsException("INVALID_POST_URL", f"Giao thức '{parsed.scheme}' không được hỗ trợ (chỉ nhận HTTP/HTTPS)", 400)

    if parsed.username or parsed.password:
        raise PostMetricsException("INVALID_POST_URL", "URL không được chứa credential", 400)

    if parsed.port and parsed.port not in (80, 443):
        raise PostMetricsException("INVALID_POST_URL", f"Cổng {parsed.port} không được phép (chỉ nhận 80/443)", 400)

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise PostMetricsException("INVALID_POST_URL", "URL thiếu hostname", 400)

    if is_ip_or_private_host(hostname):
        raise PostMetricsException("INVALID_POST_URL", "URL trỏ tới localhost hoặc IP nội bộ không được phép", 400)

    # Xác định Platform qua mapping domains
    platform = next(
        (plat for plat, domains in ALLOWED_DOMAINS.items() if hostname in domains or any(hostname.endswith("." + d) for d in domains)),
        None,
    )
    if not platform:
        raise PostMetricsException("UNSUPPORTED_PLATFORM", f"Nền tảng từ domain '{hostname}' không được hỗ trợ", 422)

    path = parsed.path or ""

    # Dispatch bảng ánh xạ 0 if-else lồng nhau
    dispatchers = {
        "TIKTOK": lambda: _normalize_tiktok(hostname, path),
        "YOUTUBE": lambda: _normalize_youtube(hostname, path, parsed),
        "INSTAGRAM": lambda: _normalize_instagram(path),
        "FACEBOOK": lambda: _normalize_facebook(hostname, path, parsed),
    }

    canonical_url, post_id, author_hint = dispatchers[platform]()
    return platform, canonical_url, post_id, author_hint
