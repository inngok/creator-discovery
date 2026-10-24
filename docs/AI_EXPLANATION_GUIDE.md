# TÀI LIỆU CÔNG THỨC & CƠ CHẾ TÍNH TOÁN TOÀN HỆ THỐNG
*(Creator Discovery & AI Recommendation Explanation)*

Tài liệu này tổng hợp toàn bộ các công thức toán học, chỉ số định lượng, thuật toán chấm điểm và cơ chế xử lý AI hiện đang hoạt động trong hệ thống.

---

## MỤC LỤC
1. [Chỉ Số Cào Dữ Liệu & Tương Tác Cơ Bản (Crawler Metrics)](#1-chỉ-số-cào-dữ-liệu--tương-tác-cơ-bản)
2. [Bộ 4 Tiêu Chí AI Explanation (Score Breakdown)](#2-bộ-4-tiêu-chí-ai-explanation-score-breakdown)
   - 2.1. Điểm Tương Tác (`engagementScore`)
   - 2.2. Điểm Quy Mô Phân Khúc (`followerScore`)
   - 2.3. Điểm Tương Đồng Ngữ Nghĩa (`semanticMatchScore`)
   - 2.4. Điểm Khớp Lĩnh Vực / Danh Mục (`nicheMatchScore`)
3. [Công Thức Độ Tin Cậy Dữ Liệu (`dataConfidence`)](#3-công-thức-độ-tin-cậy-dữ-liệu-dataconfidence)
4. [Mô Hình Phân Loại AI & Ngưỡng Ensemble (AI Classification)](#4-mô-hình-phân-loại-ai--ngưỡng-ensemble)
5. [Cơ Chế Sinh Câu Giải Thích Nghiệp Vụ (Natural Language Generation)](#5-cơ-chế-sinh-câu-giải-thích-nghiệp-vụ)
6. [Đặc Tả API Request & Response Chi Tiết](#6-đặc-tả-api-request--response-chi-tiết)

---

## 1. Chỉ Số Cào Dữ Liệu & Tương Tác Cơ Bản

Dữ liệu được trích xuất trực tiếp từ các bài đăng gần nhất ($N$ bài đăng, thông thường $N = 3 \div 10$) của Creator trên TikTok, Facebook hoặc YouTube:

### 1.1. Các chỉ số trung bình (Averages)
$$\text{avgViews} = \frac{1}{N} \sum_{i=1}^{N} \text{views}_i$$

$$\text{avgLikes} = \frac{1}{N} \sum_{i=1}^{N} \text{likes}_i$$

$$\text{avgComments} = \frac{1}{N} \sum_{i=1}^{N} \text{comments}_i$$

$$\text{avgShares} = \frac{1}{N} \sum_{i=1}^{N} \text{shares}_i$$

### 1.2. Tỷ lệ tương tác tổng thể (Engagement Rate - ER)
Công thức tính tỷ lệ phần trăm tương tác dựa trên quy mô người theo dõi:

$$\text{Engagement Rate (ER)} = \frac{\text{avgLikes} + \text{avgComments} + \text{avgShares}}{\text{Followers}} \times 100\%$$

*(Nếu $\text{Followers} \le 0$, $\text{ER} = 0.0\%$)*.

---

## 2. Bộ 4 Tiêu Chí AI Explanation (Score Breakdown)

Được chuẩn hóa trên thang điểm $[0, 100]$, phục vụ trực tiếp cho Flow 3 (Recommendation Engine) và hiển thị bảng phân tích cho Brand.

```
                    ┌── engagementScore (Chất lượng tương tác vs Benchmark Tier)
                    ├── followerScore   (Quy mô phù hợp mục tiêu chiến dịch)
Score Breakdown ────┼── semanticMatchScore (Độ tương đồng Cosine Vector với Brief)
                    └── nicheMatchScore (Độ trùng khớp danh mục / ngành hàng)
```

---

### 2.1. Điểm Tương Tác (`engagementScore`)
- **Bản chất**: Đo lường chất lượng tương tác của Creator so với **mặt bằng chuẩn (Benchmark) của cùng phân khúc quy mô (Tier)**. Tránh việc đánh giá bất công giữa kênh lớn (ER tự nhiên thấp) và kênh nhỏ (ER tự nhiên cao).
- **Bảng Benchmark theo Tier** (Chuẩn ngành Influencer Marketing):

| Phân khúc (Tier) | Khoảng Followers | ER Benchmark chuẩn |
| :--- | :--- | :--- |
| **Nano Influencer** | $< 10,000$ | **$4.5\%$** |
| **Micro Influencer** | $10,000 \le \text{followers} < 100,000$ | **$3.0\%$** |
| **Macro Influencer** | $100,000 \le \text{followers} < 1,000,000$ | **$2.0\%$** |
| **Mega Influencer / KOL** | $\ge 1,000,000$ | **$1.2\%$** |

- **Công thức tính điểm**:
$$\text{engagementScore} = \min\left(100.0, \frac{\text{ER thực tế}}{\text{ER Benchmark}} \times 100\right)$$

- **Quy tắc giải thích**:
  - $\text{ER} \ge \text{Benchmark}$: *"Tỷ lệ tương tác {ER}% vượt trội so với mặt bằng chung của các kênh cùng quy mô."* (100 điểm).
  - $\text{Score} \ge 70$: *"Tỷ lệ tương tác {ER}% duy trì ổn định, bám sát mức trung bình của các kênh cùng quy mô."*
  - $\text{Score} < 70$: *"Tỷ lệ tương tác {ER}% ở mức vừa phải so với các kênh cùng quy mô."*

---

### 2.2. Điểm Quy Mô Phân Khúc (`followerScore`)
- **Bản chất**: Đánh giá độ tối ưu của phân khúc quy mô người theo dõi đối với mục tiêu chiến dịch.
- **Thang điểm cơ sở theo phân khúc**:
  - **Micro ($10\text{k} - 100\text{k}$)**: **95 điểm** (Phân khúc vàng cho các chiến dịch KOC với ROI chuyển đổi cao nhất).
  - **Mega ($> 1\text{M}$)**: **90 điểm** (Độ phủ thương hiệu cực lớn).
  - **Macro ($100\text{k} - 1\text{M}$)**: **85 điểm** (Tầm ảnh hưởng mạnh, độ phủ rộng).
  - **Nano ($< 10\text{k}$)**: **75 điểm** (Hạt giống, chi phí tối thiểu).
- **Thưởng khớp mục tiêu (Target Match)**:
  - Nếu trong `campaignBrief` có đề cập trực tiếp đến phân khúc của Creator (hoặc brief có từ khóa *"KOC"* và Creator $< 100,000$ followers) $\rightarrow$ Thưởng tối đa **$100.0$ điểm**.
- **Công thức mở rộng khi có yêu cầu cụ thể (Target / Min - Max)**:
  - Khi có `targetFollowers`:
    $$\text{followerScore} = \max\left(0.0, 100 - \frac{|\text{Followers} - \text{Target}|}{\text{Target}} \times 100\right)$$
  - Khi có khoảng $[\text{Min}, \text{Max}]$:
    $$\text{followerScore} = \begin{cases} 
    100.0 & \text{nếu } \text{Min} \le \text{Followers} \le \text{Max} \\
    \max\left(0.0, 100 - \frac{\text{Min} - \text{Followers}}{\text{Min}} \times 100\right) & \text{nếu } \text{Followers} < \text{Min} \\
    \max\left(0.0, 100 - \frac{\text{Followers} - \text{Max}}{\text{Max}} \times 100\right) & \text{nếu } \text{Followers} > \text{Max}
    \end{cases}$$

---

### 2.3. Điểm Tương Đồng Ngữ Nghĩa (`semanticMatchScore`)
- **Bản chất**: Đo lường độ ăn khớp về nội dung giữa hồ sơ Creator (Bio, Captions, Hashtags) và bản mô tả chiến dịch (`campaignBrief`).
- **Mô hình Vector**: `bkai-foundation-models/vietnamese-bi-encoder` (Vector không gian 768 chiều).
- **Công thức Cosine Similarity**:
$$\text{CosineSimilarity}(\vec{u}, \vec{v}) = \frac{\vec{u} \cdot \vec{v}}{\|\vec{u}\|_2 \times \|\vec{v}\|_2} = \frac{\sum_{i=1}^{768} u_i v_i}{\sqrt{\sum_{i=1}^{768} u_i^2} \sqrt{\sum_{i=1}^{768} v_i^2}}$$

- **Quy đổi ra thang điểm 100**:
$$\text{semanticMatchScore} = \text{similarityScore} \times 100$$
*(Ví dụ: Cosine Similarity $= 0.82 \rightarrow \text{semanticMatchScore} = 82.0$)*.

---

### 2.4. Điểm Khớp Lĩnh Vực / Danh Mục (`nicheMatchScore`)
- **Bản chất**: Đánh giá độ trùng khớp danh mục ngành hàng giữa Creator và Campaign Brief thông qua so khớp từ khóa và độ tương đồng ngữ nghĩa động (Semantic Vector Similarity).
- **Cơ chế tính điểm hoàn toàn động (Không dùng bảng từ điển gán cứng)**:

| Mức độ phù hợp | Điều kiện nhận diện | Điểm số |
| :--- | :--- | :---: |
| **Trùng khớp trực tiếp Ngách (Exact Niche)** | Tên `niche` của Creator xuất hiện trực tiếp trong `campaignBrief` | **$100.0$** |
| **Trùng khớp qua Hashtag (Tag Match)** | Có hashtag trong `topTags` của Creator xuất hiện trong brief | **$90.0$** |
| **Trùng khớp Danh mục (Category Match)** | Tên `category` của Creator xuất hiện trong brief | **$85.0$** |
| **Ngành hàng liên quan (Semantic Match)** | Điểm tương đồng Vector Search `similarityScore` $\ge 0.65$ | **$\text{similarityScore} \times 100$** |
| **Ít liên quan (Partial Semantic Match)** | Điểm tương đồng Vector Search $0.40 \le \text{similarityScore} < 0.65$ | **$\text{similarityScore} \times 100$** |
| **Khác biệt ngành (Out-of-niche)** | Lệch chủ đề hoàn toàn ($\text{similarityScore} < 0.40$) | **$\max(20.0, \text{similarityScore} \times 100)$** |
| *Trường hợp không có Brief* | Dựa vào mức độ rõ nét của định vị chuyên môn của Creator | **$60.0 \div 85.0$** |

---

## 3. Công Thức Độ Tin Cậy Dữ Liệu (`dataConfidence`)

Trường `dataConfidence` (trong `java_mapper.py` và `import_and_crawl_excel.py`) phản ánh độ tin cậy và chất lượng thực tế của hồ sơ Creator thông qua **4 trục tiêu chí định lượng (Trọng số 100%)**:

$$\text{Data Confidence} = (35\% \times \text{Nguồn}) + (30\% \times \text{Độ tươi}) + (20\% \times \text{Độ đầy đủ}) + (15\% \times \text{Hợp lý})$$

### 4 Thành phần quy đổi định lượng ($0.0 \div 1.0$):

1. **Nguồn dữ liệu ($\text{Nguồn} - 35\%$)**:
   - **$1.00$**: API chính thức của nền tảng (`OFFICIAL_API`, `TIKTOK_API`, `INSTAGRAM_GRAPH_API`, `YOUTUBE_DATA_API`).
   - **$0.85$**: Creator tự xác minh tài khoản (`AUTHORIZED`, `CLAIMED`, `OAUTH`).
   - **$0.70$**: Cào Web Public tự động (`PUBLIC`, `WEB_CRAWL`, `PLAYWRIGHT_STEALTH`, `EXCEL_IMPORT`).
   - **$0.40$**: AI suy luận / Fallback / Chưa xác định.

2. **Độ tươi dữ liệu ($\text{Độ tươi} - 30\%$)**:
   - **$1.00$**: Dữ liệu mới cập nhật $\le 7$ ngày.
   - **$0.50 \div 1.00$**: Cập nhật từ $7 \div 30$ ngày (nội suy tuyến tính theo số ngày).
   - **$0.20$**: Quá $30$ ngày (`STALE` - kích hoạt cơ chế cào lại để làm mới).

3. **Độ đầy đủ trường thông tin ($\text{Độ đầy đủ} - 20\%$)**:
   - Tỷ lệ các trường quan trọng không bị `NULL` hoặc rỗng (Avatar, Bio, Followers $> 0$, Avg Views $> 0$, Avg Likes $> 0$, Category/Niche cụ thể, Thông tin liên hệ).
   $$\text{Độ đầy đủ} = \frac{\text{Số trường hợp lệ}}{7}$$

4. **Tính hợp lý dữ liệu ($\text{Hợp lý} - 15\%$)**:
   - Khởi tạo **$1.00$**, trừ **$0.25$** cho mỗi lỗi dị thường phát hiện được:
     - Tỷ lệ tương tác ảo ($\text{ER} > 50\%$ hoặc $\text{ER} = 0\%$ khi đã có bài viết).
     - Lượt xem lệch phi logic so với followers ($\text{avgViews} > \text{followers} \times 100$ hoặc $\text{avgViews} = 0$ mà $\text{avgLikes} > 1,000$).
     - $\text{followers} \le 0$.
     - Chưa có bài đăng nào trên kênh ($\text{recentPosts}$ rỗng).
   $$\text{Hợp lý} = \max\left(0.0, 1.0 - \sum \text{penalties}\right)$$

### Phân loại nghiệp vụ theo thang điểm $\text{dataConfidence} \in [0.0, 1.0]$:

| Mức điểm | Đánh giá chất lượng | Hành động nghiệp vụ hệ thống |
| :---: | :--- | :--- |
| **$\ge 0.85$** | Dữ liệu xuất sắc (Đầy đủ tương tác, có liên hệ, số liệu tươi mới) | Tự động duyệt, đưa vào danh sách đề xuất ưu tiên |
| **$0.70 \div 0.84$** | Dữ liệu đạt chuẩn (Cào web public đầy đủ thông số cơ bản) | Tự động duyệt, lưu trữ phục vụ tìm kiếm |
| **$< 0.70$** | Dữ liệu sơ cấp / Thiếu trường / Lỗi dị thường / Quá hạn 30 ngày | Đưa vào hàng đợi **Data Manager Review Queue** để duyệt thủ công hoặc kích cào lại |

---

## 4. Mô Hình Phân Loại AI & Ngưỡng Ensemble

Tại file `app/ai/analyzer.py`, hệ thống kết hợp Machine Learning và Vector Similarity để nhận diện ngành nghề và định dạng Creator:

### 4.1. Phân loại Category (Open-set Ensemble)
1. **Xác suất phân loại**: Vector văn bản $X \rightarrow \text{ML Classifier} \rightarrow \text{Probs}$
2. **Biên độ cách biệt (Margin)**:
   $$\text{Margin} = \text{Top1\_Prob} - \text{Top2\_Prob}$$
3. **Khoảng cách ngữ nghĩa với mẫu chuẩn (Prototype Distance)**:
   $$\text{Sim}_{\text{proto}} = \text{CosineSimilarity}(X, \vec{P}_{\text{category}})$$
4. **Quy tắc gán nhãn `UNKNOWN`**:
   Nếu $\text{Top1\_Prob} < 0.60$ hoặc $\text{Margin} < 0.15$ hoặc $\text{Sim}_{\text{proto}} < 0.65$ $\rightarrow$ Gán nhãn `UNKNOWN` và chuyển sang Gemini fallback tự động.

### 4.2. Phân loại Creator Type
Dựa trên bộ lọc luật (Zero AI Cost):
- **BRAND**: Chứa từ khóa bán sỉ, giá xưởng, official store, nhập hàng...
- **KOC**: Chứa từ khóa review, unboxing, mở hộp, săn sale, link shopee, link bio...
- **KOL**: Followers $\ge 100,000$ và có thông tin liên hệ công việc (Email, SĐT, for work, booking)...
- **USER**: Người dùng thông thường chia sẻ cá nhân.

---

## 5. Cơ Chế Sinh Câu Giải Thích Nghiệp Vụ

Nhằm giúp Brand/Marketer hiểu ngay lý do đề xuất mà không bị rối bởi thuật ngữ kỹ thuật hay các ký hiệu `()`, câu giải thích được sinh theo văn phong tự nhiên:

- **Tương tác**: Thay vì ghi *"nhóm Micro"* $\rightarrow$ *"Tỷ lệ tương tác 2.4% duy trì ổn định, bám sát mức trung bình của các kênh cùng quy mô."*
- **Quy mô**: Thay vì ghi *"phân khúc Micro Influencer"* $\rightarrow$ *"Lượng theo dõi 65,000 người vừa tầm tiếp cận, đúng tệp khán giả mục tiêu của chiến dịch."*
- **Định dạng**: *"Phong cách đánh giá chân thực, gần gũi, giúp tạo dựng lòng tin với người mua."*
- **Tóm tắt (Summary)**: Gói gọn trong **đúng 1 câu** nêu bật 3 yếu tố quyết định: Ngách + Tương tác + Quy mô.

---

## 6. Đặc Tả API Request & Response Chi Tiết

### Endpoint: `POST /api/v1/ai/explain`

#### Request Body (JSON)
```json
{
  "creatorName": "Trang Đào",
  "followers": 65000,
  "engagementRate": 2.4,
  "niche": "skincare",
  "category": "Beauty",
  "creatorType": "KOC",
  "similarityScore": 0.82,
  "campaignBrief": "Chiến dịch ra mắt kem chống nắng mới, tìm KOC chuyên về skincare để review",
  "topTags": ["skincare", "kemchongnang"]
}
```

#### Response Body (JSON)
```json
{
  "status": "success",
  "summary": "Trang Đào là lựa chọn phù hợp nhờ nội dung đúng ngách skincare, tương tác tốt và lượng người theo dõi vừa tầm chiến dịch.",
  "reasons": [
    "Tỷ lệ tương tác 2.4% duy trì ổn định, bám sát mức trung bình của các kênh cùng quy mô.",
    "Lượng theo dõi 65,000 người vừa tầm tiếp cận, đúng tệp khán giả mục tiêu của chiến dịch.",
    "Nội dung các bài đăng gần đây rất ăn khớp với thông điệp của chiến dịch.",
    "Chuyên sâu về lĩnh vực Beauty / skincare, đúng trọng tâm mặt hàng cần quảng bá.",
    "Phong cách đánh giá chân thực, gần gũi, giúp tạo dựng lòng tin với người mua."
  ],
  "scoreBreakdown": {
    "engagementScore": 80.0,
    "followerScore": 100.0,
    "semanticMatchScore": 82.0,
    "nicheMatchScore": 100.0
  }
}
```
