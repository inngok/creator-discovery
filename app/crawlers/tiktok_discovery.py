import os
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from app.core.logger import setup_logger
from app.crawlers.search_extractor import (
    evaluate_tier0_search_card,
    extract_search_card_candidates,
)

logger = setup_logger("tiktok_discovery")


class TikTokDiscoveryError(RuntimeError):
    pass


def _run_profile_check(check, *args):
    # Discovery thread owns Playwright event loop; run sync runner in separate worker
    with ThreadPoolExecutor(max_workers=1) as worker:
        return worker.submit(check, *args).result()


def _extract_trending_authors(html: str) -> list[dict]:
    """Bóc tách tác giả video nổi bật từ các video card."""
    soup = BeautifulSoup(html, "html.parser")
    # Creator cards are shown above video results in the Top search tab.
    found = {c["username"].casefold(): c
             for c in extract_search_card_candidates(html)}
    for link in soup.select('a[href*="/video/"]'):
        match = re.search(r"/@([\w.]+)/video/(\d+)", link.get("href", ""))
        if not match:
            continue
        card = link
        text = link.get_text(" ", strip=True)
        for _ in range(5):
            card = card.parent
            if card is None or card.name in {"body", "html"}:
                break
            authors = {
                m.group(1).casefold()
                for a in card.select('a[href*="/video/"]')
                if (m := re.search(r"/@([\w.]+)/video/", a.get("href", "")))
            }
            if authors - {match.group(1).casefold()}:
                break
            candidate_text = card.get_text(" ", strip=True)
            if 20 <= len(candidate_text) <= 2000:
                text = candidate_text
                break
        if text:
            username = match.group(1)
            found.setdefault(username.casefold(), {
                "username": username,
                "followers": 0,
                "platform": "TIKTOK",
                "profile_url": f"https://www.tiktok.com/@{username}",
                "source_video_url": f"https://www.tiktok.com/@{username}/video/{match.group(2)}",
                "topic_evidence_text": text,
                "from_trending": True,
            })
    return list(found.values())


def _trending_matches_topic(candidate: dict, keyword: str) -> bool:
    from app.services.discovery_verification import matches_requested_topic
    return matches_requested_topic(candidate, keyword)


def _verify_tiktok_candidate_followers(candidate: dict) -> bool:
    # Verify the whole creator profile, including posts, even when search has followers.
    from app.services.discovery_verification import verify_discovery_profile
    return verify_discovery_profile(candidate)


def _init_discovery_context(p):
    from app.crawlers.tiktok_crawler import _init_playwright_context
    context, _ = _init_playwright_context(p)
    page = context.new_page()
    try:
        page.goto("https://www.tiktok.com/", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)
    except Exception as error:
        logger.warning("Public TikTok warm-up failed: %s", type(error).__name__)
    finally:
        page.close()
    return context.browser, context


def _ensure_search_available(page, response):
    status = getattr(response, "status", 200)
    if isinstance(status, int) and status >= 400:
        raise TikTokDiscoveryError(f"TikTok search unavailable (HTTP {status})")
    text = page.locator("body").inner_text(timeout=3000)
    if isinstance(text, str) and any(message in text.casefold() for message in (
        "something went wrong", "verify to continue", "complete the captcha",
        "sorry, something wrong with the server",
    )):
        raise TikTokDiscoveryError("TikTok không trả kết quả công khai hoặc yêu cầu xác minh. Sẽ thử nguồn tìm kiếm dự phòng.")


def _search_indexed_tiktok_candidates(keywords, max_per_keyword, max_total, requested_category):
    """Search indexes provide leads only; verify every lead against its live profile."""
    from app.services.crawled_result_service import get_crawled_tiktok_identifiers
    found = []
    seen = get_crawled_tiktok_identifiers()
    checked = 0
    budget = min(max(max_total * 3, len(keywords) * 3), 30)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            for keyword in keywords:
                count = 0
                query = urllib.parse.quote(f'site:tiktok.com/@ "{keyword}"')
                for url in (f"https://www.google.com/search?q={query}&num=20",
                            f"https://html.duckduckgo.com/html/?q={query}"):
                    try:
                        response = page.goto(url, wait_until="domcontentloaded", timeout=15000)
                        if response and response.status >= 400:
                            continue
                        soup = BeautifulSoup(page.content(), "html.parser")
                        for anchor in soup.select("a[href]"):
                            href = urllib.parse.unquote(anchor.get("href", ""))
                            match = re.search(r"https?://(?:www\.)?tiktok\.com/@([\w.]+)(?:[/?&#]|$)", href)
                            if not match:
                                continue
                            username = match.group(1)
                            key = username.casefold()
                            if key in seen:
                                continue
                            seen.add(key)
                            if checked >= budget:
                                return found
                            candidate = {"username": username, "platform": "TIKTOK",
                                         "profile_url": f"https://www.tiktok.com/@{username}",
                                         "search_keyword": keyword, "requested_category": requested_category,
                                         "source": "SEARCH_INDEX_LIVE_VERIFIED"}
                            checked += 1
                            if not _run_profile_check(_verify_tiktok_candidate_followers, candidate):
                                continue
                            if not _trending_matches_topic(candidate, f"{requested_category or ''}; {keyword}"):
                                continue
                            if not evaluate_tier0_search_card(candidate)[0]:
                                continue
                            found.append(candidate)
                            count += 1
                            if len(found) >= max_total:
                                return found
                            if count >= max_per_keyword:
                                break
                    except Exception as error:
                        logger.warning("TikTok indexed search failed: %s", type(error).__name__)
                    if count >= max_per_keyword:
                        break
        finally:
            browser.close()
    return found


def search_new_tiktok_candidates(
    keywords: list[str], max_per_keyword: int = 10, max_total: int = 30,
    requested_category: str | None = None,
) -> list[dict]:
    """
    Quét trang tìm kiếm TikTok theo từ khóa để tìm kiếm các ứng viên KOC MỚI.
    Trả về danh sách dict chứa: {username, display_name, followers, bio_snippet, profile_url, platform}
    """
    discovered: list[dict] = []
    from app.services.crawled_result_service import get_crawled_tiktok_identifiers
    seen_usernames = get_crawled_tiktok_identifiers()
    search_errors = []
    search_source_available = False
    verified_count = 0
    # Output quota must not exhaust the search after the first keyword.
    verification_budget = min(max(max_total * 3, len(keywords) * 3), 30)
    per_keyword_budget = max(3, verification_budget // max(1, len(keywords)))
    checked_profiles = {}

    with sync_playwright() as p:
        try:
            browser, context = _init_discovery_context(p)
            stealth = Stealth()
            page = context.new_page()
            stealth.apply_stealth_sync(page)

            for kw in keywords:
                if len(discovered) >= max_total or verified_count >= verification_budget:
                    break

                logger.info(f"[DISCOVERY] Đang quét từ khóa TikTok: '{kw}'...")
                encoded_kw = urllib.parse.quote(kw)
                search_url = f"https://www.tiktok.com/search?q={encoded_kw}"

                try:
                    response = page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(2500)
                    _ensure_search_available(page, response)

                    kw_count = 0
                    kw_verified_count = 0
                    keyword_seen = set()
                    # Read each viewport before scrolling: TikTok can virtualize top cards.
                    for viewport in range(3):
                        candidates = _extract_trending_authors(page.content())
                        if candidates:
                            search_source_available = True
                        for cand in candidates:
                            username = cand.get("username", "").strip().lstrip("@")
                            key = username.casefold()
                            if not username or key in seen_usernames or key in keyword_seen:
                                continue
                            keyword_seen.add(key)
                            cand["search_keyword"] = kw
                            cand["requested_category"] = requested_category
                            if not evaluate_tier0_search_card(cand)[0]:
                                continue
                            # One matching video cannot establish a creator account.
                            if key not in checked_profiles:
                                if verified_count >= verification_budget or kw_verified_count >= per_keyword_budget:
                                    break
                                verified_count += 1
                                kw_verified_count += 1
                                valid = _run_profile_check(_verify_tiktok_candidate_followers, cand)
                                checked_profiles[key] = dict(cand) if valid else None
                            if checked_profiles[key] is None:
                                continue
                            cand = {**checked_profiles[key], "search_keyword": kw}
                            topic = f"{requested_category or ''}; {kw}"
                            if not _trending_matches_topic(cand, topic):
                                continue
                            if not evaluate_tier0_search_card(cand)[0]:
                                continue
                            cand["platform"] = "TIKTOK"
                            discovered.append(cand)
                            seen_usernames.add(key)
                            kw_count += 1
                            if kw_count >= max_per_keyword or len(discovered) >= max_total:
                                break
                        if (kw_count >= max_per_keyword or len(discovered) >= max_total
                                or verified_count >= verification_budget
                                or kw_verified_count >= per_keyword_budget):
                            break
                        if viewport < 2:
                            page.mouse.wheel(0, 650)
                            page.wait_for_timeout(800)

                    logger.info(
                        f"[DISCOVERY] Quét xong từ khóa '{kw}': Tìm thấy {kw_count} ứng viên tiềm năng."
                    )
                    page.wait_for_timeout(1000)

                except Exception as kw_err:
                    search_errors.append(f"{kw}: {type(kw_err).__name__}: {str(kw_err)[:250]}")
                    logger.warning(f"[DISCOVERY] Lỗi khi quét từ khóa '{kw}': {kw_err}")
                    continue

            browser.close()
        except Exception as e:
            search_errors.append(f"Playwright: {type(e).__name__}")
            logger.error(f"[DISCOVERY] Lỗi khởi tạo Playwright Discovery: {e}")

    # Empty/filtered search cards also need a second source, not only HTTP errors.
    if not discovered:
        try:
            recovered = _search_indexed_tiktok_candidates(keywords, max_per_keyword, max_total, requested_category)
            if recovered:
                return recovered
        except Exception as error:
            logger.warning("TikTok indexed fallback unavailable: %s", type(error).__name__)
        if search_errors and not search_source_available:
            raise TikTokDiscoveryError("TikTok search and verified topic discovery failed: " + "; ".join(search_errors[:5]))
    return discovered
