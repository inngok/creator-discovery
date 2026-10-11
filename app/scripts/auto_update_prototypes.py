import json
import os
import sqlite3

import yake

from app.core.logger import setup_logger

logger = setup_logger(__name__)


# Đường dẫn tĩnh tới các file
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "ai", "data", "ai_training.db")
PROTOTYPES_PATH = os.path.join(BASE_DIR, "ai", "data", "prototypes.json")


def get_new_training_data():
    if not os.path.exists(DB_PATH):
        logger.info(f"[-] Không tìm thấy Database tại {DB_PATH}")
        return []

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Lấy các bản ghi do Gemini tự động gán nhãn
    cursor.execute("""
        SELECT category, niche, combined_text 
        FROM training_logs 
        WHERE source = 'GEMINI_AUTO_LABEL' 
        AND category IS NOT NULL 
        AND category != 'UNKNOWN'
    """)
    rows = cursor.fetchall()
    conn.close()
    return rows


def extract_keywords(text: str, top_n: int = 5):
    if not text or len(text.strip()) == 0:
        return []
    # Dùng YAKE để lấy từ khóa (Cụm 1-2 từ để có ý nghĩa nhất cho Prototype)
    kw_extractor = yake.KeywordExtractor(lan="vi", n=2, dedupLim=0.8, top=top_n)
    keywords = kw_extractor.extract_keywords(text)
    return [kw[0] for kw in keywords]


def update_prototypes():
    rows = get_new_training_data()
    if not rows:
        logger.info("[!] Không có dữ liệu huấn luyện mới từ Gemini trong Database.")
        return

    logger.info(f"[*] Đang phân tích {len(rows)} bài viết khó do Gemini gán nhãn...")

    # Gom nhóm bài viết theo Category và Niche
    cat_texts = {}
    niche_texts = {}

    for cat, niche, text in rows:
        if cat:
            cat_texts[cat] = cat_texts.get(cat, "") + " " + text
        if cat and niche and niche != "UNKNOWN":
            key = (cat, niche)
            niche_texts[key] = niche_texts.get(key, "") + " " + text

    # Load thư viện JSON cũ
    if not os.path.exists(PROTOTYPES_PATH):
        logger.info("[-] Không tìm thấy file prototypes.json")
        return

    with open(PROTOTYPES_PATH, "r", encoding="utf-8") as f:
        protos = json.load(f)

    cat_protos = protos.get("CATEGORY_PROTOTYPES", {})
    niche_protos = protos.get("NICHE_PROTOTYPES", {})

    updated_count = 0

    # 1. Khai thác và đắp thêm từ khóa cho Lĩnh vực (Category)
    logger.info("\n--- ĐANG CẬP NHẬT TỪ KHÓA LĨNH VỰC (CATEGORY) ---")
    for cat, text in cat_texts.items():
        new_kws = extract_keywords(text, top_n=10)  # Lấy top 10 từ khóa mới
        if cat in cat_protos:
            added = []
            for kw in new_kws:
                # Tránh trùng lặp từ khóa đã có trong sách giáo khoa
                if kw.lower() not in cat_protos[cat].lower():
                    cat_protos[cat] += f" {kw}"
                    added.append(kw)
            if added:
                logger.info(f"[+] {cat}: Thêm từ khóa mới -> {', '.join(added)}")
                updated_count += len(added)

    # 2. Khai thác và đắp thêm từ khóa cho Ngách (Niche)
    logger.info("\n--- ĐANG CẬP NHẬT TỪ KHÓA NGÁCH (NICHE) ---")
    for (cat, niche), text in niche_texts.items():
        new_kws = extract_keywords(text, top_n=5)  # Lấy top 5 từ khóa ngách
        if cat in niche_protos and niche in niche_protos[cat]:
            added = []
            for kw in new_kws:
                if kw.lower() not in niche_protos[cat][niche].lower():
                    niche_protos[cat][niche] += f" {kw}"
                    added.append(kw)
            if added:
                logger.info(
                    f"[+] {cat} -> {niche}: Thêm từ khóa mới -> {', '.join(added)}"
                )
                updated_count += len(added)

    # 3. Lưu đè lại file JSON
    if updated_count > 0:
        protos["CATEGORY_PROTOTYPES"] = cat_protos
        protos["NICHE_PROTOTYPES"] = niche_protos
        with open(PROTOTYPES_PATH, "w", encoding="utf-8") as f:
            json.dump(protos, f, ensure_ascii=False, indent=2)
        logger.info(
            f"\n[V] THÀNH CÔNG! Đã cập nhật thêm {updated_count} từ khóa mới vào file prototypes.json."
        )
        logger.info(
            "[*] AI của bạn đã tự động thông minh hơn mà không cần restart Server!"
        )
    else:
        logger.info(
            "\n[*] Hệ thống đã tối ưu, chưa phát hiện từ khóa lóng/xu hướng nào mới lạ cần thêm vào."
        )


if __name__ == "__main__":
    logger.info("==================================================")
    logger.info(" VÒNG LẶP HỌC TẬP (DATA FLYWHEEL) - AUTO RETRAIN")
    logger.info("==================================================")
    update_prototypes()
