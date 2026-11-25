import os
import re
import unicodedata
import urllib.parse
from collections import deque
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from app.core.logger import setup_logger
from app.core.crawl_progress import (
    build_discovery_rejection_result,
    report_crawl_progress,
    report_discovery_rejection,
)
from app.crawlers.search_extractor import evaluate_tier0_search_card
from app.crawlers.tiktok_discovery import _run_profile_check
from app.services.discovery_verification import (
    TopicVerificationUnavailable,
    matches_requested_topic,
    verify_discovery_profile,
)

logger = setup_logger("facebook_discovery")


class FacebookDiscoveryError(RuntimeError):
    """Facebook search could not be accessed; not an empty result set."""


def search_new_facebook_candidates(
    keywords: list[str], max_per_keyword: int = 10, max_total: int = 30,
    requested_category: str | None = None,
) -> list[dict]:
    """
    Tìm tác giả Facebook qua hashtag và Pages theo từ khóa.
    Trả về danh sách dict chứa: {username, display_name, followers, bio_snippet, profile_url, platform}
    """
    # Kiểm tra session sớm để tránh mở browser rồi mới fail
    logger.info("Facebook discovery uses anonymous public pages.")
    discovered: list[dict] = []
    seen_usernames = set()

    context = None
    access_errors = []
    accessible_pages = 0
    keyword_counts = {}
    browser_restarts = 0
    profile_checks = 0
    verification_budget = min(max_total * 3, 30)
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
                bing_query = f"site:facebook.com {keyword}"
                searches.append((keyword, f"https://www.bing.com/search?q={urllib.parse.quote(bing_query)}&count=25", None))

            # Try hashtags for every requested term before Pages or external indexes.
            searches.sort(key=lambda search: 0 if search[2] is True else (1 if search[2] is False else 2))
            searches = deque(searches)
            while searches:
                kw, search_url, is_hashtag = searches.popleft()
                if len(discovered) >= max_total or profile_checks >= verification_budget:
                    break
                if keyword_counts.get(kw, 0) >= max_per_keyword:
                    continue

                logger.info(f"[DISCOVERY FB] Đang quét từ khóa Facebook: '{kw}'...")
                source = "Bing" if "bing.com" in search_url else (
                    "hashtag" if is_hashtag else "Pages"
                )
                report_crawl_progress(
                    f"Facebook: đang tìm '{kw}' trên {source}.",
                    platform="FACEBOOK",
                    keyword=kw,
                    stage="search",
                )

                try:
                    response = page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
                    if response is not None and response.status >= 400:
                        raise FacebookDiscoveryError(
                            f"Facebook từ chối trang tìm kiếm (HTTP {response.status}). Không thể tìm kênh theo từ khóa; kiểm tra kết nối Facebook hoặc nhập URL kênh cụ thể.")
                    page.wait_for_timeout(3500)
                    if any(part in page.url for part in ("/login", "/checkpoint")):
                        raise FacebookDiscoveryError("Facebook không cho khách chưa đăng nhập xem nguồn này. Thử URL profile công khai hoặc thử lại sau.")
                    dismiss_facebook_dialogs(page)

                    extractor = (extract_facebook_hashtag_candidates if is_hashtag is True
                                 else extract_facebook_search_card_candidates)
                    candidates = extractor(page.content())
                    # Thu thập trước mỗi lần cuộn trang
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
                        cand["source"] = "FACEBOOK_HASHTAG" if is_hashtag is True else (
                            "FACEBOOK_PAGES" if is_hashtag is False else "BING")
                        cand["search_keyword"] = kw
                        cand["requested_category"] = requested_category
                        if profile_checks >= verification_budget:
                            break
                        profile_checks += 1
                        report_crawl_progress(
                            f"Facebook: đang xác minh hồ sơ @{username}.",
                            platform="FACEBOOK",
                            keyword=kw,
                            stage="profile_verification",
                        )
                        if not _run_profile_check(verify_discovery_profile, cand):
                            continue
                        if not matches_requested_topic(cand, f"{requested_category or ''}; {kw}"):
                            report_discovery_rejection(build_discovery_rejection_result(
                                cand,
                                reason_codes=[
                                    *((cand.get("creator_check") or {}).get("reason_codes") or []),
                                    "TOPIC_NOT_MATCHED",
                                ],
                                summary_reason=f"Nội dung chưa đủ bằng chứng khớp chủ đề '{kw or requested_category or ''}'.",
                            ))
                            continue
                        discovered.append(cand)
                        kw_count += 1
                        keyword_counts[kw] = kw_count

                        if kw_count >= max_per_keyword or len(discovered) >= max_total:
                            break

                    logger.info(
                        f"[DISCOVERY FB] Quét xong từ khóa '{kw}': Tìm thấy {kw_count} Fanpage/KOC tiềm năng."
                    )
                    report_crawl_progress(
                        f"Facebook: xong '{kw}' trên {source}, có {kw_count} ứng viên đạt điều kiện.",
                        platform="FACEBOOK",
                        keyword=kw,
                        stage="search_complete",
                    )
                    page.wait_for_timeout(2000)

                except TopicVerificationUnavailable:
                    # AI không khả dụng: giữ ứng viên hiện tại cần duyệt thủ công, tiếp tục source tiếp theo
                    # (cand vẫn ở local scope — thêm vào discovered nếu pass tier0)
                    try:
                        _cand = cand  # noqa: F821  – defined in loop above when exception fires
                        _cand["requires_manual_review"] = True
                        if evaluate_tier0_search_card(_cand)[0] and _cand not in discovered:
                            discovered.append(_cand)
                            kw_count += 1
                            keyword_counts[kw] = kw_count
                    except Exception:
                        pass
                    continue
                except FacebookDiscoveryError as access_error:
                    access_errors.append(access_error)
                    report_crawl_progress(
                        f"Facebook: {source} không cho truy cập công khai, chuyển nguồn khác.",
                        platform="FACEBOOK",
                        keyword=kw,
                        stage="source_error",
                    )
                    continue
                except Exception as kw_err:
                    logger.warning(f"[DISCOVERY FB] Lỗi khi quét từ khóa '{kw}': {kw_err}")
                    report_crawl_progress(
                        f"Facebook: lỗi khi tìm '{kw}' trên {source}, thử tiếp nguồn khác.",
                        platform="FACEBOOK",
                        keyword=kw,
                        stage="source_error",
                    )
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

