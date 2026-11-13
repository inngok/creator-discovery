import re
from bs4 import BeautifulSoup

from app.ai.rules.rule_config import (
    MIN_FOLLOWERS_THRESHOLD,
    REGEX_COMMERCIAL_SELLER,
    REGEX_STATE_MEDIA,
)
from app.core.logger import setup_logger

logger = setup_logger("search_extractor")


def parse_follower_string(val: str) -> int:
    """Chuyển đổi chuỗi follower dạng '120.5K', '1.2M', '50.2 Triệu', '150K người theo dõi' sang số nguyên."""
    if not val:
        return 0
    clean = val.lower().strip()

    # Thử regex bắt cụm [con số] [đơn vị k/m/b/tr/triệu] đi liền hoặc trước cụm người theo dõi/lượt thích
    match = re.search(
        r"([\d.,]+)\s*([kmb]|triệu|tr)?\s*(?:người theo dõi|follower|followers|lượt theo dõi|lượt thích|like|likes)?",
        clean,
    )
    if match and match.group(1):
        num_str = match.group(1).rstrip(".,")
        unit = (match.group(2) or "").lower()

        # Chuẩn hóa dấu phân cách
        if "," in num_str and "." not in num_str:
            parts = num_str.split(",")
            if len(parts) == 2 and len(parts[1]) <= 2:
                num_str = f"{parts[0]}.{parts[1]}"
            else:
                num_str = num_str.replace(",", "")
        elif "." in num_str and "," not in num_str:
            parts = num_str.split(".")
            if len(parts) == 2 and len(parts[1]) == 3 and not unit:
                num_str = "".join(parts)

        multiplier = 1
        if unit in ["triệu", "tr", "m"]:
            multiplier = 1_000_000
        elif unit == "k":
            multiplier = 1_000
        elif unit == "b":
            multiplier = 1_000_000_000

        try:
            return int(float(num_str) * multiplier)
        except Exception:
            pass

    return 0


def extract_search_card_candidates(html: str) -> list[dict]:
    """
    Bóc tách các thẻ Candidate từ trang kết quả tìm kiếm TikTok User (/search/user?q=...).
    Không tốn thêm request, tận dụng toàn bộ metadata SSR đã render trên search page.
    """
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    seen_users = set()

    # Tìm các link profile
    user_links = soup.find_all("a", href=True)
    for a in user_links:
        href = a.get("href", "")
        if not href.startswith("/@"):
            continue

        username = href.split("?")[0].replace("/@", "").strip()
        if not username or username in seen_users:
            continue
        seen_users.add(username)

        # Lấy container cha chứa toàn bộ card
        parent_card = a.find_parent("div")
        card_text = parent_card.get_text(separator=" ") if parent_card else ""

        # Tìm display name và follower string
        display_name = a.get_text(strip=True) or username
        bio_snippet = ""
        followers = 0

        # Thử tìm các đoạn text mô tả và số follower lân cận
        if parent_card:
            text_lines = [line.strip() for line in parent_card.stripped_strings if line.strip()]
            for line in text_lines:
                if any(kw in line.lower() for kw in ["follower", "theo dõi", "fl"]):
                    followers = parse_follower_string(line)
                elif line != display_name and line != f"@{username}" and len(line) > 10:
                    bio_snippet = line

        candidates.append({
            "username": username,
            "display_name": display_name,
            "bio_snippet": bio_snippet,
            "followers": followers,
            "profile_url": f"https://www.tiktok.com/@{username}",
            "platform": "TIKTOK",
        })

    return candidates


FACEBOOK_EXCLUDED_SLUGS = {
    "search", "groups", "watch", "events", "gaming", "marketplace",
    "help", "login", "policies", "privacy", "friends", "messages",
    "notifications", "settings", "bookmarks", "saved", "pages", "live", "reels",
    "home.php", "profile.php", "sharer.php"
}


def extract_facebook_search_card_candidates(html: str) -> list[dict]:
    """
    Bóc tách các Fanpage / Creator từ trang kết quả tìm kiếm Facebook Pages (/search/pages/?q=...).
    Tận dụng toàn bộ metadata SSR và thẻ hiển thị trên search page.
    """
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    seen_slugs = set()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue

        clean = href.split("?")[0].rstrip("/")
        if "facebook.com/" in clean:
            slug = clean.split("facebook.com/")[-1].strip()
        elif clean.startswith("/"):
            slug = clean.lstrip("/").strip()
        else:
            continue

        if not slug or "/" in slug:
            continue

        slug_lower = slug.lower()
        if slug_lower in FACEBOOK_EXCLUDED_SLUGS or slug_lower in seen_slugs:
            continue
        if len(slug) < 3 or not re.match(r"^[a-zA-Z0-9._-]+$", slug):
            continue

        seen_slugs.add(slug_lower)

        parent_card = a.find_parent("div")
        display_name = a.get_text(strip=True) or slug
        snippet_parts = []
        followers = 0

        if parent_card:
            text_lines = [line.strip() for line in parent_card.stripped_strings if line.strip()]
            for line in text_lines:
                if any(kw in line.lower() for kw in ["người theo dõi", "followers", "lượt thích", "likes"]):
                    if not followers:
                        followers = parse_follower_string(line)
                elif line != display_name and len(line) > 3:
                    snippet_parts.append(line)

        bio_snippet = " · ".join(snippet_parts)

        candidates.append({
            "username": slug,
            "display_name": display_name,
            "bio_snippet": bio_snippet,
            "followers": followers,
            "profile_url": f"https://www.facebook.com/{slug}",
            "platform": "FACEBOOK",
        })

    return candidates


def extract_facebook_hashtag_candidates(html: str) -> list[dict]:
    """Read post authors, excluding commenters and navigation links."""
    from urllib.parse import urljoin, urlsplit, parse_qs

    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    seen = set()
    for article in soup.select('[role="article"]'):
        for anchor in article.select('h2 a[href], h3 a[href], h4 a[href]'):
            parsed = urlsplit(urljoin("https://www.facebook.com/", anchor["href"]))
            if parsed.hostname not in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
                continue
            slug = parsed.path.strip("/")
            if slug == "profile.php":
                username = parse_qs(parsed.query).get("id", [""])[0]
                if not username.isdigit():
                    continue
                profile_url = f"https://www.facebook.com/profile.php?id={username}"
            else:
                username = slug
                if (slug.lower() in FACEBOOK_EXCLUDED_SLUGS
                        or not re.fullmatch(r"[a-zA-Z0-9._-]{3,}", slug)):
                    continue
                profile_url = f"https://www.facebook.com/{username}"
            if username.casefold() in seen:
                continue
            seen.add(username.casefold())
            candidates.append({
                "username": username,
                "display_name": anchor.get_text(" ", strip=True) or username,
                "bio_snippet": "",
                "followers": 0,
                "profile_url": profile_url,
                "platform": "FACEBOOK",
            })
    return candidates


def evaluate_tier0_search_card(candidate: dict) -> tuple[bool, str]:
    """
    Bộ lọc Nấc 0 (Zero-Cost Pre-Filter):
    Đánh giá nhanh ứng viên ngay tại Search Card mà không cần gửi bất kỳ HTTP request nào đến trang Profile.
    """
    username = candidate.get("username", "")
    bio_snippet = candidate.get("bio_snippet", "").lower()
    followers = candidate.get("followers", 0)

    # 1. Chặn Follower thấp nếu trang search đã hiển thị
    if 0 < followers < MIN_FOLLOWERS_THRESHOLD:
        return False, f"DROP_LOW_FOLLOWERS_{followers}"

    # 2. Chặn Brand / Shop bán sỉ lộ liễu ngay trong Bio Snippet
    if REGEX_COMMERCIAL_SELLER.search(bio_snippet):
        return False, "DROP_COMMERCIAL_SELLER_SNIPPET"

    # 3. Chặn Cơ quan nhà nước / Báo chí
    if REGEX_STATE_MEDIA.search(bio_snippet):
        return False, "DROP_STATE_MEDIA_SNIPPET"

    return True, "PASS_TIER0_PREFILTER"
