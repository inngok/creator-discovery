import argparse
import json
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

from app.ai.creator_classifier import classify_creator_type_smart
from app.clients.java_client import java_client
from app.core.logger import setup_logger
from app.core.models import AIEnrichment, CreatorInfo, IngestPayload, Platform, Post
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


def fetch_pending_creators(limit: int = 50, shortlist_only: bool = False) -> list[dict]:
    """Lấy danh sách các Creator trên Deploy DB chưa có bài post trong bảng post (hoặc chỉ trong Shortlist)."""
    where_clause = "WHERE c.id NOT IN (SELECT DISTINCT creator_id FROM post)"
    if shortlist_only:
        where_clause += " AND c.id IN (SELECT DISTINCT creator_id FROM shortlist_item)"

    query = f"""
        SELECT c.id, c.external_id, c.user_name, c.display_name, c.avatar_url, c.profile_url, 
               COALESCE(m.followers, 0), COALESCE(m.category, 'Lifestyle'), COALESCE(m.niche, 'General'),
               COALESCE(m.contact, ''), COALESCE(m.content_summary, '')
        FROM creator c
        LEFT JOIN public_creator_metric m ON c.id = m.creator_id
        {where_clause}
        ORDER BY m.followers DESC
        {"LIMIT " + str(limit) if limit > 0 else ""};
    """
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
                "contact": str(r[9]) if r[9] else "",
                "contentSummary": str(r[10]) if r[10] else "",
            })
        conn.close()
        return creators
    except Exception as e:
        logger.error(f"[-] Lỗi khi query danh sách creator từ Deploy DB: {e}")
        return []


def delete_creator_from_db(creator_id: str, username: str, reason: str = "Tài khoản riêng tư / Không tồn tại") -> bool:
    """Xóa hoàn toàn Creator không hợp lệ (Riêng tư / Đã xóa) khỏi Database Deploy."""
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
        # Xóa các ràng buộc bảng liên quan
        cur.execute("DELETE FROM public_creator_metric WHERE creator_id = %s;", (creator_id,))
        cur.execute("DELETE FROM post WHERE creator_id = %s;", (creator_id,))
        cur.execute("DELETE FROM shortlist_item WHERE creator_id = %s;", (creator_id,))
        cur.execute("DELETE FROM relationship WHERE creator_id = %s;", (creator_id,))
        cur.execute("DELETE FROM collaboration WHERE creator_id = %s;", (creator_id,))
        cur.execute("DELETE FROM creator_claim WHERE creator_id = %s;", (creator_id,))
        cur.execute("DELETE FROM creator_invitation WHERE target_creator_id = %s;", (creator_id,))
        cur.execute("DELETE FROM creator_provider_identity WHERE creator_id = %s;", (creator_id,))
        cur.execute("DELETE FROM creator_account_verification WHERE creator_id = %s;", (creator_id,))
        cur.execute("DELETE FROM creator WHERE id = %s;", (creator_id,))
        conn.commit()
        conn.close()
        logger.info(f"  [XÓA CREATOR] Đã xóa {username} khỏi DB (Lý do: {reason}).")
        return True
    except Exception as e:
        logger.error(f"  [-] Lỗi khi xóa {username} khỏi DB: {e}")
        return False


def crawl_recent_posts_for_user(context, username: str, max_posts: int = 3) -> tuple[list[Post], str, str | None]:
    """
    Cào danh sách video gần đây từ TikTok.
    Trả về: (posts, signature, status_flag)
    status_flag:
      - None: bình thường
      - 'BLOCKED_CAPTCHA': TikTok bắt giải captcha hoặc chặn IP
      - 'PRIVATE': Tài khoản riêng tư
      - 'NOT_FOUND': Tài khoản không tồn tại / đã xóa / bị ban
    """
    clean_uname = username.strip().removeprefix("@")
    url = f"https://www.tiktok.com/@{clean_uname}"
    page = context.new_page()

    try:
        stealth = Stealth()
        stealth.apply_stealth_sync(page)

        recent_posts = _setup_network_interception(page, clean_uname)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
        except Exception as e:
            logger.warning(f"Timeout khi mở profile @{clean_uname}: {e}")

        # 1. KIỂM TRA CAPTCHA / BỊ CHẶN
        has_captcha_el = page.query_selector('#tiktok-verify-ele, .captcha_verify_container, [data-e2e="verify-bar"]')
        if has_captcha_el and has_captcha_el.is_visible():
            return [], "", "BLOCKED_CAPTCHA"

        # 2. KIỂM TRA DỮ LIỆU GỐC TRÊN TRANG (Universal Data)
        soup_data = page.evaluate("() => document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__')?.textContent")
        user_detail = {}
        if soup_data:
            try:
                d = json.loads(soup_data)
                user_detail = d.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {})
            except Exception:
                pass

        status_code = user_detail.get("statusCode", None)
        user_info = user_detail.get("userInfo", {}).get("user", {})
        is_secret = user_info.get("secret", False)
        signature = user_info.get("signature", "")
        if not signature:
            try:
                bio_el = page.query_selector('[data-e2e="user-bio"]')
                if bio_el:
                    signature = bio_el.inner_text().strip()
            except Exception:
                pass

        # 3. KIỂM TRA TÀI KHOẢN KHÔNG TỒN TẠI / BỊ XÓA
        not_found_el = page.query_selector('[data-e2e="user-not-found"]')
        if (not_found_el and not_found_el.is_visible()) or status_code in [10202, 10222, 10221]:
            return [], "", "NOT_FOUND"

        # 4. KIỂM TRA TÀI KHOẢN RIÊNG TƯ (PRIVATE)
        private_el = page.query_selector('[data-e2e="user-private"]')
        if (private_el and private_el.is_visible()) or is_secret is True:
            return [], "", "PRIVATE"

        # 5. CUỘN TRANG ĐỂ KÍCH HOẠT API ITEM_LIST
        for _ in range(3):
            if len(recent_posts) >= max_posts:
                break
            scroll_amount = random.randint(800, 1500)
            page.evaluate(f"window.scrollBy(0, {scroll_amount});")
            page.wait_for_timeout(2500)

        # Kiểm tra lại Captcha sau khi cuộn
        has_captcha_el = page.query_selector('#tiktok-verify-ele, .captcha_verify_container, [data-e2e="verify-bar"]')
        if has_captcha_el and has_captcha_el.is_visible():
            return [], "", "BLOCKED_CAPTCHA"

        # 6. FALLBACK: NẾU NETWORK CHƯA KỊP BẮT, CÀO TRỰC TIẾP TỪ DOM
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

        return recent_posts[:max_posts], signature, None
    except Exception as e:
        logger.warning(f"Lỗi khi cào video cho @{clean_uname}: {e}")
        return [], "", None
    finally:
        try:
            page.close()
        except Exception:
            pass


def run_worker(limit: int = 50, delay_min: float = 4.0, delay_max: float = 7.0, shortlist_only: bool = False):
    target_desc = "các Creator trong Shortlist" if shortlist_only else "tất cả Creator chưa có post"
    logger.info(f"[*] Bắt đầu Worker cào Recent Posts cho {target_desc} (Limit: {limit})...")
    creators = fetch_pending_creators(limit, shortlist_only=shortlist_only)
    logger.info(f"[+] Tìm thấy {len(creators)} Creator cần cào trên Deploy DB.")

    if not creators:
        logger.info(f"[V] Không có Creator nào thuộc diện cần cào ({target_desc})! Hoàn thành.")
        return

    success_count = 0
    deleted_count = 0
    consecutive_empty = 0

    with sync_playwright() as p:
        context, state_path = _init_playwright_context(p)

        for idx, cr in enumerate(creators, 1):
            uname = cr["userName"]
            logger.info(f"[{idx}/{len(creators)}] Đang cào bài post cho: {uname} ({cr['followers']:,} followers)...")

            recent_posts, live_bio, status_flag = crawl_recent_posts_for_user(context, uname, max_posts=3)

            # TRƯỜNG HỢP 1: BỊ TIKTOK CHẶN / CAPTCHA -> DỪNG NGAY
            if status_flag == "BLOCKED_CAPTCHA":
                logger.error(
                    f"\n[DỪNG CÀO NGAY LẬP TỨC] Phát hiện TikTok chặn và bắt giải Captcha tại kênh {uname}!"
                )
                logger.error("[!] Worker tự động dừng để bảo vệ IP và tránh bị ban tài khoản.")
                break

            # TRƯỜNG HỢP 2: TÀI KHOẢN RIÊNG TƯ HOẶC ĐÃ BỊ XÓA -> XÓA LUÔN KHỎI DB
            if status_flag in ["PRIVATE", "NOT_FOUND"]:
                reason_text = "Tài khoản riêng tư (Private)" if status_flag == "PRIVATE" else "Tài khoản không tồn tại / Đã xóa"
                delete_creator_from_db(cr["id"], uname, reason=reason_text)
                deleted_count += 1
                consecutive_empty = 0
                time.sleep(1.5)
                continue

            # TRƯỜNG HỢP 3: LẤY ĐƯỢC BÀI POST THÀNH CÔNG
            if recent_posts:
                consecutive_empty = 0

                bio_str = live_bio or cr.get("contentSummary", "")
                contact_str = cr.get("contact", "")

                # 1. Phân loại thông minh KOC vs KOL dựa trên nội dung video + bio + followers
                smart_type = classify_creator_type_smart(
                    username=uname,
                    display_name=cr["displayName"],
                    bio=bio_str,
                    followers=cr["followers"],
                    recent_posts=recent_posts,
                    category=cr["category"],
                    niche=cr["niche"],
                )

                # 2. Xây dựng AIEnrichment đầy đủ với đúng Category, Niche, và Smart Creator Type
                ai_enrichment = AIEnrichment(
                    category=cr["category"],
                    niche=cr["niche"],
                    creatorType=smart_type,
                    contentSummary=bio_str or f"Nhà sáng tạo nội dung {cr['category']} - {cr['niche']}",
                )

                creator_info = CreatorInfo(
                    platform=Platform.TIKTOK,
                    externalId=cr["externalId"],
                    username=uname,
                    displayName=cr["displayName"],
                    profileUrl=cr["profileUrl"],
                    avatarUrl=cr["avatarUrl"],
                    followers=cr["followers"],
                    bio=bio_str,
                    publicEmail=contact_str if "@" in contact_str else None,
                    publicPhone=contact_str if ("@" not in contact_str and contact_str) else None,
                )
                payload = IngestPayload(
                    creator=creator_info,
                    recentPosts=recent_posts,
                    aiEnrichment=ai_enrichment,
                    source="BACKGROUND_RECENT_POSTS_WORKER",
                )

                # 3. JavaMapper tự động tính toán dataConfidence hoàn toàn động:
                #    - Ngành/ngách thực tế (0.85 nếu cụ thể, 0.65 nếu chung chung)
                #    - Thông tin liên hệ / booking trong bio (+0.10)
                #    - Chỉ số tương tác thực tế (+0.05)
                #    Đồng thời sinh vector BKAI chuẩn xác theo đúng nội dung bài post và chuyên mục!
                java_data = JavaMapper.from_ingest_payload(payload)

                ok = java_client.send_creator_data(java_data)
                if ok:
                    success_count += 1
                    conf_val = java_data.get("dataConfidence", 0.0)
                    logger.info(
                        f"  [+] Thành công! Đã lưu {len(recent_posts)} bài post ({smart_type} | Conf: {conf_val}) cho {uname} lên Deploy DB."
                    )
                else:
                    logger.warning(f"  [-] Lỗi gửi bài post cho {uname} sang Deploy Backend.")

            else:
                consecutive_empty += 1
                logger.warning(f"  [!] Không tìm thấy bài post cho {uname} (Lần rỗng liên tiếp: {consecutive_empty}/4).")

                # Nếu bị rỗng liên tiếp 4 kênh công khai -> Nghi ngờ TikTok rate-limit IP
                if consecutive_empty >= 4:
                    logger.error(
                        "\n[🛑 DỪNG CÀO] 4 kênh công khai liên tiếp không tải được video (TikTok rate-limit IP / chặn mạng)."
                    )
                    logger.error("[!] Tự động dừng cào để tránh bị TikTok blacklist IP.")
                    break

            # Cập nhật session định kỳ
            try:
                context.storage_state(path=state_path)
            except Exception:
                pass

            sleep_sec = random.uniform(delay_min, delay_max)
            time.sleep(sleep_sec)

        context.browser.close()

    logger.info(f"\n[V] HOÀN TẤT PHIÊN LÀM VIỆC!")
    logger.info(f" - Đã lưu bài post thành công: {success_count} Creator")
    logger.info(f" - Đã xóa Creator riêng tư/hỏng: {deleted_count} Creator")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Worker chạy ngầm cào Recent Posts cho các Creator trên Deploy DB")
    parser.add_argument("--limit", "-l", type=int, default=0, help="Số lượng Creator cần cào (0 = tất cả)")
    parser.add_argument("--shortlist-only", "-s", action="store_true", help="Chỉ cào các Creator đang nằm trong Shortlist")
    parser.add_argument("--min-delay", type=float, default=4.0, help="Thời gian nghỉ tối thiểu giữa các kênh (giây)")
    parser.add_argument("--max-delay", type=float, default=7.0, help="Thời gian nghỉ tối đa giữa các kênh (giây)")
    args = parser.parse_args()

    run_worker(limit=args.limit, delay_min=args.min_delay, delay_max=args.max_delay, shortlist_only=args.shortlist_only)

