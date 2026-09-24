import os
import sys
import io
import json
import joblib
import pandas as pd
import numpy as np
from sklearn.metrics import accuracy_score, precision_recall_fscore_support, confusion_matrix

# Dam bao terminal khong loi font
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.ai.services.embedding_service import embedding_service

# Duong dan file input
DATA_DIR = os.path.join(BASE_DIR, "data", "final")
TEST_CSV = os.path.join(DATA_DIR, "category_gold_test.csv")

ARTIFACTS_DIR = os.path.join(BASE_DIR, "artifacts", "category_v1_candidate")
MODEL_PATH = os.path.join(ARTIFACTS_DIR, "category_model.joblib")
CLASSES_PATH = os.path.join(ARTIFACTS_DIR, "label_classes.json")

# Duong dan file output
EVAL_JSON_OUT = os.path.join(ARTIFACTS_DIR, "evaluation.json")
CONFUSION_OUT = os.path.join(ARTIFACTS_DIR, "confusion_matrix.csv")

def evaluate_model():
    print("=== BUOC 8: DANH GIA MO HINH TREN TAP GOLD TEST (CHECKPOINT F) ===")
    
    if not os.path.exists(TEST_CSV) or not os.path.exists(MODEL_PATH):
        print("[ERROR] Khong tim thay file Test hoac Model. Vui long chay cac buoc truoc do.")
        return

    # 1. Load Data
    print(f"-> Dang doc de thi (Gold Test) tu {TEST_CSV}...")
    df_test = pd.read_csv(TEST_CSV)
    print(f"-> Tong so cau hoi trong de: {len(df_test)}")

    # 2. Load Model 
    print("-> Dang dua mo hinh vao phong thi...")
    model = joblib.load(MODEL_PATH)
    with open(CLASSES_PATH, 'r', encoding='utf-8') as f:
        classes = json.load(f)

    # 3. Du doan tren tap Test
    print("-> Dang cho AI lam bai (Du doan)...")
    texts = df_test['text'].tolist()
    y_true = df_test['category'].tolist()
    
    X_test = embedding_service.get_embedding(texts)
    y_pred = model.predict(X_test)

    # 4. Cham diem (Evaluation Metrics)
    acc = accuracy_score(y_true, y_pred)
    precision, recall, f1, support = precision_recall_fscore_support(y_true, y_pred, labels=classes, zero_division=0)
    
    macro_f1 = float(np.mean(f1))
    
    metrics_report = {
        "Accuracy": float(acc),
        "Macro_F1": macro_f1,
        "Class_Metrics": {}
    }

    print("\n--- KET QUA CHAM DIEM (EVALUATION REPORT) ---")
    print(f"Diem trung binh (Accuracy): {acc*100:.2f}/100")
    print(f"Diem nang luc loi (Macro F1): {macro_f1*100:.2f}/100\n")
    print("Chi tiet diem tung mon hoc:")

    for i, cls in enumerate(classes):
        metrics_report["Class_Metrics"][cls] = {
            "Precision": float(precision[i]),
            "Recall": float(recall[i]),
            "F1_Score": float(f1[i]),
            "Support": int(support[i])
        }
        print(f"- {cls.ljust(15)}: Diem thanh phan (F1) = {f1[i]:.4f} | Ti le tra loi dung = {recall[i]*100:.1f}% (Tren tong {support[i]} cau)")

    # 5. Ma tran nham lan (Confusion Matrix)
    cm = confusion_matrix(y_true, y_pred, labels=classes)
    df_cm = pd.DataFrame(cm, index=[f"ThucTe_{c}" for c in classes], columns=[f"DuDoan_{c}" for c in classes])
    
    # 6. Xuat file
    with open(EVAL_JSON_OUT, 'w', encoding='utf-8') as f:
        json.dump(metrics_report, f, indent=4)
    
    df_cm.to_csv(CONFUSION_OUT)

    print(f"\n[DONE] Da luu bang diem chi tiet vao {EVAL_JSON_OUT}")
    print(f"[DONE] Da luu Ma tran Nham lan vao {CONFUSION_OUT}")
    print("[DONE] HOAN TAT CHECKPOINT F!")

if __name__ == "__main__":
    evaluate_model()
