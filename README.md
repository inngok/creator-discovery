# InfluencerMatch Crawler API

Hệ thống tự động cào dữ liệu KOC/KOL từ TikTok, tích hợp AI phân tích và tự động gửi dữ liệu về Java Backend.

## 🚀 Cách chạy hệ thống

Chỉ cần mở thư mục dự án và **Click đúp vào file `start_all.bat`**. 
Hệ thống sẽ tự động bật 3 cửa sổ:
1. **API Server** (Nhận lệnh)
2. **Celery Worker** (Đội công nhân cào data song song)
3. **Discovery Bot** (Tự động lướt trang xu hướng TikTok tìm username mới)

## 🌐 Xem giao diện API (UI)

Sau khi chạy hệ thống, mở trình duyệt và truy cập:
👉 **[http://localhost:8000/docs](http://localhost:8000/docs)** 

*(Tại đây bạn có thể nhập 1 username bất kỳ vào API `/api/v1/crawl/tiktok` để cào thử)*

## ⚙️ Cấu hình

Sửa file `.env` nếu cần thiết:
- `GEMINI_API_KEY`: Bắt buộc phải có để phân loại KOC/KOL.
- `JAVA_BACKEND_URL`: Đường link để ném kết quả JSON sang cho team Java.
