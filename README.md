# InfluencerMatch Crawler API

## Normalize existing PostgreSQL data

The existing `/api/v1/quality/batch-reclassify` check now defaults to `mode=NORMALIZE`
and `dry_run=true`. It reads PostgreSQL only and returns a saved before/after plan.
`mode=RECLASSIFY` keeps content/type recommendations, but stored PostgreSQL rows are
never sent back through ingestion or silently replaced with staging SQLite records.
An audit is not a fresh crawl and is not a claim that an old AI classification is correct.

Configure `NORMALIZATION_DATABASE_URL` in the crawler environment, or explicitly set
`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` and `DB_PASSWORD` / `DB_PASS`. No hardcoded
remote database or staging fallback is used. The connection must target the same database
as the backend. Set the same `DB_NORMALIZATION_API_KEY` on backend and crawler; otherwise
the existing shared creator-ingestion service key is used. The frontend uses a Data Manager
JWT against the backend, rather than receiving an internal service key:

- `POST /api/v1/data/crawler/db-normalization/preview`: e.g. `{"limit":100,"platform":"TIKTOK"}`.
- `GET /api/v1/data/crawler/db-normalization/reports/{runId}`: saved evidence and proposed changes.
- `POST /api/v1/data/crawler/db-normalization/apply`: `{"planId":"normalize-..."}`.
- `POST /api/v1/data/crawler/db-normalization/reclassify`: e.g. `{"limit":5}`;
  rechecks actual captions and returns category proposals with at least two verbatim post excerpts.
- `GET /api/v1/data/crawler/db-normalization/content-report`: latest content audit and its evidence.

Content reclassification can call the configured AI service; normalization does not. Old database
labels are not supplied as evidence. Proposals require supported, consistent category/niche values,
confidence of at least 0.85 and two different stored posts with verifiable excerpts. Unavailable AI
or insufficient evidence is reported explicitly. These proposals require human review and are not
automatically written by normalization apply. The backend proxy always forces this mode read-only.

For private-data reclassification, run Ollama locally and configure `LOCAL_LLM_MODEL` (for example,
`qwen3:4b`) and optionally `OLLAMA_API_BASE` (default `http://127.0.0.1:11434`). When
`LOCAL_LLM_MODEL` is set, enrichment uses only that Ollama model; failures do not fall back to
Gemini, Groq, or OpenAI. Leave `LOCAL_LLM_MODEL` unset to retain the existing cloud-provider
cascade. Category proposals remain evidence-checked and require human review.

For an operator running directly from this crawler checkout:

```powershell
python scripts/normalize_existing_db.py preview --all
python scripts/normalize_existing_db.py apply --plan-id normalize-REPLACE_WITH_PREVIEW_ID
```

To reuse an existing backend environment file without copying credentials or changing either
project's environment files, select it explicitly and supply its database host if omitted:

```powershell
python scripts/normalize_existing_db.py --env-file 'C:\InfluencerMatch\backend\.env' --host localhost preview --all
```

Use the same environment/host options for `apply`. Database URLs and separate `--host` overrides
cannot be mixed. V19 databases without `creator.discovery_hidden` can be inspected without
adding columns; if that schema or any protected evidence changes after preview, apply conflicts.

Plans live under `data/db_normalization/`, expire after one hour and include the database
target and contract hash. Pagination uses `nextAfterId` / `--after-id`; UUID order remains
stable when category labels change. API replicas must share this report directory.

Normalization derives category/niche codes from observed labels, recomputes ER from stored
average interaction counts and followers, and replaces generated summaries with excerpts
from the newest three stored posts. Missing counts do not become zero and missing evidence
does not become KOC, Lifestyle or a new observation. Reviewed category/niche/type decisions
are preserved. Old FRESH observations can become STALE and require a real recrawl.
Stale embeddings are cleared when their content/classification changes; lexical retrieval
remains available until a real crawl regenerates vectors. No crawling, LLM or embedding call
is made by normalization.

Apply locks creator then latest metric, rechecks the complete before-state and post evidence,
and writes an `audit_log` entry with the before/after data in the same transaction. It never
updates collectedAt, observedAt, posts, ownership, visibility, fees or profile identity.
Repeated plans return ALREADY_APPLIED; concurrent recrawls/reviews return a conflict.
Each creator commits independently, so inspect `appliedCount`, `conflictCount` and `applyResults`
for partial runs. Reports flag cases needing content review; normalization statistics are not
AI accuracy scores.

## Shared creator discovery contract

`app/contracts/creator-discovery-v1.json` is generated from the backend-owned contract.
Category aliases, category search seeds, the 5,000 follower admission floor, three-post
snapshot and embedding dimensions use this artifact. AI output and JavaMapper normalize
observed labels identically to backend ingestion and discovery; crawl configuration is not
classification evidence. Metrics and embedding evidence use the same newest three posts.
JavaMapper sends ER as a ratio with explicit contract version and unit. Existing profiles
may refresh below the admission floor; discovery excludes those profiles. Successful duplicate
run acknowledgements are accepted as synchronized, rather than reported as failures.

Sync/check from this crawler checkout:

```powershell
python scripts/sync_discovery_contract.py --backend-root 'C:\InfluencerMatch\backend'
python scripts/sync_discovery_contract.py --backend-root 'C:\InfluencerMatch\backend' --check
```

The backend `docs/creator-discovery.md` describes deployment order and fixture export.
Backend documentation generators also live here: `scripts/backend_docs/generate_diagrams.py`
and `scripts/backend_docs/generate_states.py`; both require `--backend-root` and optionally
accept `--output-dir`. The backend repository contains Java, SQL, JSON and documentation artifacts.
Focused checks: `python -m unittest tests.services.test_discovery_contract tests.services.test_creator_activity_evidence tests.crawlers.test_managed_crawl_categories`.

## Creator activity evidence

Post dates are normalized to UTC from ISO timestamps or Unix seconds/milliseconds.
Missing, relative, invalid, or future publication dates remain unknown; crawl time is
never substituted for publication time. Facebook reads explicit article timestamps
when available. Java ingestion orders dated posts newest first and retains unknown
date evidence within the backend's three-post snapshot to prevent false inactivity
warnings from pinned posts. Observation timestamps sent to Java are UTC.

The existing daily refresh job still runs at 04:00 Asia/Ho_Chi_Minh. Its database
selection uses each account's latest metric, including hidden accounts, and only
queues supported platforms. Neither crawling nor refreshing changes manager visibility.
Data Manager can also recrawl one account from the frontend activity panel.

Focused verification: `python -m unittest tests.test_creator_activity_evidence tests.test_post_validation`.

CI/CD (GitHub Actions, VPS systemd): [docs/CI_CD.md](docs/CI_CD.md).

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

