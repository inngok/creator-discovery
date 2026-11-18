import argparse
import glob
import os
import re
import sys
import time

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from app.ai.services.embedding_service import embedding_service
from app.ai.training_db import save_training_data
from app.clients.java_client import java_client
from app.core.logger import setup_logger
from app.core.location_resolver import resolve_location
from app.workers.tasks import preflight_candidate_task

logger = setup_logger("import_excel")


def clean_username(val: str) -> str:
    """Làm sạch username TikTok từ URL hoặc handle thô."""
    if not val or pd.isna(val):
        return ""
    val = str(val).strip()
    if "tiktok.com" in val:
        m = re.search(r"tiktok\.com/@?([^/?&#]+)", val)
        if m:
            val = m.group(1)
    if val.startswith("@"):
        val = val[1:]
    return val.strip()


import unicodedata


def parse_int_safe(val) -> int:
    """Chuyển đổi an toàn sang số nguyên int (hỗ trợ cả hậu tố K, M, B)."""
    if val is None or pd.isna(val):
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    s = str(val).strip().replace(" ", "").replace(",", ".")
    m = re.match(r"^([\d.]+)([kKmMbB]?)$", s)
    if m:
        num, suffix = m.groups()
        try:
            val_float = float(num)
            suffix = suffix.upper()
            if suffix == "K":
                return int(val_float * 1_000)
            elif suffix == "M":
                return int(val_float * 1_000_000)
            elif suffix == "B":
                return int(val_float * 1_000_000_000)
            return int(val_float)
        except Exception:
            return 0
    clean = re.sub(r"[^\d]", "", s)
    return int(clean) if clean else 0


def find_column(df: pd.DataFrame, possible_names: list[str]) -> str:
    """Tìm tên cột phù hợp không phân biệt hoa thường và chuẩn hóa Unicode NFC."""
    for col in df.columns:
        col_clean = unicodedata.normalize("NFC", str(col)).strip().lower()
        for candidate in possible_names:
            candidate_clean = unicodedata.normalize("NFC", candidate).strip().lower()
            if candidate_clean in col_clean:
                return col
    return ""


def run_import(file_path: str, mode: str = "crawl", delay: float = 0.2, force: bool = False):
    """
    Import Creator từ Excel.
    - mode = 'crawl': Bơm vào Celery cào mới live, AI phân loại, đẩy Java backend.
    - mode = 'direct': Lấy dữ liệu sẵn trong Excel lưu thẳng vào Java Backend & SQLite AI.
    - force: Bỏ qua kiểm tra trùng lặp từ database SQLite cũ.
    """
    if not os.path.exists(file_path):
        logger.error(f"[-] Không tìm thấy file tại: {file_path}")
        return

    logger.info(f"[*] Đang đọc file: {file_path}")
    if file_path.endswith((".xlsx", ".xls")):
        df = pd.read_excel(file_path)
    else:
        df = pd.read_csv(file_path)

    logger.info(f"[+] Tổng số dòng dữ liệu: {len(df)}")
    logger.info(f"[+] Danh sách cột: {list(df.columns)}")

    # 1. Nhận diện các cột
    col_id = find_column(df, ["id chuyên gia", "id", "username", "handle", "link"])
    col_name = find_column(df, ["biệt danh", "tên", "display_name", "tên kênh"])
    col_cat = find_column(df, ["phân loại", "ngành", "category", "lĩnh vực"])
    col_niche = find_column(df, ["xu hướng", "niche", "mặt hàng", "ngách"])
    col_followers = find_column(df, ["tổng lượng theo dõi", "theo dõi", "follower", "followers"])
    col_avatar = find_column(df, ["ảnh hồ sơ", "avatar", "ảnh"])
    col_views = find_column(df, ["lượt xem trung bình", "avg views", "views"])
    col_likes = find_column(df, ["lượt thích trung bình", "avg likes", "likes"])
    col_eng = find_column(df, ["tỷ lệ tương tác", "engagement", "er"])
    col_profile = find_column(df, ["nhà sáng tạo tiktok", "link tiktok", "url"])
    col_contact = find_column(df, ["liên hệ", "contact", "email", "phone", "sđt", "zalo"])

    if not col_id:
        logger.error("[-] Không tìm thấy cột chứa Username/ID Chuyên gia trong file!")
        return

    logger.info(f"[+] Cột Username/ID: '{col_id}'")
    logger.info(f"[+] Cột Tên: '{col_name}' | Followers: '{col_followers}' | Ngành: '{col_cat}'")
    logger.info(f"[+] Cột Views TB: '{col_views}' | Likes TB: '{col_likes}' | Tỷ lệ tương tác: '{col_eng}'")

    # Tải danh sách creator đã có trong hệ thống để chống trùng lặp
    import sqlite3
    from app.ai.training_db import DB_PATH

    existing_usernames = set()
    if not force and os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT username FROM training_logs")
            for r in c.fetchall():
                if r[0]:
                    existing_usernames.add(r[0].replace("@", "").strip().lower())
            conn.close()
            logger.info(f"[i] Đã tải {len(existing_usernames)} creator đã có trong hệ thống để lọc trùng.")
        except Exception as e:
            logger.warning(f"[!] Không thể đọc danh sách cũ: {e}")
    elif force:
        logger.info("[i] Cờ --force được bật: Bỏ qua kiểm tra trùng lặp với danh sách cũ.")

    success_count = 0
    skip_count = 0

    if mode == "crawl":
        logger.info("\n=== CHẾ ĐỘ 1: BƠM VÀO CELERY QUEUE ĐỂ CÀO MỚI LIVE ===")
        for idx, row in df.iterrows():
            raw_id = row[col_id]
            uname = clean_username(raw_id)
            if not uname:
                continue

            uname_lower = uname.lower()
            if uname_lower in existing_usernames:
                skip_count += 1
                continue
            existing_usernames.add(uname_lower)

            try:
                preflight_candidate_task.delay("TIKTOK", uname)
                success_count += 1
                if success_count % 50 == 0 or success_count == len(df):
                    logger.info(f"[Preflight Enqueue] Đã đưa {success_count} creators mới vào Preflight Queue (Đã bỏ qua {skip_count} trùng)...")
                if delay > 0:
                    time.sleep(delay)
            except Exception as e:
                logger.error(f"[-] Lỗi đưa @{uname} vào Preflight Queue: {e}")

        logger.info(f"[V] HOÀN TẤT! Đã đưa {success_count} Creator mới vào Preflight Queue (Bỏ qua {skip_count} creator bị trùng).")
        logger.info("[*] Preflight Worker sẽ kiểm tra nhanh, lọc KOC/KOL hợp lệ trước khi kích hoạt Deep Crawl.")

    elif mode == "direct":
        logger.info("\n=== CHẾ ĐỘ 2: ĐẨY DỮ LIỆU CÓ SẴN THẲNG VÀO DEPLOY BACKEND & AI DB ===")
        for idx, row in df.iterrows():
            raw_id = row[col_id]
            uname = clean_username(raw_id)
            if not uname:
                continue

            uname_lower = uname.lower()
            if uname_lower in existing_usernames:
                skip_count += 1
                continue
            existing_usernames.add(uname_lower)

            d_name = str(row[col_name]).strip() if col_name and not pd.isna(row[col_name]) else uname
            f_count = parse_int_safe(row[col_followers]) if col_followers else 0
            cat = str(row[col_cat]).strip() if col_cat and not pd.isna(row[col_cat]) else "Lifestyle"
            niche = str(row[col_niche]).strip() if col_niche and not pd.isna(row[col_niche]) else "General"
            avatar = str(row[col_avatar]).strip() if col_avatar and not pd.isna(row[col_avatar]) else ""
            views = float(parse_int_safe(row[col_views])) if col_views else 0.0
            likes = float(parse_int_safe(row[col_likes])) if col_likes else 0.0
            p_url = str(row[col_profile]).strip() if col_profile and not pd.isna(row[col_profile]) else f"https://www.tiktok.com/@{uname}"
            contact = str(row[col_contact]).strip() if col_contact and not pd.isna(row[col_contact]) else ""

            # Tỷ lệ tương tác
            er = 0.0
            if col_eng and not pd.isna(row[col_eng]):
                try:
                    clean_er = str(row[col_eng]).replace("%", "").replace(",", ".").strip()
                    er = round(float(clean_er) / 100.0, 4)
                except Exception:
                    er = 0.0

            combined_text = (
                f"KOL/KOC: {d_name} (@{uname})\n"
                f"Lĩnh vực: {cat}\n"
                f"Xu hướng/Ngách: {niche}\n"
                f"Followers: {f_count:,} | Views TB: {views:,.0f} | Likes TB: {likes:,.0f}"
            )

            # 1. Lưu vào SQLite cho AI học
            save_training_data(
                username=f"@{uname}",
                platform="TIKTOK",
                combined_text=combined_text,
                category=cat,
                niche=niche,
                creator_type="KOL" if f_count >= 500000 else "KOC",
                followers=f_count,
                source="EXCEL_IMPORT",
            )

            # 2. Sinh Vector 768 chiều dùng BKAI
            try:
                emb = embedding_service.get_search_embedding(combined_text)
            except Exception:
                emb = []

            # 3. Tính dataConfidence theo 4 thành phần quy đổi:
            # Data Confidence = (35% x Nguồn) + (30% x Độ tươi) + (20% x Độ đầy đủ) + (15% x Hợp lý)
            from app.core.confidence import compute_data_confidence
            excel_conf = compute_data_confidence(
                source_type="EXCEL_IMPORT",
                observed_at=None,
                avatar_url=avatar,
                bio=f"Nhà sáng tạo nội dung {cat} - {niche}",
                followers=f_count,
                avg_views=views,
                avg_likes=likes,
                category=cat,
                niche=niche,
                contact=contact,
                recent_posts=[],
                er=er,
            )

            java_payload = {
                "platform": "TIKTOK",
                "externalId": uname,
                "userName": f"@{uname}",
                "displayName": d_name,
                "avatarUrl": avatar,
                "creatorType": "KOL" if f_count >= 500000 else "KOC",
                "location": resolve_location(display_name=d_name, username=uname),
                "profileUrl": p_url,
                "followers": f_count,
                "avgViews": views,
                "avgLikes": likes,
                "avgComments": 0.0,
                "avgShares": 0.0,
                "engagementRate": er,
                "niche": niche,
                "contentSummary": f"Nhà sáng tạo nội dung {cat} - {niche}",
                "contact": contact,
                "category": cat,
                "freshnessStatus": "FRESH",
                "dataConfidence": excel_conf,
                "recentPosts": [],
                "embedding": emb,
            }

            ok = java_client.send_creator_data(java_payload)
            if ok:
                success_count += 1
                if success_count % 50 == 0:
                    logger.info(f"[Java Ingest] Đã gửi thành công {success_count} creator sang Deploy Backend...")

        logger.info(
            f"[V] HOÀN TẤT DIRECT IMPORT! Đã đẩy thành công {success_count} Creator mới sang Backend Deploy (Đã bỏ qua {skip_count} creator bị trùng)."
        )

        try:
            from app.scripts.auto_update_prototypes import update_prototypes
            logger.info("[*] Đang cho AI học từ khóa mới từ 600 Creator vừa nạp...")
            update_prototypes()
        except Exception as err:
            logger.warning(f"[!] Không thể chạy update_prototypes: {err}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tool Import Creator từ Excel vào Hệ Thống")
    parser.add_argument("--file", "-f", type=str, default="", help="Đường dẫn file Excel (.xlsx) hoặc CSV")
    parser.add_argument(
        "--mode",
        "-m",
        type=str,
        choices=["crawl", "direct"],
        default="crawl",
        help="Chế độ: 'crawl' (bơm vào Celery cào mới) hoặc 'direct' (đẩy thẳng vào DB Deploy)",
    )
    parser.add_argument("--delay", "-d", type=float, default=0.2, help="Độ trễ (giây) giữa các lần đẩy vào Celery")
    parser.add_argument("--force", action="store_true", help="Bỏ qua lọc trùng với database cũ để nạp mới toàn bộ")
    args = parser.parse_args()

    target_file = args.file
    if not target_file:
        excel_files = glob.glob(os.path.join("data", "*.xlsx")) + glob.glob(os.path.join("data", "*.xls"))
        if excel_files:
            target_file = excel_files[0]
            logger.info(f"[i] Tự động chọn file Excel tìm thấy: {target_file}")
        else:
            logger.error("[-] Vui lòng copy file Excel vào thư mục data/ (ví dụ: data/creators.xlsx) hoặc truyền cờ --file")
            sys.exit(1)

    run_import(target_file, mode=args.mode, delay=args.delay, force=args.force)
