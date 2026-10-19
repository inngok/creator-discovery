import io
import json
import os
import sys

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_score

# Dam bao output khong bi loi font
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Dua thu muc goc vao sys.path de the import duoc cac module cua app
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Import tu thu muc production
from app.ai.services.embedding_service import embedding_service
from app.core.logger import setup_logger

logger = setup_logger(__name__)


# Duong dan file
DATA_DIR = os.path.join(BASE_DIR, "data", "final")
TRAIN_CSV = os.path.join(DATA_DIR, "category_training.csv")
READINESS_JSON = os.path.join(DATA_DIR, "training_readiness.json")

# Thu muc xuat file nhap (candidate)
ARTIFACTS_DIR = os.path.join(BASE_DIR, "artifacts", "category_v1_candidate")
os.makedirs(ARTIFACTS_DIR, exist_ok=True)
MODEL_OUT = os.path.join(ARTIFACTS_DIR, "category_model.joblib")
CLASSES_OUT = os.path.join(ARTIFACTS_DIR, "label_classes.json")
METRICS_OUT = os.path.join(ARTIFACTS_DIR, "training_metrics.json")
CONFIG_OUT = os.path.join(ARTIFACTS_DIR, "training_config.json")


def train_model():
    logger.info("=== BUOC 7: HUAN LUYEN MO HINH CATEGORY (CHECKPOINT E) ===")

    if not os.path.exists(TRAIN_CSV) or not os.path.exists(READINESS_JSON):
        logger.info(
            "[ERROR] Thieu file du lieu input. Vui long chay cac buoc truoc do."
        )
        return

    # 1. Load Data va Readiness JSON
    logger.info(f"-> Dang doc du lieu huan luyen tu {TRAIN_CSV}...")
    df = pd.read_csv(TRAIN_CSV)

    with open(READINESS_JSON, "r", encoding="utf-8") as f:
        readiness = json.load(f)

    ready_classes = [
        cat for cat, info in readiness.items() if info["status"] == "ML_READY"
    ]

    # Chi giu lai nhung data cua cac mon hoc ML_READY
    df = df[df["category"].isin(ready_classes)].copy()
    logger.info(f"-> Tong so mau huan luyen thuc te: {len(df)}")

    # 2. Encode Text thanh Vector bang embedding_service (nhung vao RAM 1 lan)
    logger.info(
        "-> Dang bien dich van ban thanh Vector (Embedding)... Vui long doi vai giay..."
    )
    texts = df["text"].tolist()
    labels = df["category"].tolist()

    X = embedding_service.get_embedding(texts)
    y = np.array(labels)
    logger.info(f"-> Chuyen doi hoan tat! Kich thuoc ma tran huan luyen: {X.shape}")

    # 3. Huan luyen mo hinh Machine Learning (Logistic Regression)
    logger.info(
        "-> Dang bat dau huan luyen mo hinh LogisticRegression voi class_weight='balanced'..."
    )
    model = LogisticRegression(max_iter=2000, class_weight="balanced", random_state=42)

    # Thu kiem tra cheo (Cross Validation) nhanh de do luong do tot ban dau
    logger.info(
        "-> Dang khao sat chat luong cheo tren tap Train (Cross Validation 5-fold)..."
    )
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    scores = cross_val_score(model, X, y, cv=cv, scoring="f1_macro")
    cv_score = float(np.mean(scores))
    logger.info(f"-> Diem F1-Macro uoc tinh: {cv_score:.4f}")

    # Fit chinh thuc
    model.fit(X, y)
    logger.info("-> Huan luyen chinh thuc hoan tat!")

    # 4. Luu ban nhap (Candidate)
    logger.info("-> Dang luu mo hinh vao xuyen thau thu muc artifacts...")
    joblib.dump(model, MODEL_OUT)

    with open(CLASSES_OUT, "w", encoding="utf-8") as f:
        json.dump(list(model.classes_), f, indent=4)

    with open(METRICS_OUT, "w", encoding="utf-8") as f:
        json.dump({"train_cv_macro_f1": cv_score}, f, indent=4)

    with open(CONFIG_OUT, "w", encoding="utf-8") as f:
        json.dump(
            {
                "model_type": "LogisticRegression",
                "class_weight": "balanced",
                "max_iter": 2000,
                "random_state": 42,
            },
            f,
            indent=4,
        )

    logger.info(f"\n[DONE] Da luu toan bo vao {ARTIFACTS_DIR}")
    logger.info("[DONE] HOAN TAT CHECKPOINT E!")


if __name__ == "__main__":
    train_model()
