"""Live, read-only hashtag -> author -> verified profile/topic probe.

Run from the repository root: python -m scripts.probe_hashtag_discovery
No mocked network calls, backend sync, or changes to qualification rules.
"""
import json
import time
from datetime import datetime, UTC
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from app.crawlers.public_browser import create_public_context
from app.crawlers.instagram_discovery import _collect_instagram_hashtag_candidates
from app.crawlers.search_extractor import extract_facebook_hashtag_candidates
from app.crawlers.tiktok_discovery import _run_profile_check
from app.services.discovery_verification import verify_discovery_profile, matches_requested_topic


def run():
    report = {"started_at": datetime.now(UTC).isoformat(), "runs": []}
    terms = [("reviewcongnghe", "TECH; review công nghệ"),
             ("reviewmypham", "BEAUTY; review mỹ phẩm"),
             ("anuongsaigon", "FOOD; ăn uống Sài Gòn")]
    for platform in ("FACEBOOK", "INSTAGRAM"):
        for tag, topic in terms:
            entry = {"platform": platform, "hashtag": tag, "topic": topic,
                     "authors": [], "verified_profiles": 0, "topic_matches": 0}
            report["runs"].append(entry)
            start = time.monotonic()
            try:
                with sync_playwright() as playwright:
                    context = create_public_context(playwright, platform)
                    try:
                        page = context.new_page()
                        Stealth().apply_stealth_sync(page)
                        if platform == "INSTAGRAM":
                            url = f"https://www.instagram.com/explore/tags/{quote(tag)}/"
                            candidates = _collect_instagram_hashtag_candidates(page, context, url, 3)
                        else:
                            url = f"https://www.facebook.com/hashtag/{quote(tag)}"
                            response = page.goto(url, wait_until="domcontentloaded", timeout=20000)
                            entry["http_status"] = response.status if response else None
                            if response and response.status >= 400:
                                raise RuntimeError(f"Facebook hashtag HTTP {response.status}")
                            candidates = []
                            for viewport in range(3):
                                page.wait_for_timeout(2500)
                                candidates.extend(extract_facebook_hashtag_candidates(page.content()))
                                if candidates:
                                    break
                                page.mouse.wheel(0, 900)
                            if "/login" in page.url or "/checkpoint" in page.url:
                                raise RuntimeError("Facebook hashtag requires login or verification")
                        entry["final_url"] = page.url
                        unique = {c["username"].casefold(): c for c in candidates}
                        entry["authors_found"] = len(unique)
                    finally:
                        context.browser.close()
                # Close the discovery browser before starting profile browser work.
                for candidate in list(unique.values())[:2]:
                    candidate.update(search_keyword=tag, requested_category=topic,
                                     source=f"{platform}_HASHTAG")
                    row = {"username": candidate["username"], "profile_url": candidate["profile_url"]}
                    entry["authors"].append(row)
                    try:
                        verified = _run_profile_check(verify_discovery_profile, candidate)
                        row["verified_profile"] = verified
                        row["creator_check"] = candidate.get("creator_check")
                        row["market_check"] = candidate.get("market_check")
                        if verified:
                            entry["verified_profiles"] += 1
                            snapshot = candidate["_verified_snapshot"]
                            row["followers"] = snapshot["profile"].get("followers")
                            row["post_count"] = len(snapshot["posts"])
                            row["topic_match"] = matches_requested_topic(candidate, topic)
                            entry["topic_matches"] += int(row["topic_match"])
                    except Exception as error:
                        row["error"] = f"{type(error).__name__}: {str(error)[:300]}"
            except Exception as error:
                entry["error"] = f"{type(error).__name__}: {str(error)[:300]}"
            entry["elapsed_seconds"] = round(time.monotonic() - start, 1)
            print(json.dumps(entry, ensure_ascii=False), flush=True)
            path = Path("data/hashtag_live_probe.json")
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    run()
