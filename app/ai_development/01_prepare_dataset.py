import os
import sys
import io
import pandas as pd
import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Đường dẫn thư mục
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", "final")
INPUT_FILE = os.path.join(DATA_DIR, "master_clean_dataset.csv")

# Các file output
CAT_OUT = os.path.join(DATA_DIR, "category_training.csv")
NICHE_OUT = os.path.join(DATA_DIR, "niche_training.csv")
TYPE_OUT = os.path.join(DATA_DIR, "creator_type_training.csv")

def prepare_dataset():
    print(f"=== BƯỚC 1: ĐỌC DỮ LIỆU TỪ MASTER CLEAN DATASET ===")
    if not os.path.exists(INPUT_FILE):
        print(f"[LỖI] Không tìm thấy file {INPUT_FILE}!")
        return

    df = pd.read_csv(INPUT_FILE)
    print(f"-> Tổng số records ban đầu: {len(df)}")

    # 1. Normalize Null (Xử lý các giá trị NaN thành chuỗi rỗng cho text)
    df['bio'] = df['bio'].fillna('')
    df['recent_posts'] = df['recent_posts'].fillna('')
    df['hashtags'] = df['hashtags'].fillna('')
    
    # Chuẩn hóa nhãn (điền NaN bằng None hoặc khoảng trắng)
    for col in ['category', 'niche', 'creator_type']:
        if col in df.columns:
            # Thay thế các chuỗi rỗng hoặc rác bằng NaN để dễ drop
            df[col] = df[col].replace(r'^\s*$', np.nan, regex=True)

    # 2. Normalize Username (Xóa ký tự @ thừa nếu có)
    if 'username' in df.columns:
        df['username'] = df['username'].astype(str).str.replace('^@', '', regex=True)
    else:
        df['username'] = f"user_" + df.index.astype(str)

    # Đảm bảo có creator_id
    if 'creator_id' not in df.columns:
        df['creator_id'] = df.index

    # 3. Tạo cột Text (bio + recent_posts + hashtags)
    print(f"-> Đang tổng hợp cột 'text' (Bio + Posts + Hashtags)...")
    df['text'] = (df['bio'] + " " + df['recent_posts'] + " " + df['hashtags']).str.strip()
    
    # 4. Loại bỏ các record không có nội dung text nào
    df = df[df['text'] != ""]
    print(f"-> Tổng số records sau khi loại bỏ text rỗng: {len(df)}")

    # Đảm bảo có các cột cơ bản
    for col in ['source', 'human_verified']:
        if col not in df.columns:
            df[col] = "unknown" if col == "source" else False

    # Định nghĩa cấu trúc cột tối thiểu xuất ra
    base_cols = ['creator_id', 'username', 'text', 'source', 'human_verified']

    print(f"\n=== BƯỚC 2: TÁCH CÁC TẬP TRAINING ===")
    
    # 5. Tạo category_training.csv
    if 'category' in df.columns:
        df_cat = df.dropna(subset=['category']).copy()
        df_cat['category'] = df_cat['category'].astype(str).str.upper().str.strip()
        cat_cols = base_cols + ['category']
        df_cat[cat_cols].to_csv(CAT_OUT, index=False)
        print(f"[DONE] Da xuat {len(df_cat)} records co nhan 'category' ra {CAT_OUT}")
    else:
        print("[WARN] Khong co cot 'category' trong master dataset.")

    # 6. Tạo niche_training.csv
    if 'niche' in df.columns:
        df_niche = df.dropna(subset=['niche']).copy()
        df_niche['niche'] = df_niche['niche'].astype(str).str.upper().str.strip()
        niche_cols = base_cols + ['niche']
        df_niche[niche_cols].to_csv(NICHE_OUT, index=False)
        print(f"[DONE] Da xuat {len(df_niche)} records co nhan 'niche' ra {NICHE_OUT}")
    else:
        print("[WARN] Khong co cot 'niche' trong master dataset.")

    # 7. Tạo creator_type_training.csv
    if 'creator_type' in df.columns:
        df_type = df.dropna(subset=['creator_type']).copy()
        df_type['creator_type'] = df_type['creator_type'].astype(str).str.upper().str.strip()
        type_cols = base_cols + ['creator_type']
        df_type[type_cols].to_csv(TYPE_OUT, index=False)
        print(f"[DONE] Da xuat {len(df_type)} records co nhan 'creator_type' ra {TYPE_OUT}")
    else:
        print("[WARN] Khong co cot 'creator_type' trong master dataset.")

    print(f"\n[DONE] HOAN TAT CHUAN BI DATASET (CHECKPOINT A)!")

if __name__ == "__main__":
    prepare_dataset()
