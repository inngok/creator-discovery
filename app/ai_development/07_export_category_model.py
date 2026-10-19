import io
import json
import os
import shutil
import sys
from datetime import datetime

# Dam bao terminal khong bi loi font
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Duong dan goc
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.ai.config import CATEGORY_UNKNOWN_THRESHOLD
from app.core.logger import setup_logger

logger = setup_logger(__name__)


# Duong dan ben Phong thi nghiem (Artifacts)
ARTIFACTS_DIR = os.path.join(BASE_DIR, "artifacts", "category_v1_candidate")
MODEL_CANDIDATE = os.path.join(ARTIFACTS_DIR, "category_model.joblib")
CLASSES_CANDIDATE = os.path.join(ARTIFACTS_DIR, "label_classes.json")
EVAL_JSON = os.path.join(ARTIFACTS_DIR, "evaluation.json")

# Duong dan thuc te tren Production (Mat tran)
PROD_MODELS_DIR = os.path.join(PROJECT_ROOT, "app", "ai", "models", "category")
PROD_MODEL_PATH = os.path.join(PROD_MODELS_DIR, "category_model.joblib")
PROD_CLASSES_PATH = os.path.join(PROD_MODELS_DIR, "label_classes.json")
PROD_METADATA_PATH = os.path.join(PROD_MODELS_DIR, "metadata.json")

# Diem thi thap nhat de duoc ra truong
PASS_THRESHOLD = 0.40


def promote_model():
    logger.info("=== BUOC 10: DONG GOI VA XUAT XUONG MO HINH (CHECKPOINT H) ===")

    if not os.path.exists(EVAL_JSON):
        logger.info("[ERROR] Khong tim thay bang diem. Vui long thi cu (Buoc 8) truoc.")
        return

    # 1. Kiem tra bang diem cua AI
    logger.info("-> Dang doc bang diem cua AI...")
    with open(EVAL_JSON, "r", encoding="utf-8") as f:
        eval_data = json.load(f)

    macro_f1 = eval_data.get("Macro_F1", 0.0)

    logger.info(
        f"-> Diem thi Macro F1 cua AI hien tai: {macro_f1:.4f} (Diem san de qua mon: >= {PASS_THRESHOLD:.4f})"
    )

    if macro_f1 < PASS_THRESHOLD:
        logger.info(
            "\n[REJECTED] AI HOC DOT! Mo hinh da bi hoi dong ky thuat tu choi xat xuong."
        )
        logger.info("Vui long bo sung them du lieu huan luyen vao de no khon hon.")
        return

    logger.info("\n[APPROVED] XIN CHUC MUNG! Mo hinh da du diem dau.")

    # 2. Tao thu muc tren Production
    os.makedirs(PROD_MODELS_DIR, exist_ok=True)

    # 3. "Be" model tu Phong thi nghiem sang Mat tran thuc te
    logger.info(
        "-> Dang van chuyen Bo nao (Model) va Danh sach lop (Classes) sang Production (app/ai/models/category/)..."
    )
    shutil.copy2(MODEL_CANDIDATE, PROD_MODEL_PATH)
    shutil.copy2(CLASSES_CANDIDATE, PROD_CLASSES_PATH)

    # 4. Lap Ho so ly lich (Metadata) de Backend quan ly version
    logger.info("-> Dang dong moc Ho so ly lich (metadata.json)...")
    metadata = {
        "version": "v1.0.0",
        "release_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "metrics": {"macro_f1": macro_f1, "accuracy": eval_data.get("Accuracy", 0.0)},
        "config": {"unknown_threshold": CATEGORY_UNKNOWN_THRESHOLD},
        "description": "Bo nao phan loai Chu de (Category) phien ban Dau tien.",
    }

    with open(PROD_METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=4)

    logger.info("\n[DONE] DA DONG GOI THANH CONG LEN PRODUCTION!")
    logger.info(f"       Duong dan: {PROD_MODELS_DIR}")
    logger.info("[DONE] HOAN TAT CHECKPOINT H!")


if __name__ == "__main__":
    promote_model()
