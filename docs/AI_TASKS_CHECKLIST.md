# Checklist Công Việc Dành Riêng Cho AI Developer

Vì bạn sẽ chỉ tập trung vào phần phát triển Mô hình AI (Crawler sẽ do người khác làm), bạn không cần quan tâm đến các API cào dữ liệu TikTok/YouTube. Trọng tâm của bạn là **Nhận dữ liệu thô -> Xử lý (Nghiên cứu) -> Trả ra nhãn phân loại (Inference)**.

Dưới đây là Checklist chi tiết từng bước dành riêng cho vai trò AI Developer. Bạn có thể check (tick) vào từng mục sau khi làm xong.

---

## 1. Thống nhất Contract (Dữ liệu đầu vào từ Crawler)
Vì bạn không làm Crawler, bạn cần thống nhất với người làm Crawler về định dạng file dữ liệu thô (Raw Data) họ sẽ gửi cho bạn để train model.
- [ ] Thống nhất schema JSON hoặc CSV đầu vào. Bắt buộc phải có: `username`, `followers`, `engagement_rate`, `bio`, và `recent_posts` (gồm mảng caption + hashtags).
- [ ] Tạo một file dữ liệu giả (Mock Data) `raw_data_sample.csv` (ví dụ: đã tạo trong `app/ai_development/`) gồm khoảng 10-20 dòng để code nháp trong khi chờ Crawler hoàn thiện.

---

## 2. Xây dựng Script Gán Nhãn Tự Động (Auto-Labeling Pipeline)
Mục đích: Dùng Gemini làm "thầy giáo" để tự động gán nhãn cho hàng trăm/ngàn dữ liệu thô, tạo ra tập dữ liệu sạch để train mô hình của riêng bạn.

- [ ] Tạo thư mục `app/ai_development/` để chứa code nghiên cứu (Offline / Sandbox).
- [ ] Viết script `1_auto_label.py`: 
  - Đọc file `raw_data_sample.csv` (Mock Data).
  - Sử dụng thư viện `google.generativeai` gọi model `gemini-2.5-flash`.
  - Sử dụng tính năng **Structured Output (response_schema)** của Gemini kết hợp `Pydantic` để ép nó trả về JSON chuẩn form.
- [ ] Thêm logic lọc (Weak Supervision): Lọc bỏ các dòng có `confidence_score < 9` hoặc AI trả về lỗi.
- [ ] Lưu kết quả những dòng "Chắc chắn đúng" ra file mới: `labeled_clean_data.csv`.

---

## 3. Huấn luyện Mô Hình Custom ML (Model Training)
Mục đích: Từ file dữ liệu đã có nhãn chuẩn `labeled_clean_data.csv`, huấn luyện mô hình Logistic Regression chạy siêu tốc trên máy chủ nội bộ.

- [x] Cài đặt các thư viện: `pip install sentence-transformers scikit-learn pandas numpy`.
- [x] Tạo file `2_train_model.py` trong `app/ai_development/` (Viết theo định dạng `# %%` để chạy như Jupyter Notebook trên VSCode).
- [x] **Feature Extraction (Embedding):** Dùng `sentence-transformers` (model `paraphrase-multilingual-MiniLM-L12-v2`) để biến đoạn text (Bio + Caption) thành Vector số.
- [x] **Nối Vector:** Nối Vector văn bản vừa tạo với các con số như `Followers`, `ER` (chuẩn hóa MinMaxScaler).
- [x] **Training:** Chia tập Train/Test (80/20). Đẩy vào thuật toán `LogisticRegression` của thư viện `scikit-learn`.
- [x] **Đánh giá (Evaluation):** In ra màn hình các chỉ số `Accuracy`, `Classification Report` để đưa vào báo cáo Luận văn.
- [x] **Export Model:** Lưu mô hình đã train ra file `.joblib`.

---

## 4. Tích hợp Mô hình vào Môi trường Production (Online API)
Mục đích: Đem file mô hình `.joblib` vào hệ thống thực tế (`app/ai/analyzer.py`) để thiết lập kiến trúc Hybrid AI (Custom ML + Gemini) chạy mỗi khi Crawler đẩy data về.

- [x] **Cài đặt Dependency:** Cài các thư viện `sentence-transformers`, `scikit-learn`, `joblib` vào môi trường Python chính của hệ thống.
- [x] **Di chuyển Model:** Tạo thư mục `app/ai/models/` và copy 2 file `.joblib` (`creator_category_model.joblib` và `scaler.joblib`) từ `app/ai_development/` sang.
- [x] **Dọn dẹp & Xóa Code Cũ:**
  - Xóa toàn bộ logic của hàm `categorize_creator` cũ trong `app/ai/analyzer.py`. (Hàm cũ này lạm dụng Gemini để phân loại toàn bộ mọi thứ, vừa chậm vừa tốn tiền).
- [x] **Thiết lập Khởi tạo Global:** 
  - Load mô hình ML (`.joblib`) và `sentence-transformers` vào RAM ngay trên đầu file `analyzer.py` (chỉ load đúng 1 lần khi khởi động Server).
- [x] **Viết lại hàm `categorize_creator` (The New Code Flow):**
  - *Luồng dữ liệu (Code Flow):*
    1. Nhận data từ Crawler (Bio, Captions, Followers, ER).
    2. Nối Bio và Captions thành một chuỗi văn bản duy nhất.
    3. Gọi SentenceTransformers biến chữ thành NLP Vector.
    4. Nhồi Vector + Followers vào mô hình `LogisticRegression` để nó "nhả" ra nhãn phân loại chính xác KOC/KOL hay Brand.
    5. Gọi API `gemini-2.5-flash` một lần duy nhất với prompt ngắn để xin 1 câu tóm tắt nội dung kênh (`contentSummary`).
    6. Đóng gói kết quả thành Pydantic object `AIEnrichment` trả về.
- [ ] **Test End-to-End:** Chạy thử file Crawler, để nó cào và tự động đẩy dữ liệu sang file Analyzer mới này, đảm bảo kết quả JSON cuối cùng hoàn hảo.

---

*Ghi chú: Là AI Developer, bạn đã hoàn thành xuất sắc việc "rèn não" (Bước 2, 3) ở Sandbox. Bây giờ hãy bước ra khỏi Sandbox và lắp bộ não đó vào hệ thống thực tế ở Bước 4!*
