import os
import urllib.parse
import unicodedata
from collections import deque
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


class FacebookDiscoveryError(RuntimeError):
    """Facebook search could not be accessed; not an empty result set."""


def search_new_facebook_candidates(
    keywords: list[str], max_per_keyword: int = 10, max_total: int = 30
) -> list[dict]:
    """
    Tìm tác giả qua hashtag trước, bổ sung bằng tìm kiếm Pages theo từ khóa.
    Trả về danh sách dict chứa: {username, display_name, followers, bio_snippet, profile_url, platform}
    """
    discovered: list[dict] = []
    seen_usernames = set()

    context = None
    access_errors = []
    accessible_pages = 0
    keyword_counts = {}
    browser_restarts = 0
    with sync_playwright() as p:
        try:
            from app.crawlers.facebook_crawler import (
                _init_facebook_playwright_context,
                dismiss_facebook_dialogs,
                clean_facebook_username,
            )
            from app.services.crawled_result_service import get_crawled_facebook_identifiers
            known_identifiers = get_crawled_facebook_identifiers()
            seen_usernames.update(known_identifiers)
            from app.crawlers.search_extractor import (
                extract_facebook_search_card_candidates,
                extract_facebook_hashtag_candidates,
            )

            context, _ = _init_facebook_playwright_context(p)
            stealth = Stealth()
            page = context.new_page()
            stealth.apply_stealth_sync(page)

            searches = []
            for keyword in keywords:
                keyword = keyword.strip().lstrip("#").strip()
                if not keyword:
                    continue
                hashtag = "".join(keyword.split())
                plain_hashtag = "".join(char for char in unicodedata.normalize(
                    "NFD", hashtag.replace("đ", "d").replace("Đ", "D"))
                    if not unicodedata.combining(char))
                for tag in dict.fromkeys([plain_hashtag, hashtag]):
                    searches.append((keyword, f"https://www.facebook.com/hashtag/{urllib.parse.quote(tag)}", True))
                searches.append((keyword, f"https://www.facebook.com/search/pages/?q={urllib.parse.quote(keyword)}", False))

            searches = deque(searches)
            while searches:
                kw, search_url, is_hashtag = searches.popleft()
                if len(discovered) >= max_total:
                    break
                if keyword_counts.get(kw, 0) >= max_per_keyword:
                    continue

                logger.info(f"[DISCOVERY FB] Đang quét từ khóa Facebook: '{kw}'...")

                try:
                    response = page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
                    if response is not None and response.status >= 400:
                        raise FacebookDiscoveryError(
                            f"Facebook từ chối trang tìm kiếm (HTTP {response.status}). Không thể tìm kênh theo từ khóa; kiểm tra kết nối Facebook hoặc nhập URL kênh cụ thể.")
                    page.wait_for_timeout(3500)
                    if any(part in page.url for part in ("/login", "/checkpoint")):
                        raise FacebookDiscoveryError("Facebook yêu cầu đăng nhập để tìm kiếm. Cần kết nối lại tài khoản Facebook cho dịch vụ thu thập.")
                    dismiss_facebook_dialogs(page)

                    extractor = (extract_facebook_hashtag_candidates if is_hashtag
                                 else extract_facebook_search_card_candidates)
                    candidates = extractor(page.content())
                    # Collect before each scroll: Facebook can remove older posts from the DOM.
                    for _ in range(6):
                        if len({candidate["username"].casefold() for candidate in candidates}
                               - seen_usernames) >= min(max_per_keyword - keyword_counts.get(kw, 0),
                                                       max_total - len(discovered)):
                            break
                        page.mouse.wheel(0, 1500)
                        page.wait_for_timeout(1500)
                        candidates.extend(extractor(page.content()))

                    if not candidates and page.locator('input[type="password"]:visible').count():
                        raise FacebookDiscoveryError("Facebook yêu cầu đăng nhập và không trả về tác giả công khai.")
                    accessible_pages += 1
                    kw_count = keyword_counts.get(kw, 0)

                    for cand in candidates:
                        username = cand.get("username", "").strip()
                        if not username or username.lower() in seen_usernames:
                            continue
                        profile_identifier = clean_facebook_username(
                            cand.get("profile_url") or username).lstrip("@").casefold()
                        if profile_identifier in known_identifiers:
                            continue

                        seen_usernames.add(username.lower())

                        pass_tier0, drop_reason = evaluate_tier0_search_card(cand)
                        if not pass_tier0:
                            logger.debug(f"[DISCOVERY FB TIER-0 DROP] Bỏ qua {username}: {drop_reason}")
                            continue

                        cand["platform"] = "FACEBOOK"
                        discovered.append(cand)
                        kw_count += 1
                        keyword_counts[kw] = kw_count

                        if kw_count >= max_per_keyword or len(discovered) >= max_total:
                            break

                    logger.info(
                        f"[DISCOVERY FB] Quét xong từ khóa '{kw}': Tìm thấy {kw_count} Fanpage/KOC tiềm năng."
                    )
                    page.wait_for_timeout(2000)

                except FacebookDiscoveryError as access_error:
                    access_errors.append(access_error)
                    continue
                except Exception as kw_err:
                    logger.warning(f"[DISCOVERY FB] Lỗi khi quét từ khóa '{kw}': {kw_err}")
                    target_closed = ("Target page, context or browser has been closed" in str(kw_err)
                                     or page.is_closed() is True
                                     or (context.browser and context.browser.is_connected() is False))
                    if target_closed:
                        if browser_restarts >= 2:
                            if discovered:
                                break
                            raise FacebookDiscoveryError(
                                "Trình duyệt Facebook bị đóng nhiều lần giữa lượt săn. "
                                "Không thể tiếp tục; vui lòng thử lại và kiểm tra tài nguyên máy chủ."
                            ) from kw_err
                        browser_restarts += 1
                        logger.warning("[DISCOVERY FB] Khởi tạo lại trình duyệt (%s/2), thử lại nguồn đang quét.",
                                       browser_restarts)
                        try:
                            if context.browser:
                                context.browser.close()
                            else:
                                context.close()
                        except Exception:
                            pass
                        context, _ = _init_facebook_playwright_context(p)
                        page = context.new_page()
                        stealth.apply_stealth_sync(page)
                        searches.appendleft((kw, search_url, is_hashtag))
                        continue
                    access_errors.append(FacebookDiscoveryError(f"Không truy cập được nguồn Facebook: {kw_err}"))
                    continue

            if not discovered and not accessible_pages and access_errors:
                raise access_errors[0]

        except FacebookDiscoveryError:
            raise
        except Exception as e:
            logger.error(f"[DISCOVERY FB] Lỗi khởi tạo Playwright Discovery FB: {e}")
            raise FacebookDiscoveryError("Không khởi tạo được trình duyệt tìm kiếm Facebook.") from e

        finally:
            if context is not None:
                try:
                    if context.browser:
                        context.browser.close()
                    else:
                        context.close()
                except Exception:
                    pass

    return discovered
