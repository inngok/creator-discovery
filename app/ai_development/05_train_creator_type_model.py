import io
import json
import os
import sys

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_score

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.ai.services.embedding_service import embedding_service
from app.core.logger import setup_logger

logger = setup_logger(__name__)


# Duong dan file
DATA_DIR = os.path.join(BASE_DIR, "data", "final")
TRAIN_CSV = os.path.join(DATA_DIR, "creator_type_training.csv")

# Thu muc xuat file production luon
PROD_DIR = os.path.join(PROJECT_ROOT, "app", "ai", "models", "creator_type")
os.makedirs(PROD_DIR, exist_ok=True)
MODEL_OUT = os.path.join(PROD_DIR, "creator_type_model.joblib")
CLASSES_OUT = os.path.join(PROD_DIR, "label_classes.json")


def train_model():
    logger.info("=== HUAN LUYEN MO HINH CREATOR TYPE ===")

    if not os.path.exists(TRAIN_CSV):
        logger.info(f"[ERROR] Thieu file du lieu {TRAIN_CSV}")
        return

    # 1. Load Data
    logger.info(f"-> Dang doc du lieu tu {TRAIN_CSV}...")
    df = pd.read_csv(TRAIN_CSV)

    df = df.dropna(subset=["creator_type", "text"])
    logger.info(f"-> Tong so mau huan luyen thuc te: {len(df)}")

    # 2. Encode Text
    logger.info("-> Dang bien dich van ban thanh Vector...")
    texts = df["text"].tolist()
    labels = df["creator_type"].tolist()

    X = embedding_service.get_embedding(texts)
    y = np.array(labels)

    # 3. Huan luyen
    logger.info("-> Dang bat dau huan luyen LogisticRegression...")
    model = LogisticRegression(max_iter=2000, class_weight="balanced", random_state=42)

    try:
        cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
        scores = cross_val_score(model, X, y, cv=cv, scoring="f1_macro")
        logger.info(f"-> Diem F1-Macro uoc tinh: {float(np.mean(scores)):.4f}")
    except Exception:
        logger.info("-> Khong du data de cross-validation, bo qua cham diem...")

    model.fit(X, y)
    logger.info("-> Huan luyen hoan tat!")

    # 4. Luu thang vao Production
    logger.info("-> Dang luu mo hinh vao Production...")
    joblib.dump(model, MODEL_OUT)

    with open(CLASSES_OUT, "w", encoding="utf-8") as f:
        json.dump(list(model.classes_), f, indent=4)

    logger.info(f"\n[DONE] Thanh cong! Mo hinh nam tai: {PROD_DIR}")


if __name__ == "__main__":
    train_model()
