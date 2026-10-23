import argparse
import os
import random
import re
import sys
import time
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.clients.java_client import java_client
from app.core.logger import setup_logger
from app.core.models import CreatorInfo, IngestPayload, Platform, Post
from app.crawlers.tiktok_crawler import _init_playwright_context, _setup_network_interception
from app.mappers.java_mapper import JavaMapper

logger = setup_logger("recent_posts_worker")


def parse_stat(val: str) -> int:
    """Chuyển đổi chuỗi số (K, M, B) sang số nguyên."""
    if not val:
        return 0
    s = str(val).strip().replace(" ", "").replace(",", ".")
    m = re.match(r"^([\d.]+)([kKmMbB]?)$", s)
    if m:
        num, suffix = m.groups()
        try:
            v = float(num)
            suf = suffix.upper()
            if suf == "K":
                return int(v * 1_000)
            elif suf == "M":
                return int(v * 1_000_000)
            elif suf == "B":
                return int(v * 1_000_000_000)
            return int(v)
        except Exception:
            return 0
    clean = re.sub(r"[^\d]", "", s)
    return int(clean) if clean else 0


def fetch_pending_creators(limit: int = 50) -> list[dict]:
    """Lấy danh sách các Creator trên Deploy DB chưa có bài post trong bảng post."""
    query = f"""
        SELECT c.id, c.external_id, c.user_name, c.display_name, c.avatar_url, c.profile_url, 
               COALESCE(m.followers, 0), COALESCE(m.category, 'Lifestyle'), COALESCE(m.niche, 'General')
        FROM creator c
        LEFT JOIN public_creator_metric m ON c.id = m.creator_id
        WHERE c.id NOT IN (SELECT DISTINCT creator_id FROM post)
        ORDER BY m.followers DESC
        {"LIMIT " + str(limit) if limit > 0 else ""};
    """
    # 1. Thử kết nối trực tiếp PostgreSQL bằng psycopg2
    try:
        import psycopg2
        conn = psycopg2.connect(
            host="221.121.3.226",
            user="influencermatch_user",
            password="12345",
            dbname="influencermatch_dev",
            connect_timeout=10,
        )
        cur = conn.cursor()
        cur.execute(query)
        rows = cur.fetchall()
        creators = []
        for r in rows:
            creators.append({
                "id": str(r[0]),
                "externalId": str(r[1]) if r[1] else "",
                "userName": str(r[2]) if r[2] else "",
                "displayName": str(r[3]) if r[3] else "",
                "avatarUrl": str(r[4]) if r[4] else "",
                "profileUrl": str(r[5]) if r[5] else "",
                "followers": int(r[6]) if r[6] else 0,
                "category": str(r[7]) if r[7] else "Lifestyle",
                "niche": str(r[8]) if r[8] else "General",
            })
        conn.close()
        return creators
    except Exception as e:
        logger.warning(f"[!] Lỗi kết nối psycopg2 trực tiếp: {e}. Thử fallback sang Docker...")

    # 2. Fallback sang docker exec psql nếu có
    try:
        import subprocess
        cmd = [
            "docker", "exec", "-e", "PGPASSWORD=12345", "influencermatch_postgres",
            "psql", "-h", "221.121.3.226", "-U", "influencermatch_user", "-d", "influencermatch_dev",
            "-t", "-A", "-F", "|",
            "-c", query,
        ]
        output = subprocess.check_output(cmd).decode("utf-8")
        creators = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|")
            if len(parts) >= 9:
                creators.append({
                    "id": parts[0],
                    "externalId": parts[1],
                    "userName": parts[2],
                    "displayName": parts[3],
                    "avatarUrl": parts[4],
                    "profileUrl": parts[5],
                    "followers": int(parts[6]) if parts[6].isdigit() else 0,
                    "category": parts[7],
                    "niche": parts[8],
                })
        return creators
    except Exception as e:
        logger.error(f"[-] Lỗi khi query danh sách creator từ Deploy DB: {e}")
        return []


def crawl_recent_posts_for_user(context, username: str, max_posts: int = 3) -> list[Post]:
    """Cào danh sách video gần đây từ trang profile TikTok bằng Network Interception + Fallback."""
    clean_uname = username.strip().removeprefix("@")
    url = f"https://www.tiktok.com/@{clean_uname}"
    page = context.new_page()

    try:
        stealth = Stealth()
        stealth.apply_stealth_sync(page)

        # 1. Bắt gói tin API item_list trả về từ TikTok
        recent_posts = _setup_network_interception(page, clean_uname)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_selector(
                    '#__UNIVERSAL_DATA_FOR_REHYDRATION__, [data-e2e="user-title"]',
                    timeout=10000,
                )
            except Exception:
                pass
            page.wait_for_timeout(2000)
        except Exception as e:
            logger.warning(f"Timeout khi mở profile @{clean_uname}: {e}")

        # 2. Cuộn trang 2-3 lần để TikTok gửi request API nạp video
        for _ in range(3):
            if len(recent_posts) >= max_posts:
                break
            scroll_amount = random.randint(800, 1500)
            page.evaluate(f"window.scrollBy(0, {scroll_amount});")
            page.wait_for_timeout(2500)

        # 3. Fallback: Nếu không bắt được qua Network, quét bằng DOM
        if not recent_posts:
            soup = BeautifulSoup(page.content(), "html.parser")
            cards = soup.select('[data-e2e="user-post-item"]')
            seen_ids = set()

            for card in cards[:max_posts]:
                a = card.find("a", href=True)
                if not a:
                    continue
                href = a["href"]
                m = re.search(r"/video/(\d+)", href)
                post_id = m.group(1) if m else ""
                if not post_id or post_id in seen_ids:
                    continue
                seen_ids.add(post_id)

                views_el = card.select_one('[data-e2e="video-views"]')
                views_val = parse_stat(views_el.text) if views_el else 0

                img = card.find("img")
                caption = img.get("alt", "") if img else (a.get("title", "") or a.text.strip())

                recent_posts.append(Post(
                    platformPostId=post_id,
                    postUrl=href if href.startswith("http") else f"https://www.tiktok.com{href}",
                    caption=caption,
                    views=views_val,
                    likes=0,
                    comments=0,
                    shares=0,
                    postedAt=None,
                ))

        return recent_posts[:max_posts]
    except Exception as e:
        logger.warning(f"Lỗi khi cào video cho @{clean_uname}: {e}")
        return []
    finally:
        try:
            page.close()
        except Exception:
            pass


def run_worker(limit: int = 50, delay_min: float = 3.0, delay_max: float = 6.0):
    logger.info(f"[*] Bắt đầu Worker cào Recent Posts (Limit: {limit} creators)...")
    creators = fetch_pending_creators(limit)
    logger.info(f"[+] Tìm thấy {len(creators)} Creator chưa có bài post trên Deploy DB.")

    if not creators:
        logger.info("[V] Tất cả Creator đều đã có bài post! Hoàn thành.")
        return

    success_count = 0

    with sync_playwright() as p:
        # Nạp session cookie chuẩn từ data/playwright_state.json
        context, state_path = _init_playwright_context(p)

        for idx, cr in enumerate(creators, 1):
            uname = cr["userName"]
            logger.info(f"[{idx}/{len(creators)}] Đang cào bài post cho: {uname} ({cr['followers']:,} followers)...")

            recent_posts = crawl_recent_posts_for_user(context, uname, max_posts=3)

            if recent_posts:
                # Đóng gói IngestPayload gửi sang Deploy Backend
                creator_info = CreatorInfo(
                    platform=Platform.TIKTOK,
                    externalId=cr["externalId"],
                    username=uname,
                    displayName=cr["displayName"],
                    profileUrl=cr["profileUrl"],
                    avatarUrl=cr["avatarUrl"],
                    followers=cr["followers"],
                )
                payload = IngestPayload(
                    creator=creator_info,
                    recentPosts=recent_posts,
                    aiEnrichment=None,
                    source="BACKGROUND_RECENT_POSTS_WORKER",
                )
                java_data = JavaMapper.from_ingest_payload(payload)
                # Giữ nguyên category & niche sẵn có, gán creatorType hợp lệ để backend chấp nhận
                java_data["creatorType"] = "KOL" if cr["followers"] >= 50000 else "KOC"
                java_data["category"] = cr["category"]
                java_data["niche"] = cr["niche"]

                ok = java_client.send_creator_data(java_data)
                if ok:
                    success_count += 1
                    logger.info(f"  [+] Thành công! Đã lưu {len(recent_posts)} bài post cho {uname} lên Deploy DB.")
                else:
                    logger.warning(f"  [-] Lỗi gửi bài post cho {uname} sang Deploy Backend.")
            else:
                logger.warning(f"  [!] Không tìm thấy bài post cho {uname} (có thể bị TikTok hạn chế hoặc tài khoản riêng tư).")

            # Cập nhật lại session định kỳ
            try:
                context.storage_state(path=state_path)
            except Exception:
                pass

            # Nghỉ ngơi giữa các profile để tránh bị TikTok chặn bot
            sleep_sec = random.uniform(delay_min, delay_max)
            time.sleep(sleep_sec)

        context.browser.close()

    logger.info(f"\n[V] HOÀN TẤT! Đã cào và lưu bài post cho {success_count}/{len(creators)} Creator.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Worker chạy ngầm cào Recent Posts cho các Creator trên Deploy DB")
    parser.add_argument("--limit", "-l", type=int, default=50, help="Số lượng Creator cần cào (0 = tất cả)")
    parser.add_argument("--min-delay", type=float, default=3.0, help="Thời gian nghỉ tối thiểu giữa các kênh (giây)")
    parser.add_argument("--max-delay", type=float, default=6.0, help="Thời gian nghỉ tối đa giữa các kênh (giây)")
    args = parser.parse_args()

    run_worker(limit=args.limit, delay_min=args.min_delay, delay_max=args.max_delay)

