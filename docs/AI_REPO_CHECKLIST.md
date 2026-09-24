# Thiết kế Kiến trúc AI và Hệ thống Flow 2 (Backend & AI Worker)

Tài liệu này mô tả toàn diện kiến trúc AI tùy chỉnh (Custom ML Model kết hợp Gemini), chiến lược quản lý cơ sở dữ liệu, và luồng giao tiếp thực tế giữa Spring Boot Backend và Python AI Worker cho Flow 2.

---

## 1. Kiến Trúc AI Mới: Hybrid Approach (Custom ML + Gemini)

Thay vì lạm dụng 100% API Gemini (tốn kém, chậm, và thiếu tính nghiên cứu học thuật), hệ thống ứng dụng kiến trúc lai kết hợp sức mạnh của Discriminative AI và Generative AI.

- **Ý nghĩa cốt lõi của AI trong hệ thống:** Đóng vai trò là bộ lọc diễn giải ngữ nghĩa (Semantic Analyzer). AI biến đổi dữ liệu phi cấu trúc (Unstructured Data: Bio, Hashtag, Text) thành dữ liệu có cấu trúc (Structured Data: nhãn `Category`, `CreatorType`). Nếu không có quá trình này, Backend không thể thực hiện tìm kiếm, lọc, hay Recommendation chính xác.
- **Custom ML Model (Transfer Learning):** Sử dụng Pre-trained Embedding Model (chuyển chữ thành Vector) kết hợp với một lớp phân loại nhẹ (Logistic Regression). Ưu điểm: Phân loại nhãn (`Category`, `Niche`, `CreatorType`) với tốc độ mili-giây, giá rẻ và độ ổn định schema tuyệt đối.
- **Gemini AI:** Đóng vai trò trợ lý sinh ngôn ngữ tự nhiên (NLP) để tóm tắt nội dung (`contentSummary`).

### 1.1 Tự động hóa tạo tập dữ liệu (Auto-Labeling Pipeline)
Để tạo ra tập dữ liệu huấn luyện (Training Data) chất lượng mà không tốn sức người (Zero-Manual Effort), hệ thống áp dụng kỹ thuật **LLM-as-a-Judge & Weak Supervision**:
1. Cào hàng loạt ~1000 Creators phổ biến.
2. Đẩy qua `gemini-1.5-pro` để gán nhãn kèm `confidence_score` (1-10).
3. Code tự động loại bỏ các mẫu có độ tự tin thấp (< 9/10). Lấy ~500-700 mẫu "sạch" còn lại làm Ground Truth để huấn luyện mô hình Logistic Regression.

### 1.2 Vòng đời của mô hình AI (Continuous Learning)
AI trong thực tế không chỉ train một lần. Hệ thống sẽ có chu trình huấn luyện lại (Retrain) định kỳ:
- Khi mô hình dự đoán ở thực tế có độ tự tin thấp, hệ thống ghi nhận lại Creator đó.
- Định kỳ (ví dụ mỗi tháng), gom các Creator khó đoán, dùng Gemini gán nhãn lại, bổ sung vào tập dữ liệu cũ.
- Chạy Retrain lại mô hình Logistic Regression siêu tốc (tốn < 1 phút) để tạo ra phiên bản model mới (V2) cập nhật xu hướng mạng xã hội mới nhất.

---

## 2. Quản lý Dữ liệu Thực Tế (Database M2 Strategy)

Sự tách biệt rõ ràng giữa Dữ liệu Huấn Luyện AI (Offline) và Dữ liệu Chạy Thực Tế M2 (Online) giúp hệ thống vận hành không xung đột.

- **Không ảnh hưởng AI:** Tập Training Data là tĩnh. Cho dù Creator có khóa kênh hay ngừng hoạt động sau này, quy luật ngôn ngữ trong Bio/Post của họ vẫn đúng và vẫn có giá trị dạy cho AI.
- **Chiến lược Soft Delete cho M2:** Khi Backend phát hiện Creator ngừng hoạt động, DB **không Hard Delete** (xóa hẳn khỏi ổ cứng) mà chỉ đổi cờ `activityStatus = INACTIVE`.
  - Giúp giữ lại lịch sử các chiến dịch cũ (Campaign History).
  - Tránh bị cào lại từ đầu nếu Creator quay lại mạng xã hội.
  - Tốc độ Query của Brand không đổi nhờ đánh Index (`CREATE INDEX idx_status ON CreatorPublicSnapshot(activityStatus)`). Tìm kiếm lấy list `ACTIVE` sẽ bỏ qua dữ liệu rác trong chớp mắt.
- **Data Retention Policy (Chống phình to DB):** Để dọn dẹp, hệ thống chạy một Cron-job bảo trì (Maintenance Job). Creator nào bị đánh dấu `INACTIVE` quá 6 tháng VÀ chưa từng tham gia Campaign nào sẽ bị Hard Delete vĩnh viễn khỏi DB, giúp tối ưu dung lượng lưu trữ dài hạn.

---

## 3. Ứng dụng Thực Tế: Luồng xử lý Flow 2 (BE & AI)

Dưới đây là kịch bản (Flow) thực tế chạy trên Production khi hệ thống vận hành.

### Bước 1: Kích hoạt (Trigger) từ Spring Boot (BE)
- **Tác nhân:** Chỉ có 2 trường hợp kích hoạt hệ thống cào: (1) **Scheduled Job** chạy định kỳ trên BE, hoặc (2) **Manual** (Admin tự bấm nút Refresh). *(Lưu ý: Khi Brand dùng tính năng Discovery tìm kiếm, BE KHÔNG kích hoạt cào realtime mà chỉ lấy từ CSDL M2 có sẵn. Tính năng cào realtime chỉ chạy để refresh dữ liệu khi đi vào luồng Recommendation cho một Creator cụ thể).*
- **Hành động:** Backend bóc tách ra các Username/Handle cần thu thập mới hoặc làm mới (refresh).
- **Giao tiếp:** BE ném Message vào Queue (RabbitMQ/Redis) hoặc gọi HTTP POST sang AI Worker báo: *"Hãy xử lý Creator @abcbeauty trên nền tảng TikTok"*.

### Bước 2: Thu thập và Lọc Profile (Crawling & Filtering)
- Worker nhận lệnh, kích hoạt `tiktok_crawler` (hoặc youtube) để lấy Profile (Followers, Bio...) và 3 bài viết mới nhất.
- **Rule Lọc Cứng (Hard Filter):** Do lúc tìm Username không biết số Follower, nên sau khi cào xong Profile, code sẽ check ngay: **Nếu Followers < 7000 -> Tự động out (Drop luôn, không chạy AI để đỡ tốn tài nguyên)**.
- Chỉ những Creator >= 7000 Followers mới được đẩy tiếp sang bước AI.

### Bước 3: AI Nhảy Vào Xử Lý (Feature Extraction & Inference)
- AI nhận payload (Bio + Caption 3 posts + Hashtags) của những người >= 7000 Follower.
- Đưa qua mô hình `sentence-transformers` để lấy Vector.
- Đẩy Vector vào mô hình `Logistic Regression` (đã train bằng file `.joblib`) để lấy nhãn `Category`, `Niche`, `CreatorType`.

### Bước 4: AI Trả Kết Quả và Lưu Database
- Gọi Gemini tóm tắt nhanh nội dung kênh (`contentSummary`).
- AI đóng gói tất cả thành chuỗi JSON chuẩn (`IngestPayload`) và gọi HTTP POST đẩy thẳng về Backend Spring Boot.
- Backend nhận JSON và lưu thẳng xuống Database M2 (`CreatorPublicSnapshot`). Hoàn tất luồng xử lý cực kỳ gọn gàng!

---

## 4. Các Action Items Thực Hiện Code

Để đạt được kiến trúc và luồng trên, hệ thống được xây dựng theo 2 Phase:

### Phase 1: Xây dựng Custom AI Model (Offline Sandbox) - [HOÀN THÀNH]
Toàn bộ code nằm trong thư mục `app/ai_development/`:
- [x] **Script Gán nhãn Tự động (`1_auto_label.py`):** Dùng `gemini-2.5-flash` và Pydantic để tự động gán nhãn dữ liệu thô từ `raw_data_sample.csv` (Mock Data) tạo ra `labeled_clean_data.csv`.
- [x] **Script Huấn luyện (`2_train_model.py`):** Dùng `sentence-transformers` chuyển text thành vector và đẩy vào thuật toán `LogisticRegression` của `scikit-learn`. Lưu kết quả ra file `.joblib`.
- [x] **Script Tích hợp Nháp (`3_test_integration.py`):** Viết bản nháp hoàn hảo kết hợp Pydantic + ML inference + Gemini Summary để test phân loại chuẩn KOC/BRAND.

### Phase 2: Lắp Não Vào Môi Trường Production (Online API) - [ĐANG THỰC HIỆN]
- [ ] **Cài đặt thư viện:** Bổ sung `sentence-transformers`, `scikit-learn`, `joblib` vào môi trường Python chính của toàn hệ thống (không phải ảo nữa).
- [ ] **Di chuyển Mô hình:** Chép 2 file `.joblib` từ Sandbox sang thư mục hệ thống (ví dụ `app/ai/models/`).
- [ ] **Viết lại hàm Analyzer (`app/ai/analyzer.py`):** Xóa toàn bộ logic thuần Gemini cũ. Thay bằng kiến trúc Hybrid (Load Model ra RAM -> NLP Vector -> Phân loại ML -> Gọi Gemini tóm tắt -> Trả JSON `AIEnrichment`).
- [ ] **Test với Crawler Thật:** Mở `tiktok_crawler.py` (đã có tính năng lọc `< 7000 follower` ở dòng 86), cho chạy thử và xem toàn bộ Data Flow tự động chảy vào hệ thống AI để nhả JSON chuẩn xác.
