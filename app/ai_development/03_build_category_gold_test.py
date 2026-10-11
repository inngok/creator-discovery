import io
import json
import os
import sys

import pandas as pd
from sklearn.model_selection import train_test_split

from app.core.logger import setup_logger

logger = setup_logger(__name__)


sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", "final")
INPUT_CSV = os.path.join(DATA_DIR, "category_training.csv")
READINESS_JSON = os.path.join(DATA_DIR, "training_readiness.json")

OUT_GOLD_TEST = os.path.join(DATA_DIR, "category_gold_test.csv")
OUT_TRAIN_V1 = os.path.join(DATA_DIR, "category_train_v1.csv")

TEST_SIZE = 0.20  # 20%


def build_gold_test():
    logger.info("=== BUOC 4: TAO TAP KIEM TRA GOLD TEST (CHECKPOINT C) ===")

    if not os.path.exists(INPUT_CSV) or not os.path.exists(READINESS_JSON):
        logger.info("[ERROR] Thieu file input. Vui long chay Buoc 1 va Buoc 2 truoc.")
        return

    # 1. Doc du lieu
    df = pd.read_csv(INPUT_CSV)
    with open(READINESS_JSON, "r", encoding="utf-8") as f:
        readiness = json.load(f)

    # 2. Xac dinh cac nhan ML_READY
    ready_classes = [
        cat for cat, info in readiness.items() if info["status"] == "ML_READY"
    ]
    logger.info(f"-> Cac chu de ML_READY duoc phep train: {ready_classes}")

    # Loc dataframe chi lay nhung class ML_READY
    df_ready = df[df["category"].isin(ready_classes)].copy()

    # 3. Chia tap Train / Test
    # Su dung tham so stratify de dam bao ty le cac mon hoc (class) deu nhau o ca 2 tap
    df_train, df_test = train_test_split(
        df_ready, test_size=TEST_SIZE, random_state=42, stratify=df_ready["category"]
    )

    logger.info("-> Da chia data thanh cong:")
    logger.info(f"   - So mau de Day (Train)      : {len(df_train)} records")
    logger.info(f"   - So mau de Kiem tra (Test)  : {len(df_test)} records")

    # 4. Xuat file
    df_test.to_csv(OUT_GOLD_TEST, index=False)
    df_train.to_csv(OUT_TRAIN_V1, index=False)

    logger.info(f"\n[DONE] Da xuat tap Kiem tra : {OUT_GOLD_TEST}")
    logger.info(f"[DONE] Da xuat tap Huan luyen: {OUT_TRAIN_V1}")
    logger.info("[DONE] HOAN TAT CHECKPOINT C!")


if __name__ == "__main__":
    build_gold_test()
