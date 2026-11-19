# Chạy crawler tự động trên VPS

Khuyến nghị khởi đầu: Ubuntu, CPU x86_64 4 vCPU, RAM 8 GB, SSD 60–80 GB.
Worker chạy 24/7 với concurrency=1; nhu cầu RAM thực tế cần đo khi cào và tải model.

## Chuẩn bị

Cài Docker Engine và Docker Compose plugin. Bật Docker khởi động cùng VPS:

```bash
sudo systemctl enable --now docker
```

Copy source lên VPS, bao gồm `app/ai/models` chứa model đã huấn luyện.
Nếu cần tiếp tục dữ liệu/session cũ, copy thêm `data` và `app/ai/data` khi crawler cũ đã dừng.
Không commit cookie hoặc `.env` lên Git.

```bash
cp .env.example .env
mkdir -p data app/ai/data app/ai/models
```

Sửa `.env`: điền API key, `JAVA_BACKEND_SECRET` và `JAVA_BACKEND_URL`
trỏ tới endpoint ingest HTTPS của backend đã deploy, không dùng localhost.
Compose tự đặt `REDIS_URL=redis://redis:6379/0` cho container.

## Khởi động

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 -f worker beat
```

Beat phát task theo giờ Việt Nam (Asia/Ho_Chi_Minh):

- 03:00 hằng ngày: tìm tối đa 100 creator mới.
- 04:00 hằng ngày: làm mới tối đa 100 creator cũ.
- 02:00 Chủ nhật: chạy pipeline huấn luyện lại AI.

Lịch nằm trong `app/workers/celery_app.py`. Thay lịch cần build/deploy lại.
Chỉ chạy **một Beat** cho cùng hàng đợi, kể cả khi tăng số worker.
Task được xử lý tuần tự; giờ lên lịch là giờ đưa vào hàng đợi, không đảm bảo giờ hoàn tất.
Beat không tự bù các lần chạy bị bỏ lỡ khi VPS tắt.
Không khởi động thêm các script cào vòng lặp nếu không chủ đích chạy song song với lịch này.

API chỉ mở tại `127.0.0.1:8000` trên VPS. Có thể kiểm tra qua SSH tunnel:

```bash
ssh -L 8000:127.0.0.1:8000 user@VPS_IP
```

Sau đó mở http://localhost:8000/docs. Nếu backend cần gọi API crawler,
cấu hình reverse proxy HTTPS và xác thực/giới hạn truy cập trước khi mở ra ngoài.
Redis không publish port ra Internet.

## Dữ liệu và vận hành

- `./data`: staging SQLite, kết quả cào, cookie và browser storage state.
- `./app/ai/data`: database AI.
- `./app/ai/models`: model được dùng chung giữa API và worker.
- Docker volumes: Redis AOF, cache Hugging Face, trạng thái Beat.

Các service tự khởi động lại khi tiến trình thoát hoặc VPS reboot nếu Docker đã được bật.
Điều này không phát hiện tiến trình bị treo hoặc đảm bảo task thành công; theo dõi log và hàng đợi.
Redis AOF giúp giữ hàng đợi qua restart, không thay thế backup.
Backup định kỳ các thư mục dữ liệu/model và Docker volumes; dừng dịch vụ khi copy SQLite.
Không chạy `docker compose down -v` nếu muốn giữ volumes.

Cập nhật code:

```bash
docker compose up -d --build
docker compose logs --tail=100 worker beat
docker stats --no-stream
```

Kiểm tra một lượt cào và xác nhận backend nhận dữ liệu trước khi để chạy lịch lâu dài.
IP datacenter có thể gặp captcha hoặc chặn truy cập; VPS khỏe hơn không giải quyết được vấn đề đó.
