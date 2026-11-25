import os
import re
import unicodedata
import urllib.parse
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from app.core.logger import setup_logger
from app.core.crawl_progress import report_crawl_progress
from app.services.discovery_verification import TopicVerificationUnavailable
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


def _ensure_index_available(page):
    text = page.locator("body").inner_text(timeout=3000)
    if isinstance(text, str) and any(term in text.casefold() for term in (
            "unusual traffic", "automated queries", "lưu lượng truy cập bất thường",
            "hệ thống của chúng tôi đã phát hiện", "complete the captcha")):
        raise InstagramDiscoveryError("Search index temporarily blocks automated access")


def _verify_instagram_candidate(candidate: dict, topic: str) -> bool:
    from app.crawlers.tiktok_discovery import _run_profile_check
    from app.services.discovery_verification import verify_discovery_profile, matches_requested_topic
    return (_run_profile_check(verify_discovery_profile, candidate)
            and matches_requested_topic(candidate, topic))


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


def _instagram_hashtags(keyword: str) -> list[str]:
    tag = re.sub(r"\s+", "", keyword.strip().lstrip("#"))
    plain = "".join(c for c in unicodedata.normalize("NFD", tag.replace("đ", "d").replace("Đ", "D"))
                    if not unicodedata.combining(c))
    return list(dict.fromkeys(t for t in (tag, plain) if t))


def _extract_instagram_hashtag_authors(html: str, post_url: str = "") -> list[dict]:
    """Only post owners and article headers establish authors, never commenters."""
    from app.crawlers.meta_public_data import embedded_payloads, dictionaries
    soup = BeautifulSoup(html, "html.parser")
    authors = {}

    def add(username, name="", url=""):
        username = _clean_instagram_username(username) if isinstance(username, str) else None
        if username:
            authors.setdefault(username.casefold(), {
                "username": username, "display_name": name or username,
                "followers": 0, "bio_snippet": "", "platform": "INSTAGRAM",
                "profile_url": f"https://www.instagram.com/{username}/",
                "source": "INSTAGRAM_HASHTAG", "source_post_url": url or post_url,
            })

    for payload in embedded_payloads(soup):
        for node in dictionaries(payload):
            code = node.get("shortcode") or node.get("code")
            if not isinstance(code, str) or not re.fullmatch(r"[\w-]+", code):
                continue
            if post_url and code != urllib.parse.urlsplit(post_url).path.strip("/").split("/")[-1]:
                continue
            owner = node.get("owner") or node.get("user")
            if isinstance(owner, dict):
                add(owner.get("username"), owner.get("full_name", ""),
                    f"https://www.instagram.com/p/{code}/")
    for article in soup.select("article"):
        post = article.select_one('a[href*="/p/"], a[href*="/reel/"]')
        url = urllib.parse.urljoin("https://www.instagram.com/", post["href"]) if post else post_url
        for anchor in article.select("header a[href]"):
            parsed = urllib.parse.urlsplit(urllib.parse.urljoin("https://www.instagram.com/", anchor["href"]))
            slug = parsed.path.strip("/")
            if parsed.hostname in {"instagram.com", "www.instagram.com"} and "/" not in slug:
                add(slug, anchor.get_text(" ", strip=True), url)
    return list(authors.values())


def _collect_instagram_hashtag_candidates(page, context, url: str, max_posts: int) -> list[dict]:
    response = page.goto(url, wait_until="domcontentloaded", timeout=20000)
    status = getattr(response, "status", 200)
    if isinstance(status, int) and status >= 400:
        raise InstagramDiscoveryError(f"Instagram hashtag HTTP {status}")
    page.wait_for_timeout(2500)
    if isinstance(page.url, str) and any(part in page.url for part in ("/accounts/login", "/challenge", "/checkpoint")):
        raise InstagramDiscoveryError("Instagram hashtag requires login or verification")
    authors, posts = {}, {}
    for viewport in range(3):
        html = page.content()
        for candidate in _extract_instagram_hashtag_authors(html):
            authors.setdefault(candidate["username"].casefold(), candidate)
        for anchor in BeautifulSoup(html, "html.parser").select('a[href]'):
            parsed = urllib.parse.urlsplit(urllib.parse.urljoin("https://www.instagram.com/", anchor["href"]))
            if parsed.hostname in {"instagram.com", "www.instagram.com"} and re.fullmatch(r"/(?:p|reel)/[\w-]+/?", parsed.path):
                posts.setdefault(parsed.path.rstrip("/"), "https://www.instagram.com" + parsed.path)
        if len(authors) >= max_posts or len(posts) >= max_posts:
            break
        if viewport < 2:
            page.mouse.wheel(0, 900)
            page.wait_for_timeout(1000)
    passwords = page.locator('input[type="password"]:visible').count()
    if not authors and not posts and isinstance(passwords, int) and passwords > 0:
        raise InstagramDiscoveryError("Instagram hashtag requires login")
    covered_posts = {c["source_post_url"].rstrip("/") for c in authors.values() if c["source_post_url"]}
    detail = None
    try:
        for post_url in list(posts.values())[:max_posts]:
            if post_url.rstrip("/") in covered_posts:
                continue
            if detail is None:
                detail = context.new_page()
                Stealth().apply_stealth_sync(detail)
            try:
                response = detail.goto(post_url, wait_until="domcontentloaded", timeout=15000)
                if response is not None and isinstance(response.status, int) and response.status >= 400:
                    continue
                detail.wait_for_timeout(1500)
                if isinstance(detail.url, str) and any(part in detail.url for part in ("/accounts/login", "/challenge")):
                    continue
                for candidate in _extract_instagram_hashtag_authors(detail.content(), post_url):
                    authors.setdefault(candidate["username"].casefold(), candidate)
            except Exception as error:
                logger.debug("Instagram hashtag post unavailable: %s", type(error).__name__)
    finally:
        if detail is not None:
            detail.close()
    return list(authors.values())[:max_posts]


def search_new_instagram_candidates(
    keywords: list[str], max_per_keyword: int = 10, max_total: int = 30,
    requested_category: str | None = None,
) -> list[dict]:
    """Discover hashtag post owners first; verify profiles before filling quotas."""
    if max_total <= 0 or max_per_keyword <= 0:
        return []
    terms = list(dict.fromkeys(k.strip().lstrip("#").strip() for k in keywords if k.strip().lstrip("#").strip()))
    if not terms:
        return []
    discovered, errors = [], []
    seen_usernames = set()
    checks, budget = 0, min(max(max_total * 3, len(terms) * 3), 60)
    accessible = False
    counts = {}
    browser = None
    with sync_playwright() as p:
        try:
            from app.crawlers.instagram_session import create_instagram_context
            context = create_instagram_context(p)
            browser = context.browser
            page = context.new_page()
            Stealth().apply_stealth_sync(page)
            sources = [(kw, "hashtag", "https://www.instagram.com/explore/tags/" + urllib.parse.quote(tag, safe="") + "/")
                       for kw in terms for tag in _instagram_hashtags(kw)]
            # All native hashtag pages run before any external search provider.
            for provider in ("Google", "DuckDuckGo", "Bing"):
                for kw in terms:
                    query = urllib.parse.quote(f'site:instagram.com "{kw}" Vietnam -inurl:p -inurl:reel -inurl:explore')
                    url = {"Google": f"https://www.google.com/search?q={query}&num=25&hl=vi&gl=vn",
                           "DuckDuckGo": f"https://html.duckduckgo.com/html/?q={query}",
                           "Bing": f"https://www.bing.com/search?q={query}&count=25"}[provider]
                    sources.append((kw, provider, url))
            for kw, source, url in sources:
                if len(discovered) >= max_total or checks >= budget:
                    break
                if counts.get(kw, 0) >= max_per_keyword:
                    continue
                report_crawl_progress(
                    f"Instagram: đang tìm '{kw}' trên {source}.",
                    platform="INSTAGRAM", keyword=kw,
                    stage="hashtag" if source == "hashtag" else "search_provider",
                )
                try:
                    if source == "hashtag":
                        candidates = _collect_instagram_hashtag_candidates(
                            page, context, url, max_posts=min(budget - checks, 12))
                    else:
                        response = page.goto(url, wait_until="domcontentloaded", timeout=15000)
                        status = getattr(response, "status", 200)
                        if isinstance(status, int) and status >= 400:
                            raise InstagramDiscoveryError(f"{source} HTTP {status}")
                        page.wait_for_timeout(1500)
                        _ensure_index_available(page)
                        candidates = _extract_candidates_from_search_html(page.content())
                    accessible = True
                    for cand in candidates:
                        key = cand["username"].casefold()
                        if key in seen_usernames:
                            continue
                        seen_usernames.add(key)
                        if not evaluate_tier0_search_card(cand)[0]:
                            continue
                        if checks >= budget:
                            break
                        checks += 1
                        cand["search_keyword"] = kw
                        cand["requested_category"] = requested_category
                        cand["source"] = "INSTAGRAM_HASHTAG" if source == "hashtag" else source.upper()
                        report_crawl_progress(
                            f"Instagram: đang xác minh @{cand['username']}.", platform="INSTAGRAM",
                            keyword=kw, stage="profile_verification",
                        )
                        if not _verify_instagram_candidate(cand, f"{requested_category or ''}; {kw}"):
                            continue
                        discovered.append(cand)
                        counts[kw] = counts.get(kw, 0) + 1
                        if counts[kw] >= max_per_keyword or len(discovered) >= max_total:
                            break
                    report_crawl_progress(
                        f"Instagram: xong '{kw}' trên {source}, có {counts.get(kw, 0)} ứng viên đã xác minh.",
                        platform="INSTAGRAM", keyword=kw, stage="search_complete",
                    )
                except TopicVerificationUnavailable:
                    # AI không khả dụng: giữ ứng viên hiện tại cần duyệt thủ công, tiếp tục source tiếp theo
                    try:
                        _cand = cand  # noqa: F821  – defined in loop above when exception fires
                        _cand["requires_manual_review"] = True
                        if evaluate_tier0_search_card(_cand)[0] and _cand not in discovered:
                            discovered.append(_cand)
                            counts[kw] = counts.get(kw, 0) + 1
                    except Exception:
                        pass
                    continue
                except Exception as error:
                    errors.append(f"{source}: {type(error).__name__}: {str(error)[:200]}")
                    logger.warning("Instagram %s unavailable: %s", source, type(error).__name__)
                    report_crawl_progress(
                        f"Instagram: {source} không khả dụng, đang thử nguồn tiếp theo.",
                        platform="INSTAGRAM", keyword=kw, stage="source_error",
                    )
        except InstagramDiscoveryError:
            raise
        except Exception as error:
            errors.append(f"Browser: {type(error).__name__}")
            logger.exception("Instagram discovery failed")
        finally:
            if browser is not None:
                browser.close()
    if errors and not accessible and not discovered:
        raise InstagramDiscoveryError("Instagram discovery unavailable: " + "; ".join(errors[:5]))
    return discovered
