import pandas as pd
import os

# Doc file data
file_path = 'app/ai_development/data/final/master_clean_dataset.csv'
print("Dang doc du lieu tu CSV...")
df = pd.read_csv(file_path, encoding='utf-8-sig')

# Bo quy tac vang (Keywords) de phan loai
category_rules = {
    'FOOD': ['ăn sập', 'đồ ăn', 'ẩm thực', 'nấu ăn', 'food', 'mỏ khoét', 'quán ngon', 'đặc sản', 'bếp', 'review đồ ăn', 'món ngon', 'mukbang', 'ăn uống', 'nhà hàng'],
    'BEAUTY': ['skincare', 'makeup', 'trang điểm', 'son môi', 'dưỡng da', 'trị mụn', 'mỹ phẩm', 'serum', 'làm đẹp', 'toner', 'kem chống nắng'],
    'TECH': ['công nghệ', 'điện thoại', 'laptop', 'phần mềm', 'thủ thuật', 'apple', 'samsung', 'iphone', 'tech', 'review công nghệ', 'pc', 'máy tính'],
    'FASHION': ['phối đồ', 'outfit', 'thời trang', 'local brand', 'quần áo', 'giày', 'sneaker', 'mặc đẹp', 'mix match', 'túi xách', 'phong cách'],
    'LIFESTYLE': ['vlog', 'cuộc sống', 'gia đình', 'decor', 'chữa lành', 'nhật ký', 'trải nghiệm', 'du lịch', 'vlogger', 'nhà', 'hằng ngày', 'tâm sự']
}

creator_type_rules = {
    'KOC': ['review', 'săn sale', 'link ở bio', 'chốt đơn', 'mua tại đây', 'unbox', 'đập hộp', 'giỏ hàng', 'voucher', 'mã giảm giá'],
    'BRAND': ['cửa hàng', 'shop', 'store', 'thương hiệu', 'công ty', 'chính hãng', 'độc quyền', 'đại lý'],
    'KOL': ['chuyên gia', 'official', 'chính thức', 'diễn viên', 'mc', 'ca sĩ', 'đại sứ']
}

def assign_category(text):
    text = str(text).lower()
    for cat, kws in category_rules.items():
        if any(kw in text for kw in kws):
            return cat
    return 'UNKNOWN'

def assign_creator_type(text):
    text = str(text).lower()
    for ctype, kws in creator_type_rules.items():
        if any(kw in text for kw in kws):
            return ctype
    return 'USER'

print("Dang tien hanh dung bo loc Keyword chuyen sau de Don dep data (Clean up)...")
df['category'] = df['bio'].apply(assign_category)
df['creator_type'] = df['bio'].apply(assign_creator_type)

# Thong ke ket qua
print("\n--- KET QUA PHAN LOAI DANH MUC (CATEGORY) ---")
print(df['category'].value_counts())

print("\n--- KET QUA PHAN LOAI LOAI KINH DOANH (CREATOR TYPE) ---")
print(df['creator_type'].value_counts())

# Chi giu lai nhung record duoc phan loai thanh cong (Khong lay UNKNOWN) de train AI cho chuan
clean_df = df[df['category'] != 'UNKNOWN'].copy()
print(f"\nDa loc ra {len(clean_df)} records SACH 100% de huan luyen AI.")

clean_df.to_csv(file_path, index=False, encoding='utf-8-sig')
print(f"Da ghi de thanh cong du lieu sach vao {file_path}")
