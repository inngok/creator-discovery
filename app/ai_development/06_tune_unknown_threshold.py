import os
import sys
import io
import json
import joblib
import numpy as np

# Dam bao terminal khong bi loi font Tieng Viet
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Thu muc goc
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.ai.services.embedding_service import embedding_service

# Duong dan Model nhap
ARTIFACTS_DIR = os.path.join(BASE_DIR, "artifacts", "category_v1_candidate")
MODEL_PATH = os.path.join(ARTIFACTS_DIR, "category_model.joblib")

# Duong dan file Config
CONFIG_DIR = os.path.join(PROJECT_ROOT, "app", "ai")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.py")

# Tap du lieu la (OOD - Out of Distribution)
OOD_SAMPLES = [
    "I trade bitcoin, ethereum and crypto everyday. Join my telegram for trading signals.",
    "Real estate market is crashing. Buy properties now for cheap! #realtor #investment",
    "Let's play League of Legends today! Streaming on Twitch. Gamer life.",
    "Reviewing the latest iPhone 15 Pro Max and Macbook M3. Tech is awesome."
]

# Tap du lieu quen thuoc (IN-DOMAIN)
IN_DOMAIN_SAMPLES = [
    "Skincare routine with new serum. Beauty makeup tutorial.",
    "Outfit of the day, street style fashion lookbook.",
    "My baby is 6 months old! Parenting tips for new moms, diapers review.",
    "Gym workout routine, protein shake, fitness tips for abs."
]

def find_threshold():
    print("=== BUOC 9: TUNE NGUONG 'UNKNOWN' (CHECKPOINT G) ===")
    
    if not os.path.exists(MODEL_PATH):
        print("[ERROR] Khong tim thay Model nhap. Vui long chay buoc 7.")
        return

    model = joblib.load(MODEL_PATH)
    
    # 1. Kiem tra su tu tin cua AI voi Du lieu la (OOD)
    print("\n-> Dang kiem tra AI voi Du lieu la (Crypto, Tech, Game, BDS)...")
    ood_X = embedding_service.get_embedding(OOD_SAMPLES)
    ood_probs = model.predict_proba(ood_X)
    ood_max_probs = np.max(ood_probs, axis=1) # Lay diem tu tin cao nhat
    
    for i, text in enumerate(OOD_SAMPLES):
        print(f"   [La lam] '{text[:30]}...' -> Diem tu tin: {ood_max_probs[i]:.4f}")
    print(f"=> Trung binh Diem tu tin (OOD): {np.mean(ood_max_probs):.4f}")

    # 2. Kiem tra su tu tin cua AI voi Du lieu quen (IN-DOMAIN)
    print("\n-> Dang kiem tra AI voi Du lieu quen (Beauty, Fashion, Parenting)...")
    in_X = embedding_service.get_embedding(IN_DOMAIN_SAMPLES)
    in_probs = model.predict_proba(in_X)
    in_max_probs = np.max(in_probs, axis=1)
    
    for i, text in enumerate(IN_DOMAIN_SAMPLES):
        print(f"   [Quen thuoc] '{text[:30]}...' -> Diem tu tin: {in_max_probs[i]:.4f}")
    print(f"=> Trung binh Diem tu tin (IN-DOMAIN): {np.mean(in_max_probs):.4f}")

    # 3. Tinh toan Nguong (Threshold)
    # Dat nguong lon hon diem tu tin cao nhat cua nhung thu la lam mot chut de AI khong bi nhan vo.
    threshold = round(float(np.max(ood_max_probs) + 0.05), 2)
    # Gioi han nguong de khong bi qua chat hoac qua long
    if threshold > 0.8: threshold = 0.8
    if threshold < 0.4: threshold = 0.4 
    
    print(f"\n-> Phan Tich: Khi gap do la lam, AI thuong co diem tu tin quanh muc {np.mean(ood_max_probs):.2f}.")
    print(f"-> Vi vay, de xuat dat Nguong (Threshold) an toan la: {threshold}")
    print(f"   (Neu AI du doan 1 bai viet co diem < {threshold} thi he thong se bao la UNKNOWN).")

    # 4. Luu vao app/ai/config.py
    print(f"\n-> Dang luu nguong vao {CONFIG_PATH}...")
    
    config_content = f'''"""
Cau hinh cho cac mo hinh AI trong he thong.
"""

# Nguong tu tin toi thieu (Confidence Threshold) cho Category Model.
# Neu mo hinh du doan ra ket qua voi diem tu tin < nguong nay, 
# he thong se phan loai creator nay la "UNKNOWN".
CATEGORY_UNKNOWN_THRESHOLD = {threshold}
'''
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        f.write(config_content)
        
    print(f"[DONE] Da luu thanh cong config vao app/ai/config.py")
    print("[DONE] HOAN TAT CHECKPOINT G!")

if __name__ == "__main__":
    find_threshold()
