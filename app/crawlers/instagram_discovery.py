import os
import re
import urllib.parse
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from app.core.logger import setup_logger
from app.crawlers.search_extractor import (
    evaluate_tier0_search_card,
    parse_follower_string,
)

logger = setup_logger("instagram_discovery")

INSTAGRAM_EXCLUDED_SLUGS = {
    "explore",
    "reels",
    "stories",
    "accounts",
    "p",
    "reel",
    "direct",
    "legal",
    "about",
    "developer",
    "help",
    "support",
    "privacy",
    "terms",
    "api",
    "challenge",
    "emails",
    "login",
    "directory",
    "settings",
    "feed",
    "tv",
    "tags",
}


class InstagramDiscoveryError(RuntimeError):
    """Lỗi khi thực hiện tìm kiếm / discovery KOC Instagram mới."""


def _clean_instagram_username(raw: str) -> str | None:
    """Chuẩn hóa và lọc username Instagram hợp lệ."""
    if not raw:
        return None
    slug = raw.strip().removeprefix("@").split("/")[0].split("?")[0].strip()
    slug_lower = slug.lower()
    if slug_lower in INSTAGRAM_EXCLUDED_SLUGS:
        return None
    # Instagram username: 1-30 chars, alphanumeric + dots + underscores
    if not re.fullmatch(r"[a-zA-Z0-9._]{3,30}", slug):
        return None
    return slug


def _extract_candidates_from_search_html(html: str) -> list[dict]:
    """
    Bóc tách các profile Instagram từ trang kết quả tìm kiếm (Google / DuckDuckGo / Meta search).
    Trích xuất kèm số follower và bio nếu có trong đoạn trích (snippet).
    """
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[dict] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Hỗ trợ cả link trực tiếp và link redirect dạng /url?q=https://www.instagram.com/...
        if "instagram.com" not in href:
            continue

        match = re.search(r"instagram\.com/([a-zA-Z0-9._]+)", href)
        if not match:
            continue

        username = _clean_instagram_username(match.group(1))
        if not username or username.lower() in seen:
            continue

        seen.add(username.lower())

        # Lấy văn bản xung quanh liên kết để bóc tách follower & bio snippet
        snippet = ""
        parent = a.find_parent(["div", "li", "article"])
        if parent:
            snippet = parent.get_text(" ", strip=True)

        display_name = username
        # Định dạng snippet Google thường là: "... (@username) on Instagram: 'Bio...' " hoặc "Tên (@username)"
        name_match = re.search(r"([^\n(•·]+?)\s*\(@" + re.escape(username) + r"\)", snippet, re.IGNORECASE)
        if name_match:
            cand_name = name_match.group(1).strip()
            if len(cand_name) <= 50 and not cand_name.lower().startswith("http"):
                display_name = cand_name

        # Bóc tách follower nếu có trong snippet (vd: '120K Followers', '45.5K người theo dõi')
        followers = 0
        follower_match = re.search(
            r"([\d.,]+[kmb]?)\s*(?:followers|người theo dõi|lượt theo dõi)",
            snippet,
            re.IGNORECASE,
        )
        if follower_match:
            followers = parse_follower_string(follower_match.group(0))

        candidates.append({
            "username": username,
            "display_name": display_name,
            "followers": followers,
            "bio_snippet": snippet[:500],
            "profile_url": f"https://www.instagram.com/{username}/",
            "platform": "INSTAGRAM",
            "source": "SEARCH_DORKING",
        })

    return candidates


def search_new_instagram_candidates(
    keywords: list[str], max_per_keyword: int = 10, max_total: int = 30
) -> list[dict]:
    """
    Tự động săn tìm KOC Instagram mới theo từ khóa / lĩnh vực (Beauty, Food, Tech, Fashion...):
    - Sử dụng kỹ thuật Search Dorking sạch (không cần đăng nhập, không lo checkpoint/khóa tài khoản).
    - Bóc tách username, display name, follower, bio snippet.
    - Sàng lọc nhanh qua Tier-0 Pre-filter (loại bỏ shop sỉ lẻ/rác ngay từ snippet).
    """
    discovered: list[dict] = []
    seen_usernames: set[str] = set()

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()
            stealth = Stealth()
            stealth.apply_stealth_sync(page)

            for kw in keywords:
                if len(discovered) >= max_total:
                    break

                clean_kw = kw.strip().lstrip("#").strip()
                if not clean_kw:
                    continue

                logger.info(f"[INSTAGRAM DISCOVERY] Đang quét KOC Instagram ngành: '{clean_kw}'...")

                # 1. Thử quét qua Google Dorking
                query = f'site:instagram.com "{clean_kw}" -inurl:p -inurl:reel -inurl:explore'
                encoded_q = urllib.parse.quote(query)
                search_url = f"https://www.google.com/search?q={encoded_q}&num=25&hl=vi"

                candidates: list[dict] = []
                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(1500)
                    candidates = _extract_candidates_from_search_html(page.content())
                except Exception as g_err:
                    logger.debug(f"[INSTAGRAM DISCOVERY] Google search '{clean_kw}' gặp lỗi: {g_err}")

                # 2. Nếu Google bị chặn captcha hoặc ít kết quả -> Fallback sang DuckDuckGo HTML
                if len(candidates) < 3:
                    try:
                        ddg_query = f'site:instagram.com "{clean_kw}"'
                        ddg_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(ddg_query)}"
                        page.goto(ddg_url, wait_until="domcontentloaded", timeout=12000)
                        page.wait_for_timeout(1000)
                        ddg_candidates = _extract_candidates_from_search_html(page.content())
                        candidates.extend(ddg_candidates)
                    except Exception as ddg_err:
                        logger.debug(f"[INSTAGRAM DISCOVERY] DuckDuckGo search '{clean_kw}' gặp lỗi: {ddg_err}")

                kw_count = 0
                for cand in candidates:
                    uname = cand["username"].lower()
                    if uname in seen_usernames:
                        continue
                    seen_usernames.add(uname)

                    # Tier-0 Filter: Loại nhanh các tài khoản shop sỉ/lẻ lộ liễu từ snippet
                    passed, reason = evaluate_tier0_search_card(cand)
                    if not passed:
                        logger.debug(f"[INSTAGRAM DISCOVERY] Bỏ qua @{cand['username']}: {reason}")
                        continue

                    discovered.append(cand)
                    kw_count += 1
                    if kw_count >= max_per_keyword or len(discovered) >= max_total:
                        break

                logger.info(
                    f"[INSTAGRAM DISCOVERY] Quét xong ngành '{clean_kw}': Thu thập {kw_count} ứng viên Instagram."
                )
                page.wait_for_timeout(1000)

            browser.close()
        except Exception as e:
            logger.error(f"[INSTAGRAM DISCOVERY] Lỗi trong tiến trình tìm kiếm Instagram: {e}")

    return discovered
