import os
import urllib.parse
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from app.core.logger import setup_logger
from app.crawlers.search_extractor import (
    evaluate_tier0_search_card,
    extract_search_card_candidates,
)

logger = setup_logger("discovery_crawler")


def _init_discovery_context(p):
    browser = p.chromium.launch(
        headless=True,
        args=[
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
    )
    state_path = os.path.join("data", "playwright_state.json")
    context_args = {
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 720},
    }
    if os.path.exists(state_path):
        context_args["storage_state"] = state_path

    context = browser.new_context(**context_args)
    return browser, context


def search_new_tiktok_candidates(
    keywords: list[str], max_per_keyword: int = 10, max_total: int = 30
) -> list[dict]:
    """
    Quét trang tìm kiếm TikTok theo từ khóa để tìm kiếm các ứng viên KOC MỚI.
    Trả về danh sách dict chứa: {username, display_name, followers, bio_snippet, profile_url, platform}
    """
    discovered: list[dict] = []
    seen_usernames = set()

    with sync_playwright() as p:
        try:
            browser, context = _init_discovery_context(p)
            stealth = Stealth()
            page = context.new_page()
            stealth.apply_stealth_sync(page)

            for kw in keywords:
                if len(discovered) >= max_total:
                    break

                logger.info(f"[DISCOVERY] Đang quét từ khóa TikTok: '{kw}'...")
                encoded_kw = urllib.parse.quote(kw)
                search_url = f"https://www.tiktok.com/search/user?q={encoded_kw}"

                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
                    page.wait_for_timeout(3500)

                    # Cuộn trang 2-3 lần để tải thêm thẻ người dùng
                    for _ in range(3):
                        page.mouse.wheel(0, 1500)
                        page.wait_for_timeout(1500)

                    html = page.content()
                    candidates = extract_search_card_candidates(html)
                    kw_count = 0

                    for cand in candidates:
                        username = cand.get("username", "").strip()
                        if not username or username.lower() in seen_usernames:
                            continue

                        seen_usernames.add(username.lower())

                        # Bộ lọc Tier-0: loại nhanh kênh < 7000 fl hoặc nick bán hàng
                        pass_tier0, drop_reason = evaluate_tier0_search_card(cand)
                        if not pass_tier0:
                            logger.debug(f"[DISCOVERY TIER-0 DROP] Bỏ qua @{username}: {drop_reason}")
                            continue

                        cand["platform"] = "TIKTOK"
                        discovered.append(cand)
                        kw_count += 1

                        if kw_count >= max_per_keyword or len(discovered) >= max_total:
                            break

                    logger.info(
                        f"[DISCOVERY] Quét xong từ khóa '{kw}': Tìm thấy {kw_count} ứng viên tiềm năng."
                    )
                    page.wait_for_timeout(2000)

                except Exception as kw_err:
                    logger.warning(f"[DISCOVERY] Lỗi khi quét từ khóa '{kw}': {kw_err}")
                    continue

            browser.close()
        except Exception as e:
            logger.error(f"[DISCOVERY] Lỗi khởi tạo Playwright Discovery: {e}")

    return discovered
