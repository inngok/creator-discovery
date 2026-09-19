# Hướng dẫn xây dựng Python Worker (Crawler & AI) cho Flow 2

Tài liệu này giải thích cách xây dựng repo Python đóng vai trò là một Worker độc lập để cào dữ liệu (Crawling) và xử lý ngữ nghĩa (AI Semantic Enrichment), sau đó trả kết quả về cho Spring Boot Backend.

---

## 1. Kiến trúc tổng thể (Architecture)

Repo Python nên được thiết kế như một **Background Worker** hoặc **Microservice**:

*   **Stack khuyên dùng**: `FastAPI` (để nhận request HTTP nhanh) hoặc `Celery` + `Redis` (nếu muốn làm hệ thống Queue chuyên nghiệp).
*   **Input**: Nhận lệnh từ Spring Boot (VD: "Hãy crawl creator `@lehoang` trên nền tảng `TIKTOK`").
*   **Process**:
    1.  Cào thông tin Profile (Factual data).
    2.  Cào 3 bài Post gần nhất.
    3.  Gửi đống chữ (Bio, Captions) cho AI (Gemini) để phân loại.
*   **Output**: Đóng gói thành cục JSON theo chuẩn Contract và gọi API `POST /internal/creators/ingest` của Spring Boot để lưu lại.

---

## 2. Chiến lược cào dữ liệu (Crawling Strategy)

Việc cào dữ liệu Social Media (TikTok, Instagram, YouTube) rất dễ bị chặn (IP Block, Captcha). Bạn nên chọn công cụ phù hợp với MVP (Minimum Viable Product).

### Các lựa chọn thư viện:
1.  **Chính ngạch (Official API)**: YouTube Data API v3 (Rất dễ, nên ưu tiên).
2.  **Dịch vụ trung gian (SaaS)**: Sử dụng [Apify](https://apify.com/) (rất xịn, có sẵn crawler cho TikTok/IG, đỡ phải tự host browser, tốn ít phí nhưng cực kỳ ổn định).
3.  **Tự build (Self-hosted)**:
    *   `Playwright` hoặc `Selenium`: Bật trình duyệt giả lập. (Cấu hình proxy xoay vòng - Rotating Proxies).
    *   `BeautifulSoup` / `Requests` (Khó với TikTok vì họ render bằng JavaScript).

### Quy trình Crawl (Per Creator):
1.  Truy cập trang cá nhân.
2.  Trích xuất **Profile Data**: Tên, Username, Follower count, Avatar URL, Bio (mô tả).
3.  Tìm **3 bài viết mới nhất**: Trích xuất Link bài, Số Views, Số Likes, Số Comments, Caption, Hashtag.
4.  *Tuyệt đối không tính toán Average hay tính Engagement Rate ở đây, cứ trả nguyên số thô về cho Java tính.*

---

## 3. Chiến lược tích hợp AI (Gemini Semantic Enrichment)

Sau khi có dữ liệu thô, ta dùng AI (Gemini) để biến nó thành dữ liệu có cấu trúc (Semantic Data). 
*Lưu ý: AI không được sinh ra con số (không đoán follower/views), chỉ dùng để phân loại (Classification).*

### 3.1. Dữ liệu đầu vào (Prompt Input)
Gom nhóm Bio và 3 caption lại thành text đưa cho AI:
```text
Tôi có một Creator trên TIKTOK.
Bio: "Review đồ công nghệ chân thực"
Post 1: "Trên tay con iPhone 16 Pro Max... #iphone #tech"
Post 2: "Có nên mua laptop gaming năm 2026? #laptop"
Post 3: "Tai nghe giá rẻ dưới 1 triệu #review"
```

### 3.2. Cấu trúc Prompt (Prompt Engineering)
Yêu cầu AI phân tích và trả về dưới định dạng **JSON** với các tiêu chí khắt khe. Trong Python, sử dụng thư viện `pydantic` kết hợp Gemini API Structured Output để ép kiểu.

Yêu cầu AI phân loại các field sau:
*   `niche`: Ngách (VD: "Tech Review", "Skincare", "Food").
*   `category`: Danh mục lớn (VD: "TECHNOLOGY", "LIFESTYLE").
*   `creatorType`: Phân loại (KOL, KOC, BLOGGER, EXPERT).
*   `contentSummary`: Câu tóm tắt 1 dòng về nội dung kênh.

### 3.3. Ví dụ Output từ AI
```json
{
  "niche": "Tech Review",
  "category": "TECHNOLOGY",
  "creatorType": "KOC",
  "contentSummary": "Chuyên review các thiết bị công nghệ, điện thoại và laptop."
}
```

---

## 4. Contract giao tiếp (Gửi data về Spring Boot)

Sau khi có Data cào được (Factual) và Data từ AI (Semantic), Python sẽ gộp lại thành một cục JSON duy nhất và bắn HTTP POST về Spring Boot.

**Endpoint bên Spring Boot:** `POST /internal/creators/ingest`

**Payload JSON mẫu:**
```json
{
  "creator": {
    "platform": "TIKTOK",
    "externalId": "123456789",
    "username": "tech_reviewer",
    "displayName": "Hoàng Tech",
    "profileUrl": "https://tiktok.com/@tech_reviewer",
    "avatarUrl": "https://...",
    "followers": 150000,
    "bio": "Review đồ công nghệ chân thực"
  },
  "recentPosts": [
    {
      "platformPostId": "T987654",
      "postUrl": "https://...",
      "caption": "Trên tay con iPhone 16 Pro Max... #iphone #tech",
      "views": 20000,
      "likes": 1500,
      "comments": 120,
      "shares": 50,
      "postedAt": "2026-09-18T10:00:00Z"
    }
  ],
  "aiEnrichment": {
    "niche": "Tech Review",
    "category": "TECHNOLOGY",
    "creatorType": "KOC",
    "contentSummary": "Chuyên review thiết bị công nghệ"
  },
  "source": "APIFY_TIKTOK",
  "collectedAt": "2026-09-19T10:00:00Z"
}
```

## 5. Tóm tắt thứ tự làm việc cho Python Developer
1. Dựng project Python (`FastAPI` + `Pydantic`).
2. Tích hợp công cụ cào data (Khuyên dùng `Apify API` hoặc `Playwright`).
3. Tích hợp `Google Generative AI SDK` để gọi Gemini.
4. Viết logic: Nhận Username -> Cào Data -> Đẩy Data thô qua Gemini lấy JSON -> Đóng gói gửi HTTP POST về máy chủ Java.
5. Xử lý lỗi (Lỗi network, lỗi AI trả JSON sai) và có cơ chế Retry tự động.
