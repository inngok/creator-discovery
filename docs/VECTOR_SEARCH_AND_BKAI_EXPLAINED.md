# TÌM HIỂU VỀ VECTOR SEARCH & MÔ HÌNH BKAI BI-ENCODER
*(Giải thích cơ chế tìm kiếm ngữ nghĩa trong hệ thống Creator Discovery)*

Tài liệu này giải thích một cách trực quan, dễ hiểu từ bản chất đến cách vận hành thực tế của **Vector Search** và mô hình **BKAI Vietnamese Bi-Encoder (768 chiều)** đang được sử dụng trong hệ thống.

---

## 1. Tìm Kiếm Truyền Thống vs Tìm Kiếm Vector (Vector Search)

### 1.1. Cách tìm kiếm truyền thống (Keyword Match / SQL LIKE)
Trước đây, khi tìm kiếm Creator cho chiến dịch, hệ thống thường dùng so khớp từ khóa:
```sql
SELECT * FROM creators WHERE bio LIKE '%kem chống nắng%'
```
- **Hạn chế lớn**: Máy tính chỉ so sánh từng chữ cái.
- **Ví dụ lỗi thực tế**:
  - Campaign Brief yêu cầu: *"Tìm người review mỹ phẩm dưỡng ẩm cho da mùa đông"*.
  - Creator A viết trong Bio: *"Chuyên gia skincare routine, phục hồi da khô"*.
  - 👉 **Kết quả**: Tìm kiếm truyền thống sẽ **bỏ sót Creator A**, vì chữ *"mỹ phẩm dưỡng ẩm"* không trùng từng chữ với *"skincare routine, phục hồi da khô"*, mặc dù về bản chất 2 người này hoàn toàn cùng một chủ đề!

### 1.2. Tìm kiếm Vector (Vector Search / Semantic Search)
Tìm kiếm Vector không so sánh chữ cái, mà **so sánh ý nghĩa ngữ cảnh (Ngữ nghĩa - Semantics)**:
- Máy tính biến văn bản thành một chuỗi các con số (gọi là **Vector** hay **Tọa độ ngữ nghĩa**).
- Những câu từ có ý nghĩa tương đương nhau sẽ được đặt ở **vị trí gần nhau trong không gian đa chiều**, bất kể chúng dùng từ ngữ khác nhau.

```
       [Thời trang, Outfit, Quần áo]  📍 (Cụm thời trang)
                    ▲
                    │
                    │         📍 "Skincare dưỡng ẩm"
                    │        ╱
                    │       ╱ Khoảng cách gần (Góc nhỏ = Giống nhau)
                    │      ▼
                    │    📍 "Mỹ phẩm chăm sóc da"
   ─────────────────┼────────────────────────►
                    │
                    │
                    ▼
       [Tai nghe, Gaming, Laptop]      📍 (Cụm công nghệ - Ở rất xa)
```

---

## 2. Mô Hình BKAI Bi-Encoder Là Gì?

Trong hệ thống, mô hình AI đang chạy tên là:
`bkai-foundation-models/vietnamese-bi-encoder`

### 2.1. Nguồn gốc
- Được nghiên cứu và huấn luyện bởi **Trung tâm BKAI (Đại học Bách Khoa Hà Nội)**.
- Đây là mô hình AI mã nguồn mở được huấn luyện chuyên sâu cho **ngôn ngữ Tiếng Việt**, hiểu rất tốt:
  - Tiếng Việt có dấu, không dấu.
  - Từ ngữ đời thường, tiếng lóng mạng xã hội (unbox, săn sale, chốt đơn, pass đồ...).
  - Ngữ cảnh đan xen giữa tiếng Anh và tiếng Việt của giới trẻ và Creator Việt Nam.

### 2.2. Bi-Encoder là gì?
"Bi" nghĩa là 2. Kiến trúc Bi-Encoder gồm **2 tháp xử lý độc lập**:
1. **Tháp 1 (Mã hóa Campaign Brief)**: Biến văn bản yêu cầu của nhãn hàng thành 1 Vector.
2. **Tháp 2 (Mã hóa Creator Profile)**: Biến thông tin kênh (Bio, Captions, Chuyên mục) thành 1 Vector.

Vì 2 tháp độc lập, hệ thống có thể:
- Tính sẵn và lưu Vector của hàng triệu Creator vào Database từ trước.
- Khi Brand gõ Campaign Brief mới, chỉ cần mất **0.01 giây** để tính vector cho brief rồi so sánh ngay, tốc độ cực nhanh!

### 2.3. Con số 768 chiều nghĩa là gì?
Khi bạn đưa đoạn văn bản:
`"Tìm KOC review mỹ phẩm skincare dưỡng ẩm"`
vào mô hình BKAI, nó sẽ trả về một mảng gồm **đúng 768 con số thực**:
```json
[0.0241, -0.1582, 0.0894, 0.3120, ..., -0.0415]  // Tổng cộng 768 số
```
- Mỗi chiều (dimension) trong 768 con số này đại diện cho một đặc trưng ngữ nghĩa tiềm ẩn (ví dụ: yếu tố thương mại, chủ đề làm đẹp, giọng điệu vui vẻ, tính công nghệ...).
- 768 con số này đóng vai trò như **"Dấu vân tay kỹ thuật số"** đại diện cho ý nghĩa của đoạn văn bản đó.

---

## 3. Luồng Hoạt Động Thực Tế Trong Hệ Thống

Toàn bộ quy trình từ lúc cào dữ liệu đến khi đề xuất cho Brand diễn ra qua 4 bước:

```
[BƯỚC 1: CÀO DATA]
TikTok/Facebook ──► Cào Bio + 5 Captions ──► BKAI Embedding ──► Lưu Vector 768 chiều vào DB
                                                                           │
[BƯỚC 2: BRAND TẠO BRIEF]                                                  │
Nhãn hàng nhập: "Cần tìm KOC làm đẹp" ──► Gọi /api/v1/ai/embed ──► Vector Brief (768 số)
                                                                           │
[BƯỚC 3: SO KHỚP VECTOR]                                                  │
                                         Cosine Similarity (Vector Brief, Vector Creator)
                                                                           │
                                                                           ▼
[BƯỚC 4: XUẤT KẾT QUẢ]                                    similarityScore (VD: 0.82)
Gọi /api/v1/ai/explain ◄───────────────────────────────────────────────────┘
       │
       ▼
Sinh điểm semanticMatchScore: 82.0 + Câu giải thích tự nhiên
```

### Bước 1: Lúc cào dữ liệu Creator
Trong file `java_mapper.py`:
1. Gom Bio + Tên hiển thị + Chuyên mục + 5 Caption bài đăng gần nhất thành 1 đoạn văn bản đại diện cho kênh Creator.
2. Gọi hàm `embedding_service.get_search_embedding(text)`.
3. Mô hình BKAI sinh ra Vector 768 chiều.
4. Vector này được gửi sang Java Backend để lưu vào cơ sở dữ liệu (PostgreSQL với tiện ích mở rộng `pgvector`).

### Bước 2: Khi Brand tạo Campaign
Brand nhập mô tả chiến dịch vào hệ thống (ví dụ: *"Dự án ra mắt son lì kháng nước"*).
Java Backend gọi sang Python API:
- `POST /api/v1/ai/embed` với body: `{"text": "Dự án ra mắt son lì kháng nước"}`.
- Python dùng BKAI trả về Vector 768 chiều của Campaign Brief.

### Bước 3: So khớp Cosine Similarity
Java Backend (hoặc Database pgvector) tính góc giữa Vector của Brief và Vector của từng Creator theo công thức **Cosine Similarity**:

$$\text{Cosine Similarity} = \frac{\vec{A} \cdot \vec{B}}{\|\vec{A}\| \times \|\vec{B}\|}$$

- **Kết quả trả về một con số từ `0.0` đến `1.0`** (gọi là `similarityScore`):
  - $\approx 1.0$: Hai nội dung gần như trùng khớp hoàn toàn về ngữ nghĩa.
  - $0.7 \div 0.9$: Nội dung rất liên quan (ví dụ: son môi vs mỹ phẩm trang điểm).
  - $0.4 \div 0.6$: Có liên quan một phần (ví dụ: thời trang vs mỹ phẩm).
  - $< 0.3$: Hoàn toàn không liên quan (ví dụ: mỹ phẩm vs linh kiện máy tính).

### Bước 4: AI sinh câu giải thích
Java Backend gửi `similarityScore` này sang API `POST /api/v1/ai/explain` của Python:
- Điểm ngữ nghĩa: $\text{semanticMatchScore} = \text{similarityScore} \times 100$.
- Sinh câu nhận xét tự nhiên: *"Nội dung các bài đăng gần đây rất ăn khớp với thông điệp của chiến dịch."*

---

## 4. Tại Sao Hệ Thống Dùng BKAI Bi-Encoder Thay Vì OpenAI / Gemini?

| Tiêu chí | Dùng BKAI Bi-Encoder (Hiện tại) | Dùng OpenAI / Gemini API |
| :--- | :--- | :--- |
| **Chi phí** | **0 đồng (Miễn phí 100%)** — Chạy trực tiếp trên RAM máy chủ | Tốn tiền trên từng từ ngữ (Token) gửi đi |
| **Tốc độ** | **Siêu nhanh (5 - 15 mili-giây)** vì chạy local | Chậm hơn (500ms - 2000ms) do phải gọi qua internet |
| **Độ nhạy tiếng Việt** | Rất cao (Được đại học Bách Khoa tối ưu riêng cho tiếng Việt) | Tốt nhưng đôi khi bị ảnh hưởng bởi ngữ nghĩa tiếng Anh |
| **Bảo mật & Ổn định** | Không phụ thuộc internet hay giới hạn Rate Limit của bên thứ 3 | Dễ bị nghẽn mạng hoặc lỗi quota API |

---

## 5. Tóm Tắt 1 Câu Đơn Giản
> **Vector Search với BKAI giống như việc AI đọc hiểu nội dung của Creator và yêu cầu của Brand, sau đó dịch cả hai sang một hệ tọa độ toán học (768 chiều). Hai điểm nào nằm càng gần nhau trên bản đồ ngữ nghĩa đó thì Creator đó càng phù hợp với chiến dịch của Brand.**
