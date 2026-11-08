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
    """Chuyển đổi chuỗi follower dạng '120.5K', '1.2M', '50.2 Triệu' sang số nguyên."""
    if not val:
        return 0
    clean = val.lower().strip()
    # Loại bỏ các chữ 'followers', 'người theo dõi'
    clean = re.sub(r"[^\d.,kmbtrtriệu]", "", clean)

    multiplier = 1
    if "triệu" in clean or "tr" in clean or "m" in clean:
        multiplier = 1_000_000
        clean = re.sub(r"[m|tr|triệu]", "", clean)
    elif "k" in clean:
        multiplier = 1_000
        clean = re.sub(r"k", "", clean)
    elif "b" in clean:
        multiplier = 1_000_000_000
        clean = re.sub(r"b", "", clean)

    clean = clean.replace(",", ".")
    try:
        return int(float(clean) * multiplier)
    except Exception:
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
