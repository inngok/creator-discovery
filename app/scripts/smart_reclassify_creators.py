import os
import sys
import psycopg2
from dotenv import load_dotenv

load_dotenv()

from app.ai.creator_classifier import classify_creator_type_smart
from app.core.logger import setup_logger

logger = setup_logger("smart_reclassify")


def run_smart_reclassification():
    logger.info("[*] Bắt đầu quét và phân loại thông minh KOC/KOL dựa trên bài đăng...")

    conn = psycopg2.connect(
        host="221.121.3.226",
        user="influencermatch_user",
        password="12345",
        dbname="influencermatch_dev"
    )
    cur = conn.cursor()

    # 1. Lấy tất cả creator kèm metric và bài post
    query = """
        SELECT c.id, c.user_name, c.display_name, c.bio, 
               COALESCE(m.followers, 0), COALESCE(m.category, ''), COALESCE(m.niche, ''),
               m.creator_type
        FROM creator c
        LEFT JOIN public_creator_metric m ON c.id = m.creator_id;
    """
    cur.execute(query)
    creators = cur.fetchall()
    logger.info(f"[+] Tìm thấy tổng cộng {len(creators)} Creator trong hệ thống.")

    # 2. Lấy danh sách posts gom theo creator_id
    cur.execute("SELECT creator_id, caption, post_url FROM post;")
    posts_by_creator = {}
    for r in cur.fetchall():
        cid = str(r[0])
        if cid not in posts_by_creator:
            posts_by_creator[cid] = []
        posts_by_creator[cid].append({"caption": r[1] or "", "post_url": r[2] or ""})

    updated_count = 0
    koc_count = 0
    kol_count = 0

    update_payloads = []

    for cr in creators:
        cid = str(cr[0])
        uname = cr[1] or ""
        dname = cr[2] or ""
        bio = cr[3] or ""
        followers = int(cr[4]) if cr[4] else 0
        cat = cr[5] or ""
        niche = cr[6] or ""
        old_type = cr[7] or ""

        recent_posts = posts_by_creator.get(cid, [])

        smart_type = classify_creator_type_smart(
            username=uname,
            display_name=dname,
            bio=bio,
            followers=followers,
            recent_posts=recent_posts,
            category=cat,
            niche=niche,
            classified_type=old_type,
        )

        if smart_type == "KOL":
            kol_count += 1
        else:
            koc_count += 1

        if smart_type != old_type:
            update_payloads.append((smart_type, cid))

    if update_payloads:
        cur.executemany(
            "UPDATE public_creator_metric SET creator_type = %s WHERE creator_id = %s;",
            update_payloads
        )
        conn.commit()
        updated_count = len(update_payloads)

    conn.close()

    logger.info("\n" + "=" * 50)
    logger.info(f"[V] HOÀN TẤT PHÂN LOẠI THÔNG MINH!")
    logger.info(f" - Tổng KOC: {koc_count} kênh")
    logger.info(f" - Tổng KOL: {kol_count} kênh")
    logger.info(f" - Số kênh được điều chỉnh lại nhãn chuẩn xác: {updated_count} kênh")
    logger.info("=" * 50)


if __name__ == "__main__":
    run_smart_reclassification()
