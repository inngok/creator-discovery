# HƯỚNG DẪN VẬN HÀNH HỆ THỐNG PRE-CRAWL KOC/KOL HARD FILTER
## InfluencerMatch AI – Background Creator Data Collection Runbook

---

## 📌 1. TỔNG QUAN HỆ THỐNG
Hệ thống **Pre-Crawl KOC/KOL Hard Filter** được xây dựng nhằm mục đích sàng lọc chất lượng ứng viên ngay trước khi cấp phát tài nguyên trình duyệt cào sâu (Playwright Deep Crawl), đảm bảo:
- **Tiết kiệm tài nguyên:** Giảm ~55% đến ~80% thời gian cào và RAM bằng cách chặn các shop bán sỉ, catalog thương mại, báo chí, meme reup trước khi khởi động Chromium.
- **Độ chính xác cao:** Đạt 100% Precision trên tập Auto-Accept, không chấp nhận việc đưa Brand/Seller vào danh sách Creator.
- **Bảo toàn danh tính Master Identity:** Đảm bảo toàn bộ dữ liệu cào background gửi sang Java Backend (`SEP_BE`) luôn mang `sourceType = 'PUBLIC'` và `masterId = None`, không xâm phạm luồng Claim/OAuth của Creator.

---

## ⚙️ 2. CẤU HÌNH BIẾN MÔI TRƯỜNG (.env)

Các biến cấu hình chính điều khiển hoạt động của hệ thống nằm trong file `.env` tại thư mục gốc `D:\creator-discovery`:

```bash
# ==========================================
# CẤU HÌNH HÀNG ĐỢI CELERY & REDIS
# ==========================================
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0

# ==========================================
# CƠ SỞ DỮ LIỆU STAGING
# ==========================================
# Mặc định sử dụng SQLite WAL mode tại local:
CANDIDATE_STAGING_DB_PATH=data/candidate_staging.db

# ==========================================
# CẤU HÌNH JAVA INGESTION API (SEP_BE)
# ==========================================
JAVA_BACKEND_URL=http://localhost:8080/api/v1/creators/ingest
JAVA_API_KEY=your_internal_secret_key_here

# ==========================================
# NGƯỠNG PHÂN LOẠI RULE ENGINE (rule_config.py)
# ==========================================
# Ngưỡng số lượng bài preview tối thiểu để đánh giá
MIN_POST_PREVIEWS=5
# Ngưỡng tỷ lệ bài review KOC (3/5 bài = 0.6)
KOC_REVIEW_RATIO_THRESHOLD=0.6
# Số bài có nhận xét ưu/nhược điểm tối thiểu của KOC
KOC_MIN_OPINION_POSTS=2
# Ngưỡng tỷ lệ bài chuyên môn KOL (3/5 bài = 0.6)
KOL_REPRESENTATIVE_RATIO_THRESHOLD=0.6
# Ngưỡng follower tối thiểu cho ứng viên mới
MIN_CANDIDATE_FOLLOWERS=7000
```

---

## 🚀 3. HƯỚNG DẪN KHỞI ĐỘNG HỆ THỐNG

Hệ thống yêu cầu chạy **2 Worker Celery riêng biệt** để đảm bảo tác vụ Deep Crawl nặng không làm nghẽn hàng đợi kiểm tra nhanh Preflight:

### Bước 1: Khởi động Redis
```bash
# Kiểm tra Redis đang hoạt động tại cổng 6379
redis-cli ping
# Kết quả mong đợi: PONG
```

### Bước 2: Khởi động Worker 1 — Preflight Queue (Nhanh & Nhẹ)
```bash
# Windows PowerShell (từ thư mục D:\creator-discovery)
.\venv\Scripts\celery -A app.workers.celery_app worker -Q preflight_queue -c 4 --loglevel=info -n worker_preflight@%h
```
> **Đặc điểm:** Chạy concurrency = 4, xử lý hàng trăm request kiểm tra nhanh (1.5 – 3s / candidate) mà không ngốn tài nguyên.

### Bước 3: Khởi động Worker 2 — Deep Crawl Queue (Nặng)
```bash
# Windows PowerShell (mở cửa sổ terminal thứ 2)
.\venv\Scripts\celery -A app.workers.celery_app worker -Q deep_crawl_queue -c 1 --loglevel=info -n worker_deep_crawl@%h
```
> **Đặc điểm:** Concurrency = 1 (hoặc 2 nếu máy cấu hình cao) để quản lý tải RAM trình duyệt Chromium và tránh bị khóa IP bởi nền tảng mạng xã hội.

### Bước 4: Khởi động FastAPI Server
```bash
.\venv\Scripts\uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 🔍 4. QUY TRÌNH KIỂM CHỨNG & THỬ NGHIỆM

### 4.1. Chạy toàn bộ 22 Test Cases bắt buộc
```bash
.\venv\Scripts\python.exe tests/run_all_mandatory_tests.py
```
*Kỳ vọng: Toàn bộ 22/22 TC01 – TC22 đều hiển thị `✅ PASS` với thời gian thực thi dưới 1 giây.*

### 4.2. Chạy Benchmark đo lường hiệu năng
```bash
.\venv\Scripts\python.exe tests/benchmark_performance.py
```
*Kỳ vọng: Tốc độ Preflight nhanh hơn Deep Crawl ~19 lần, tỷ lệ tiết kiệm tài nguyên $\ge 50\%$.*

### 4.3. Đánh giá độ chính xác Precision & Recall
```bash
.\venv\Scripts\python.exe tests/evaluate_precision_recall.py
```
*Kỳ vọng: Overall Auto-Accept Precision đạt 100% (Mục tiêu tối thiểu: $\ge 95\%$).*

### 4.4. Thử nghiệm một ứng viên cụ thể qua API
Gửi yêu cầu đưa ứng viên vào Preflight Gate:
```bash
curl -X POST "http://localhost:8000/api/v1/crawl/tiktok/enqueue" \
     -H "Content-Type: application/json" \
     -d '{"username": "linh_beauty_review", "force_deep_crawl": false}'
```

---

## 👨‍💼 5. DÀNH CHO DATA MANAGER: QUY TRÌNH DUYỆT ỨNG VIÊN

### 5.1. Xem danh sách ứng viên chờ duyệt (Pending Review)
```bash
curl -X GET "http://localhost:8000/api/v1/candidates/review/pending?limit=20&offset=0"
```
Response mẫu:
```json
{
  "total": 1,
  "limit": 20,
  "offset": 0,
  "candidates": [
    {
      "id": "c1f7b029-...",
      "platform": "TIKTOK",
      "username": "mai_lan_creator_with_shop",
      "display_name": "Mai Lan Review",
      "decision": "NEEDS_MANAGER_REVIEW",
      "reason_codes": ["BR06_SELECTIVE_REVIEW_CREATOR_WITH_SHOP"],
      "matched_signals": ["CREATOR_PERSONA_DETECTED", "KOC_REVIEW_POSTS_3", "HAS_OWN_SHOP_OR_SELLING"],
      "evidence_summary": {
        "followers": 32000,
        "review_post_ratio": 0.6,
        "has_commercial_bio": true
      }
    }
  ]
}
```

### 5.2. Thao tác phê duyệt (APPROVE KOC / KOL)
Khi Data Manager xác nhận ứng viên là Creator chân chính:
```bash
curl -X POST "http://localhost:8000/api/v1/candidates/review/{candidate_id}/action" \
     -H "Content-Type: application/json" \
     -d '{
       "action": "APPROVE_KOC",
       "reviewer": "manager_nguyen_van_a",
       "note": "Xác nhận video review chân thật, shop đồ chỉ là phụ kiện nhỏ"
     }'
```
*Kết quả: Hệ thống tự động chuyển trạng thái `MANAGER_APPROVED` và đẩy 1 task duy nhất vào `deep_crawl_queue`.*

### 5.3. Thao tác từ chối (REJECT)
```bash
curl -X POST "http://localhost:8000/api/v1/candidates/review/{candidate_id}/action" \
     -H "Content-Type: application/json" \
     -d '{
       "action": "REJECT",
       "reviewer": "manager_nguyen_van_a",
       "note": "Kênh reup video, không phải người thật sáng tạo"
     }'
```

---

## 🚨 6. XỬ LÝ SỰ CỐ & ROLLBACK AN TOÀN

### Sự cố 1: Worker báo lỗi `DEEP CRAWL GATE BLOCKED`
- **Nguyên nhân:** Có luồng gọi trực tiếp vào `process_tiktok_profile` mà không qua Preflight hoặc ứng viên chưa được duyệt.
- **Hành động:** Đây là tính năng bảo vệ đang hoạt động chính xác (Guard Blocked). Kiểm tra lại script gọi (`discovery_bot.py` hoặc API client) xem đã chuyển sang gọi `preflight_candidate_task` chưa.

### Sự cố 2: Worker báo lỗi `QUARANTINE_POST_VALIDATION_FAILED`
- **Nguyên nhân:** Sau khi cào sâu 10 bài, phát hiện candidate là shop bán sỉ hàng loạt (catalog) lọt lưới hoặc dữ liệu cào về bị thiếu (`< 3` bài).
- **Hành động:** Dữ liệu tự động bị cách ly và KHÔNG gửi sang Java Backend. Kiểm tra bản ghi trong bảng `candidate_staging` để xem chi tiết lý do.

### Quy trình Rollback khẩn cấp (Nếu cần quay lại chế độ cũ)
Trong trường hợp cần cào khẩn cấp một danh sách mà không muốn qua Preflight Filter:
1. Gọi API enqueue với tham số `"force_deep_crawl": true`:
   ```bash
   curl -X POST "http://localhost:8000/api/v1/crawl/tiktok/enqueue" \
        -H "Content-Type: application/json" \
        -d '{"username": "urgent_creator", "force_deep_crawl": true}'
   ```
2. Cờ `force_deep_crawl=True` sẽ tự động ghi nhãn `MANAGER_APPROVED` vào Staging DB để mở khóa Worker Gate ngay lập tức.
