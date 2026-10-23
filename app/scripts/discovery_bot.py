import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
import undetected_chromedriver as uc

from app.core.logger import setup_logger
from app.crawlers.search_extractor import (
    evaluate_tier0_search_card,
    extract_search_card_candidates,
)
from app.workers.tasks import preflight_candidate_task

logger = setup_logger(__name__)


def discover_creators_by_keyword(keyword: str):
    safe_kw = keyword.encode("ascii", "ignore").decode()
    logger.info(f"[DISCOVERY] Starting TikTok search for keyword: '{safe_kw}'...")
    options = uc.ChromeOptions()
    options.headless = True  # Tàng hình

    try:
        driver = uc.Chrome(options=options)
    except:
        # Fallback for Chrome 154 if auto-detect fails
        options2 = uc.ChromeOptions()
        options2.headless = True
        driver = uc.Chrome(options=options2, version_main=154)

    try:
        import urllib.parse

        encoded_keyword = urllib.parse.quote(keyword)
        # Vào trang Tìm kiếm Người dùng của TikTok
        driver.get(f"https://www.tiktok.com/search/user?q={encoded_keyword}")

        # Chờ trang load (Tìm kiếm thường load lâu hơn)
        time.sleep(7)

        usernames = set()
        enqueued_count = 0
        dropped_count = 0

        # Scroll a few times to discover creators
        for i in range(5):
            logger.info(f"[DISCOVERY] Scrolling {i+1}/5 to load more content...")
            driver.execute_script("window.scrollBy(0, 1500);")
            time.sleep(3)

            html = driver.page_source
            candidates = extract_search_card_candidates(html)

            for cand in candidates:
                username = cand["username"]
                if not username or username in usernames:
                    continue
                usernames.add(username)

                # Nấc 0: Zero-cost Pre-filter ngay tại thẻ tìm kiếm
                pass_tier0, drop_reason = evaluate_tier0_search_card(cand)
                if not pass_tier0:
                    dropped_count += 1
                    logger.info(
                        f"[DISCOVERY TIER-0 DROP] Bỏ qua @{username} "
                        f"({cand.get('followers', 0)} fl, '{cand.get('bio_snippet', '')}'): {drop_reason}"
                    )
                    continue

                # 🚀 Gửi vào hàng đợi Preflight nhẹ, KHÔNG BAO GIỜ gọi Deep Crawl trực tiếp
                preflight_candidate_task.delay("TIKTOK", username)
                enqueued_count += 1
                logger.info(
                    f"[DISCOVERY ENQUEUE PREFLIGHT] Đã đưa @{username} vào Preflight Queue "
                    f"({cand.get('followers', 0)} fl)"
                )

        logger.info(
            f"[DISCOVERY] Completed! Found {len(usernames)} candidates: "
            f"{enqueued_count} sent to Preflight Queue, {dropped_count} dropped by Tier-0."
        )
    except Exception as e:
        logger.info(f"[DISCOVERY] Error: {e}")
    finally:
        driver.quit()


if __name__ == "__main__":
    # Hệ thống sẽ tự động lướt qua đa dạng các ngành nghề
    diverse_keywords = [
        "review đồ ăn",
        "thời trang",
        "mỹ phẩm",
        "công nghệ",
        "du lịch",
        "thể thao",
        "mẹ và bé",
        "giải trí",
    ]

    while True:
        for kw in diverse_keywords:
            discover_creators_by_keyword(kw)
            # Thêm thời gian nghỉ giữa các từ khóa (giãn cách ra 10 phút)
            logger.info(
                f"[DISCOVERY] Đã quét xong từ khóa '{kw}'. Tạm nghỉ 10 phút để tránh bị chặn IP..."
            )
            time.sleep(600)

        logger.info(
            "[DISCOVERY] Hoàn thành 1 vòng lớn. Sẽ ngủ 6 tiếng (21600s) trước khi chạy vòng tiếp theo..."
        )
        time.sleep(21600)
