# 🤖 Kế Hoạch Auto-Labeling (Teacher-Student ML)

Tài liệu này mô tả chiến lược sử dụng LLM (Google Gemini) làm "Người thầy" để tự động thu thập và dán nhãn dữ liệu (Data Labeling), từ đó huấn luyện lại mô hình Local ML (Học trò) cho dự án Creator Discovery.

## 📌 Bối cảnh và Vấn đề
- **Local ML Model (`category_model.joblib`)**: Hoạt động nhanh, miễn phí, nhưng độ chính xác (confidence) thấp do thiếu tập dữ liệu (dataset) huấn luyện phong phú bằng tiếng Việt.
- **Gemini API**: Hiểu tiếng Việt cực tốt, phân loại xuất sắc nhưng gọi API tốn kém, phụ thuộc mạng và có rủi ro quá tải (Rate limit).
- **Mục tiêu**: Lợi dụng sự thông minh của Gemini để tạo ra dữ liệu huấn luyện cho Local ML hoàn toàn tự động.

## 🚀 Chiến lược "Lấy mỡ nó rán nó" (Knowledge Distillation)

### Giai đoạn 1: Thu thập tự động (Current State)
1. Hệ thống cào dữ liệu (Playwright / RapidAPI) quét thông tin Creator.
2. Local ML tiến hành dự đoán `Category` và `Niche`. 
3. Nếu độ tự tin của ML quá thấp (dưới ngưỡng `CATEGORY_UNKNOWN_THRESHOLD`) => kết quả ra `UNKNOWN`.
4. Lúc này, hệ thống sẽ kích hoạt **Gemini Fallback**. Prompt JSON gửi lên Gemini yêu cầu phân loại lại Category, Niche kèm theo sinh ra Summary, Topic và Tags.
5. Khi Gemini trả về nhãn đúng đắn (Ví dụ: Lê Tuấn Khang -> `LIFESTYLE`), hệ thống tự động ghi **(Text Đầu Vào + Nhãn của Gemini)** xuống database nội bộ.
   - Database: `app/ai/data/ai_training.db` (SQLite).
   - Table: `training_logs`

### Giai đoạn 2: Tự chủ Data (Huấn luyện lại Local ML)
- Sau 1-2 tháng hoạt động, `ai_training.db` sẽ tích lũy được hàng chục nghìn dòng dữ liệu (Text vs Label) cực kỳ xịn sò.
- **Vai trò của Gemini**: Lúc này Gemini đóng vai trò như một "Data Annotator" (Người cày data) miệt mài gán nhãn miễn phí cho hệ thống.
- **Action**: Kéo toàn bộ dữ liệu từ bảng `training_logs` ra, đem vào script train lại model bằng các thư viện ML phổ biến (vd: `scikit-learn`, `XGBoost`).
- **Phạm vi Training**:
  - Dạy lại `category_model.joblib` để nhận diện các Niche/Category tiếng Việt lóng.
  - Dạy lại `creator_type_model.joblib` (KOL, KOC, USER) dựa trên Followers, Engagements kết hợp với nhãn chuẩn mà Gemini đã chấm.
- Kết xuất ra các file model phiên bản 2 (`v2.joblib`) với độ chính xác và tự tin (confidence) vượt trội (>0.8).

### Giai đoạn 3: Độc lập hoàn toàn (Cai nghiện API)
- Bạn đem các model `v2.joblib` gắn vào hệ thống và **tắt hoàn toàn cờ kết nối Gemini API** (`if gemini_model:` -> False).
- Hệ thống lúc này đã "tốt nghiệp", tự vận hành nội bộ 100%. 
- ML sẽ tự tin nhìn vào Caption và số Follower để chốt ngay `KOL`, `KOC` hay `BEAUTY` nhanh như chớp (tốn 0.01 giây/creator) mà không mất thêm bất kỳ chi phí API nào.
- Trọn vẹn giải quyết hoàn toàn rủi ro phụ thuộc: Nhanh, Rẻ, và Độc quyền AI.

---

## ✅ Checklist Công Việc Đã Hoàn Thành
- [x] Tạo file cơ sở dữ liệu `app/ai/training_db.py` tích hợp SQLite cho nhánh AI.
- [x] Sửa logic `process_ai_enrichment` trong `analyzer.py` chuyển sang cơ chế Prompt JSON gộp.
- [x] Tích hợp bộ sinh `Tags`, `SemanticTags` và `Topic` chuẩn hóa xịn sò từ Gemini.
- [x] Tích hợp cơ chế Fallback (Lấp liếm UNKNOWN bằng Gemini).
- [x] Lưu tự động kết quả Fallback xuống SQLite (`ai_training.db`).

## 🛠 Hướng dẫn lấy Data sau này
Để trích xuất dữ liệu đi train, chỉ cần dùng tool DB Browser for SQLite hoặc chạy script python:
```python
import sqlite3
import pandas as pd

conn = sqlite3.connect("app/ai/data/ai_training.db")
df = pd.read_sql_query("SELECT combined_text, category, niche FROM training_logs", conn)
print(f"Total labeled data: {len(df)}")
# Dung df nay de fit vao Sklearn / TF-IDF
```
