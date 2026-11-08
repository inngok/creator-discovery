# InfluencerMatch Crawler API

Hệ thống tự động cào dữ liệu KOC/KOL từ đa nền tảng (**TikTok, Facebook, YouTube**), tích hợp **AI Enrichment** (phân loại Category, Niche, CreatorType, Summary, Topic) và tự động chuẩn hóa gửi dữ liệu sang Java Backend.

---

## 🚀 1. Cách chạy nhanh (Khuyên dùng)

Hệ thống đã được tích hợp sẵn 2 script quản lý:

### Khởi động hệ thống (`start.bat`)
- **Chế độ chạy ngầm (Mặc định):**
  Double click vào `start.bat` (hoặc mở CMD gõ `start.bat`).
  Hệ thống sẽ tự khởi động ngầm:
  1. **FastAPI Server** (Port `8000`)
  2. **Celery Worker** (Xử lý hàng đợi cào & phân tích AI)
  3. **Discovery Bot** (Tự động lướt tìm Creator TikTok mới)

- **Chế độ hiện cửa sổ console riêng (Visible mode - để debug & xem log trực tiếp):**
  Mở CMD hoặc PowerShell tại thư mục dự án và gõ:
  ```cmd
  start.bat visible
  ```

### Tắt hệ thống (`stop.bat`)
- Double click vào `stop.bat` (hoặc mở CMD gõ `stop.bat`).
- Script sẽ tự động dọn dẹp các tiến trình chạy ngầm của Uvicorn, Celery và giải phóng Port `8000`.

---

## 🛠️ 2. Cách chạy thủ công từng dịch vụ (Dành cho Developer)

Nếu muốn tự chạy từng service riêng biệt để kiểm tra log:

### Bước 0: Bật Redis
```powershell
docker start influencermatch_redis
# Hoặc khởi động redis server cài trên máy
```

### Bước 1: Khởi động FastAPI Server
```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Bước 2: Khởi động Celery Worker
```powershell
.\venv\Scripts\celery.exe -A app.workers.celery_app worker --loglevel=info --pool=threads --concurrency=1
```

### Bước 3: Khởi động Discovery Bot (Tùy chọn)
```powershell
.\venv\Scripts\python.exe -m app.scripts.discovery_bot
```

---

## 🌐 3. Xem giao diện API & Test

Sau khi khởi động server, truy cập Swagger UI trên trình duyệt:
👉 **[http://localhost:8000/docs](http://localhost:8000/docs)**

Các endpoint chính:
- `GET /api/v1/crawl/instagram?username={username}`: Cào trực tiếp profile Instagram (Playwright Stealth, trả về raw data).
- `POST /api/v1/crawl/instagram/enqueue?username={username}`: Đưa vào hàng đợi Celery cào ngầm + AI phân tích riêng + gửi sang Java backend.
- `GET /api/v1/crawl/tiktok?username={username}`: Cào dữ liệu TikTok trực tiếp.
- `POST /api/v1/crawl/tiktok/enqueue?username={username}`: Đưa vào hàng đợi Celery cào ngầm + AI.
- `GET /api/v1/crawl/facebook?username={username}`: Cào fanpage/profile Facebook.
- `POST /api/v1/crawl/facebook/enqueue?username={username}`: Đưa vào hàng đợi Celery cào ngầm Facebook + AI.
- `GET /api/v1/crawl/youtube?channel_id={id}`: Cào kênh YouTube qua API.
- `POST /api/v1/ai/enrich`: Phân tích AI độc lập cho Creator (Category, Niche, Type, Sentiment).
- `POST /api/v1/ai/embed`: Trích xuất vector 768 chiều (BKAI Bi-Encoder) phục vụ Semantic Search.

---

## ⚙️ 4. Cấu hình môi trường (`.env`)

Cấu hình các khóa trong file `.env`:
```env
# AI Services
GEMINI_API_KEY=your_gemini_api_key

# Java Backend
JAVA_BACKEND_URL=http://localhost:8080/api/v1/creators/ingest

# Redis Queue
REDIS_URL=redis://localhost:6379/0
```

---

## 🤖 5. Công Cụ & Script Hỗ Trợ Tự Động (CLI Tools)

### 5.1. Worker Cào Bài Viết Tự Động (`crawl_recent_posts_worker.py`)
Worker chạy nền liên tục rà soát các Creator chưa đủ số lượng bài viết hoặc chưa có tương tác chi tiết:
- **Playwright Stealth**: Giả lập trình duyệt ẩn danh, chống phát hiện bot.
- **Tự ngắt an toàn khi bị chặn / Captcha**: Khi phát hiện trang xác minh Captcha hoặc bị TikTok chặn tạm thời, script lập tức dừng tiến trình để bảo vệ IP và tài nguyên máy chủ.
- **Tự động làm sạch tài khoản riêng tư (Private Account)**: Tự động xóa khỏi database những Creator chuyển sang chế độ riêng tư (không cào được bài viết công khai).
- **Phân loại lại thông minh (Smart KOC / KOL)**: Tự động sửa nhãn định vị kênh dựa trên followers và tương tác thực tế.

```powershell
# Chạy worker cào bài viết (mặc định delay an toàn 8-15s giữa các request):
.\venv\Scripts\python.exe -m app.scripts.crawl_recent_posts_worker
```

### 5.2. Công cụ Import Creator từ Excel/CSV (`import_and_crawl_excel.py`)
Hỗ trợ nạp hàng nghìn Creator từ file Excel danh sách ngoài:
- **Lọc trùng lặp thông minh**: Tự động đối chiếu với danh sách cũ để tránh cào trùng hoặc đẩy lặp Creator.
- **2 Chế độ hoạt động linh hoạt**:
  - `direct`: Đẩy thẳng thông tin có sẵn vào Deploy Backend & SQLite AI Training DB.
  - `crawl`: Bơm username vào Celery Preflight Queue để bot tự động vào trang live cào bài viết và số liệu mới nhất.

```powershell
# Ví dụ nạp dữ liệu từ file Excel:
.\venv\Scripts\python.exe app\scripts\import_and_crawl_excel.py "data/danh_sach.xlsx" --mode direct
```

---

## 📊 6. Thang Đo Độ Tin Cậy Dữ Liệu (`dataConfidence`)

Mỗi Creator đẩy sang Backend đều kèm chỉ số `dataConfidence` $\in [0.00, 1.00]$ được tính toán động từ 4 trục tiêu chí:
$$\text{Data Confidence} = (35\% \times \text{Nguồn}) + (30\% \times \text{Độ tươi}) + (20\% \times \text{Độ đầy đủ}) + (15\% \times \text{Hợp lý})$$

- **Nguồn dữ liệu (35%)**: API chính thức ($1.0$) > Xác minh/Claimed ($0.85$) > Cào Web public/Excel ($0.70$) > AI suy luận ($0.40$).
- **Độ tươi (30%)**: $\le 7$ ngày ($1.0$) | $7 \div 30$ ngày ($0.5 \div 1.0$) | $> 30$ ngày ($0.2$ - STALE, kích cào lại).
- **Độ đầy đủ (20%)**: Tỷ lệ các trường không bị NULL/rỗng (Avatar, Bio, Followers, Avg Views, Avg Likes, Category/Niche, Contact).
- **Tính hợp lý (15%)**: Chỉ số bình thường ($1.0$). Phát hiện chỉ số ảo/dị thường (ER ảo, Views lệch vô lý, 0 bài đăng...) trừ $0.25$/lỗi.

👉 *Xem chi tiết toàn bộ công thức toán học và giải thích thuật toán tại:* **[docs/AI_EXPLANATION_GUIDE.md](docs/AI_EXPLANATION_GUIDE.md)**.

