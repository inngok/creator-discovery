import re
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright
from playwright_stealth import Stealth

from app.core.logger import setup_logger
from app.core.crawl_progress import report_crawl_progress
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
        return worker.submit(copy_context().run, check, *args).result()


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
        img = link.find("img")
        img_alt = img.get("alt", "") if img else ""
        link_title = link.get("title", "")
        fallback_text = text or img_alt or link_title
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
            if 1 <= len(candidate_text) <= 2000:
                text = candidate_text
                break
        evidence_text = text or fallback_text or f"TikTok @{match.group(1)}"
        username = match.group(1)
        found.setdefault(username.casefold(), {
            "username": username,
            "followers": 0,
            "platform": "TIKTOK",
            "profile_url": f"https://www.tiktok.com/@{username}",
            "source_video_url": f"https://www.tiktok.com/@{username}/video/{match.group(2)}",
            "topic_evidence_text": evidence_text,
            "source": "TIKTOK_SEARCH_VIDEO",
        })
    return list(found.values())


def _trending_matches_topic(candidate: dict, keyword: str) -> bool:
    from app.services.discovery_verification import matches_requested_topic
    return matches_requested_topic(candidate, keyword)


def _verify_tiktok_candidate_followers(candidate: dict) -> bool:
    # Verify the whole creator profile, including posts, even when search has followers.
    from app.services.discovery_verification import verify_discovery_profile
    return verify_discovery_profile(candidate, allow_review_candidate=True)


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
        "đã xảy ra lỗi", "có vấn đề với máy chủ", "vui lòng thử lại",
    )):
        raise TikTokDiscoveryError("TikTok không trả kết quả công khai hoặc yêu cầu xác minh. Sẽ thử nguồn tìm kiếm dự phòng.")


def _open_tiktok_discovery_page(page, keyword: str | None):
    """Use TikTok's own search form, or its Explore feed for trending leads."""
    if keyword is None:
        response = page.goto("https://www.tiktok.com/explore", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2500)
        _ensure_search_available(page, response)
        return
    page.goto("https://www.tiktok.com/", wait_until="domcontentloaded", timeout=20000)
    search = page.locator('input[data-e2e="search-user-input"]:visible, input[type="search"]:visible').first
    # Desktop TikTok can collapse the search form behind the navigation button.
    if not search.is_visible():
        page.locator('[data-e2e="nav-search"]').click(timeout=10000)
    search.wait_for(state="visible", timeout=15000)
    search.fill(keyword.strip(), timeout=10000)
    search.press("Enter", timeout=10000)
    page.wait_for_url("**/search**", wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(2500)
    try:
        page.locator('a[href*="/video/"], [data-e2e="search-user-card"], '
                     '[data-e2e="search-user-container"], [data-e2e="search-user-item"]').first.wait_for(
            state="visible", timeout=10000)
    except PlaywrightTimeoutError:
        # An empty result is allowed; source errors below still trigger Explore.
        pass
    _ensure_search_available(page, None)


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
    verification_budget = min(max(max_total * 6, len(keywords) * 6), 60)
    per_keyword_budget = max(3, verification_budget // max(1, 2 * len(keywords)))
    checked_profiles = {}

    with sync_playwright() as p:
        try:
            browser, context = _init_discovery_context(p)
            stealth = Stealth()
            page = context.new_page()
            stealth.apply_stealth_sync(page)

            for kw in keywords or [""]:
                if len(discovered) >= max_total or verified_count >= verification_budget:
                    break

                logger.info(f"[DISCOVERY] Đang quét từ khóa TikTok: '{kw}'...")
                report_crawl_progress(
                    f"TikTok: đang quét từ khóa '{kw}'.",
                    platform="TIKTOK",
                    keyword=kw,
                    stage="search",
                )
                sources = ("search", "trending") if kw.strip() else ("trending",)
                for source in sources:
                    if len(discovered) >= max_total or verified_count >= verification_budget:
                        break
                    try:
                        report_crawl_progress(
                            f"TikTok: {'nhập từ khóa vào ô tìm kiếm' if source == 'search' else 'quét video trending trên Khám phá'}: '{kw}'.",
                            platform="TIKTOK", keyword=kw,
                            stage="search" if source == "search" else "trending",
                        )
                        _open_tiktok_discovery_page(page, kw if source == "search" else None)
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
                                cand["source"] = "TIKTOK_UI_SEARCH" if source == "search" else "TIKTOK_EXPLORE"
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
                                cand = {**checked_profiles[key], "search_keyword": kw,
                                        "source": "TIKTOK_UI_SEARCH" if source == "search" else "TIKTOK_EXPLORE"}
                                topic = f"{requested_category or ''}; {kw}"
                                if (requested_category or kw.strip()) and not _trending_matches_topic(cand, topic):
                                    report_crawl_progress(
                                        f"TikTok: @{username} chưa đủ bằng chứng khớp chủ đề.",
                                        platform="TIKTOK",
                                        keyword=kw,
                                        stage="topic_rejected",
                                    )
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
                        report_crawl_progress(
                            f"TikTok: quét xong '{kw}', có {kw_count} ứng viên đạt điều kiện.",
                            platform="TIKTOK",
                            keyword=kw,
                            stage="search_complete",
                        )
                        page.wait_for_timeout(1000)
                        if kw_count:
                            break

                    except Exception as kw_err:
                        search_errors.append(f"{kw}: {type(kw_err).__name__}: {str(kw_err)[:250]}")
                        logger.warning(f"[DISCOVERY] Lỗi khi quét từ khóa '{kw}': {kw_err}")
                        report_crawl_progress(
                            f"TikTok: không quét được '{kw}', đang chuyển sang nguồn dự phòng.",
                            platform="TIKTOK",
                            keyword=kw,
                            stage="search_error",
                        )
                        continue

            browser.close()
        except Exception as e:
            search_errors.append(f"Playwright: {type(e).__name__}")
            logger.error(f"[DISCOVERY] Lỗi khởi tạo Playwright Discovery: {e}")

    if not discovered and search_errors and not search_source_available:
        raise TikTokDiscoveryError("TikTok search and trending discovery failed: " + "; ".join(search_errors[:5]))
    return discovered
