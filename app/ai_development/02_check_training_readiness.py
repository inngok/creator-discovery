import io
import json
import os
import sys

import pandas as pd

from app.core.logger import setup_logger

logger = setup_logger(__name__)


# Dam bao output ra terminal khong bi loi font tieng Viet
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Duong dan thu muc
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", "final")
INPUT_FILE = os.path.join(DATA_DIR, "category_training.csv")
OUTPUT_FILE = os.path.join(DATA_DIR, "training_readiness.json")

MIN_SAMPLES_PER_CLASS = 30


def check_readiness():
    logger.info("=== BUOC 3: KIEM TRA DO SAN SANG CUA DU LIEU (CHECKPOINT B) ===")

    if not os.path.exists(INPUT_FILE):
        logger.info(
            f"[ERROR] Khong tim thay file {INPUT_FILE}! Vui long chay buoc 1 truoc."
        )
        return

    # 1. Doc file
    df = pd.read_csv(INPUT_FILE)
    logger.info(f"-> Da tai {len(df)} records tu category_training.csv")

    # 2. Dem so luong mau cua tung Category
    counts = df["category"].value_counts()

    # 3. Phan loai trang thai (ML_READY hay NOT_READY)
    readiness_report = {}
    logger.info("\n--- BAO CAO TRANG THAI ---")

    for category, count in counts.items():
        status = "ML_READY" if count >= MIN_SAMPLES_PER_CLASS else "NOT_READY"
        readiness_report[category] = {
            "count": int(
                count
            ),  # Ep kieu int vi json khong hieu kieu so cua numpy/pandas
            "status": status,
        }
        logger.info(f"- {category.ljust(15)} : {str(count).rjust(3)} mau -> {status}")

    # 4. Xuat bao cao ra file json
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(readiness_report, f, indent=4, ensure_ascii=False)

    logger.info(f"\n[DONE] Da luu bao cao vao {OUTPUT_FILE}")


if __name__ == "__main__":
    check_readiness()
