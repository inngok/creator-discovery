# Hướng dẫn xây dựng Python Worker (Crawler & AI) cho Flow 2

Tài liệu này giải thích cách xây dựng repo Python đóng vai trò là một Worker độc lập để cào dữ liệu (Crawling) và xử lý ngữ nghĩa (AI Semantic Enrichment) theo kiến trúc Hybrid AI, sau đó trả kết quả về cho Spring Boot Backend.

---

## 1. Kiến trúc tổng thể (Architecture)

Repo Python được thiết kế như một **Background Worker** hoặc **Microservice**:

*   **Stack khuyên dùng**: `FastAPI` (để nhận request HTTP nhanh) hoặc `Celery` + `Redis` (nếu muốn làm hệ thống Queue chuyên nghiệp).
*   **Input**: Nhận lệnh từ Spring Boot (VD: "Hãy crawl creator `@lehoang` trên nền tảng `TIKTOK`").
*   **Process**:
    1.  Cào thông tin Profile và 3 bài Post gần nhất.
    2.  Chạy bộ lọc cứng (Hard Filter) loại bỏ các kênh quá nhỏ.
    3.  Chạy AI Inference (Custom ML Model) để phân loại và gọi Gemini để lấy câu tóm tắt.
*   **Output**: Đóng gói thành cục JSON theo chuẩn Contract và gọi API `POST /internal/creators/ingest` của Spring Boot để lưu lại.

---

## 2. Chiến lược Cào Dữ Liệu & Bộ Lọc (Crawling & Filtering)

Việc cào dữ liệu Social Media (TikTok, Instagram, YouTube) rất dễ bị chặn, bạn có thể tự code (Playwright) hoặc dùng dịch vụ trung gian (Apify).

### 2.1. Quy trình Crawl
1.  Truy cập trang cá nhân.
2.  Trích xuất **Profile Data**: Tên, Username, Follower count, Avatar URL, Bio (mô tả).
3.  Tìm **3 bài viết mới nhất**: Trích xuất Caption, Hashtag, Số Views, Likes...

### 2.2. Rule Lọc Cứng (System Design Optimization)
Ý tưởng cốt lõi để tối ưu hóa tài nguyên: **Không lôi mô hình AI nặng nề ra chạy cho những dữ liệu rác.**

- Do ở khâu tìm kiếm Username (Discovery) chúng ta chưa biết số lượng Follower của họ là bao nhiêu, nên quá trình lọc sẽ diễn ra **ngay sau khi cào xong Profile**.
- **Logic:** Code kiểm tra `if followers < 7000 -> Drop`. Tự động loại bỏ và kết thúc tiến trình của Creator này.
- **Chỉ những Creator >= 7000 Followers mới được chuyển tiếp (Pass) sang Bước 3 cho AI xử lý.** 

---

## 3. Chiến lược tích hợp AI (Hybrid Semantic Enrichment)

Hệ thống **không lạm dụng Gemini** để phân loại trực tiếp. Thay vào đó, nó sử dụng kiến trúc lai: Một mô hình Machine Learning nhà làm (đã train sẵn lưu thành file `.joblib`) để phân loại, và chỉ dùng Gemini để tóm tắt văn bản.

### 3.1. Làm sao AI phân biệt được KOC, KOL và Brand?
Nếu chỉ nhìn vào View hay Follower, ta không thể biết ai là KOC thực thụ, ai là nhãn hàng (Brand) tự seeding, ai là người dùng bình thường. AI giải quyết bài toán này thông qua **Tín hiệu Ngôn ngữ (NLP)**:

1. **Dấu hiệu từ Bio (Tiểu sử) - Tín hiệu mạnh nhất:**
   - **KOC/KOL:** Thường có cụm từ *"Booking/Work via Email/Zalo"*, *"Review chân thực"*, *"Link mua đồ ở đây"* (Kèm link Bio/Shopee Affiliate).
   - **Brand (Nhãn hàng):** Chứa từ khóa *"Official Store"*, *"Chính hãng"*, *"Hotline đặt hàng"*, *"Tuyển sỉ/Đại lý"*.
   - **Người dùng bình thường:** Bio thường trống, hoặc trích dẫn câu châm ngôn, tuyệt đối không để thông tin liên hệ công việc.

2. **Dấu hiệu từ Caption (Nội dung bài viết):**
   - **KOC/KOL:** Văn phong mang tính **trải nghiệm cá nhân** (*"Mình vừa săn được..."*, *"Hôm nay test thử..."*, *"Cảm nhận của mình sau 1 tuần..."*).
   - **Brand:** Văn phong mang tính **khuyến mãi/bán hàng** (*"Deal hời duy nhất hôm nay"*, *"Freeship toàn quốc"*, *"Nhập mã giảm 50%"*).

3. **Dấu hiệu từ Hashtags:**
   - **KOC/KOL:** Thường xài `#review`, `#unboxing`, `#haul`, `#tiktokmademebuyit`.
   - **Brand:** Thường xài `#sale`, `#khuyenmai`, `#chinhhang` kèm theo tên thương hiệu.

### 3.2. Cơ chế hoạt động của mô hình Logistic Regression
Ở các bước chuẩn bị Data (Offline), ta đã dùng Gemini dán nhãn cho hàng ngàn mẫu, sau đó dạy cho mô hình Logistic Regression thông qua thư viện `sentence-transformers`.
Mô hình đã tự ngầm hiểu quy luật: Hễ Vector chữ nào có hàm lượng từ *"Booking"*, *"Review"* cao thì xác suất là `KOC` lên đến 99%. Hễ có từ *"Official Store"* thì xác suất là `BRAND` 99%.

=> **Trong thực tế (Online Pipeline):** Worker chỉ cần ném dữ liệu vào mô hình `.joblib`. Nó sẽ liếc mắt đọc trường `bio` và `recentPosts.caption` rồi chốt ngay nhãn `Category`, `Niche`, `CreatorType` trong vài mili-giây! Cuối cùng, API Gemini chỉ được gọi để hỏi 1 câu tóm tắt nội dung kênh (`contentSummary`).

---

## 4. Contract giao tiếp (Gửi data về Spring Boot)

Sau khi có Data cào được và Data AI sinh ra, Python sẽ gộp lại thành một cục JSON duy nhất và bắn HTTP POST về Spring Boot để lưu thẳng xuống Database M2 (`CreatorPublicSnapshot`).

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
    "contentSummary": "Chuyên review thiết bị công nghệ",
    "tags": []
  },
  "source": "APIFY_TIKTOK",
  "collectedAt": "2026-09-19T10:00:00Z"
}
```

## 5. Tóm tắt thứ tự làm việc cho Python Developer
1. Dựng project Python (`FastAPI` + `Pydantic`).
2. Tích hợp công cụ cào data (Khuyên dùng `Apify API` hoặc `Playwright`).
3. Khởi tạo và nạp file mô hình `.joblib` đã huấn luyện vào bộ nhớ (RAM).
4. Viết logic Code: 
   - Nhận Username -> Cào Data.
   - **Filter:** `if followers < 7000 -> Out`.
   - **AI Inference:** Đưa phần còn lại qua NLP Vector + Logistic Regression để lấy nhãn. Gọi Gemini lấy Summary.
   - Đóng gói gửi HTTP POST về máy chủ Java.
5. Xử lý lỗi network và cơ chế Retry tự động.
