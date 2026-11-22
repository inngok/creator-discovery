import re
from typing import Optional
from bs4 import BeautifulSoup

from app.crawlers.search_extractor import parse_follower_string


def extract_meta_content(soup: BeautifulSoup, selector: str) -> str:
    """Trích xuất thuộc tính content từ thẻ meta đầu tiên khớp selector, trả về rỗng nếu không có."""
    if not soup:
        return ""
    elem = soup.select_one(selector)
    return (elem.get("content") or "").strip() if elem else ""


def parse_regex_int(pattern: str, text: str) -> Optional[int]:
    """Tìm số theo regex và parse về integer không âm, trả về None nếu không khớp."""
    if not text:
        return None
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None
    try:
        val = parse_follower_string(match.group(1))
        return val if val >= 0 else None
    except Exception:
        return None


def parse_regex_str(pattern: str, text: str, group_idx: int = 1) -> str:
    """Tìm chuỗi theo regex, trả về chuỗi rỗng nếu không khớp."""
    if not text:
        return ""
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(group_idx).strip() if match else ""
