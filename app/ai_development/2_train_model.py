# %% [markdown]
# # Bước 3: Huấn luyện Mô hình Phân loại (Custom ML)
# Script này đóng vai trò như một Jupyter Notebook. 
# Nếu bạn dùng VSCode, bạn có thể bấm "Run Cell" ở các block `# %%` để chạy từng đoạn.
# 
# Mục tiêu: Đọc dữ liệu đã gán nhãn `labeled_clean_data.csv` -> Chuyển Text thành Vector -> Train Logistic Regression -> Lưu model.

# %%
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

print("Đang nạp các thư viện cần thiết...")
import pandas as pd
import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
import joblib
import warnings
warnings.filterwarnings('ignore') # Ẩn các cảnh báo lặt vặt

# %% [markdown]
# ## 1. Đọc Dữ Liệu
# Đọc file dữ liệu sạch đã được AI gán nhãn ở bước 1.

# %%
data_path = 'labeled_clean_data.csv'
print(f"\n1. Đang nạp dữ liệu từ {data_path}...")
try:
    df = pd.read_csv(data_path)
    print(f"-> Nạp thành công {len(df)} mẫu dữ liệu.")
except Exception as e:
    print(f"Lỗi: Không tìm thấy file {data_path}. Vui lòng chạy file 1_auto_label.py trước!")
    exit()

# %% [markdown]
# ## 2. Feature Engineering (Trích xuất đặc trưng)
# Biến đổi chữ viết (Bio, Posts) thành Vector 384 chiều bằng thư viện `sentence-transformers`.
# Chuẩn hóa (Scale) các con số như Followers, ER về thang đo 0-1.

# %%
print("\n2. Đang nạp mô hình Pretrained Embedding (sentence-transformers)...")
print("-> (Lần đầu chạy sẽ hơi lâu vì cần tải model ~80MB từ Internet)")
# Sử dụng model đa ngữ nhẹ và cực tốt
embedder = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')

print("-> Đang nhúng văn bản (Embedding Text)...")
# Gộp Bio và Posts lại thành 1 câu dài để nhúng
df['combined_text'] = df['bio'] + " " + df['recent_posts']
text_embeddings = embedder.encode(df['combined_text'].tolist())

print("-> Đang chuẩn hóa (Scale) Followers và ER...")
scaler = MinMaxScaler()
numerical_features = scaler.fit_transform(df[['followers', 'engagement_rate']])

print("-> Đang gộp các đặc trưng (Vector + Numbers) lại với nhau...")
# Nối (Concatenate) mảng vector text (384 cột) với mảng số (2 cột) => 386 cột
X = np.hstack((text_embeddings, numerical_features))

# Ở đây ta sẽ train thử để nhận diện cột 'category'. Bạn có thể copy code để train cho 'niche' và 'creatorType'.
y = df['category'] 

# %% [markdown]
# ## 3. Chia tập dữ liệu (Train / Test Split)
# Chia dữ liệu ra làm 2 phần: 80% để dạy (Train), 20% để kiểm tra (Test).

# %%
print("\n3. Đang chia tập dữ liệu...")
# Lưu ý: Vì mock data của chúng ta chỉ có 4 dòng, nên code này chia train/test sẽ hơi gượng ép.
# Trong thực tế với 500 mẫu, việc chia này rất mượt.
if len(df) > 2:
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
else:
    # Nếu data quá ít (lúc test), lấy luôn toàn bộ làm train và test.
    X_train, X_test, y_train, y_test = X, X, y, y

print(f"-> Số mẫu để Train: {len(X_train)}, Số mẫu để Test: {len(X_test)}")

# %% [markdown]
# ## 4. Huấn luyện Mô hình Logistic Regression
# Đưa dữ liệu (X_train) và nhãn (y_train) vào cho thuật toán Machine Learning cổ điển.

# %%
print("\n4. Đang huấn luyện mô hình Logistic Regression...")
model = LogisticRegression(max_iter=1000)
model.fit(X_train, y_train)
print("-> Huấn luyện xong! (Chỉ mất vài mili-giây)")

# %% [markdown]
# ## 5. Đánh giá Mô hình (Evaluation)
# Cho model làm bài kiểm tra trên tập Test và chấm điểm.

# %%
print("\n5. Đánh giá Mô hình trên tập Test...")
y_pred = model.predict(X_test)

acc = accuracy_score(y_test, y_pred)
print(f"-> Độ chính xác (Accuracy): {acc * 100:.2f}%")

print("\nBáo cáo chi tiết (Classification Report):")
print(classification_report(y_test, y_pred))

# %% [markdown]
# ## 6. Lưu trữ Mô hình (Export Model)
# Lưu mô hình đã học xong ra file .joblib. Lát nữa backend/API chỉ cần nạp file này lên là dùng được luôn.

# %%
print("\n6. Đang lưu mô hình ra file...")
joblib.dump(model, 'creator_category_model.joblib')
joblib.dump(scaler, 'scaler.joblib') # Cần lưu cả scaler để thực tế còn chuẩn hóa số liệu
print("-> Đã lưu thành công: 'creator_category_model.joblib' và 'scaler.joblib'!")
print("Hoàn tất Bước 3!")
