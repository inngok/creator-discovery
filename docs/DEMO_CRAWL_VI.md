# Cào thủ công và tự động: TikTok, Facebook, Instagram

## Truy cập TikTok và chế độ công khai

TikTok dùng lại session do người vận hành đã lưu trong `data/playwright_state.json` (hoặc cookie export cũ `data/tiktok_cookies.json`). Đây là khôi phục hành vi trước bản chuyển toàn bộ crawler sang public-only. Cả tìm kiếm, preflight và đọc profile dùng cùng bộ nạp session. Cookie hết hạn và dữ liệu thuộc domain khác được loại trong bộ nhớ; crawler không ghi đè file session.

Có thể đặt `TIKTOK_SESSION_PATH` đến file Playwright state riêng của server. Đặt `TIKTOK_USE_SAVED_SESSION=false` để ép TikTok chạy công khai. Nếu file thiếu, hỏng hoặc không còn cookie hợp lệ, crawler dùng context công khai. Session có mặt không bảo đảm nguồn cho truy cập; `/api/v1/crawl/connections` trả `mode`, `uses_saved_session` và `remotely_verified=false`. Cần gọi `/collect` để kiểm tra live riêng.

Facebook và Instagram tiếp tục dùng context công khai, không nạp session lưu trên đĩa. Session/cookie không được commit lên Git. Nếu server thiếu session TikTok, người vận hành phải thiết lập session của mình trên server; deploy code không tự sao chép session từ máy phát triển.

## Bấm cào theo từ khóa hoặc username

Facebook và Instagram chỉ đưa tài khoản có bằng chứng thuộc thị trường Việt Nam vào kết quả, cả tìm tự động lẫn cào bằng username và worker đồng bộ. Bằng chứng gồm quốc gia/địa điểm công khai trên hồ sơ hoặc bio tiếng Việt kèm nội dung tiếng Việt ở ít nhất hai bài khác nhau. Đây là nhận diện thị trường nội dung, không phải xác minh quốc tịch. Không suy quốc gia từ tên, username, từ khóa tìm kiếm hoặc mặc định Vietnam của location resolver. Tài khoản ở nước ngoài có thông tin rõ ràng và trường hợp chưa đủ bằng chứng đều bị loại. Vì vậy có thể bỏ sót tài khoản Việt Nam chỉ viết tiếng Anh hoặc không công khai địa điểm.

Không có danh sách tài khoản demo dùng làm kết quả dự phòng: nguồn tìm kiếm phải trả về ứng viên, sau đó hệ thống kiểm tra profile, nội dung, chủ đề và thị trường trước khi tính vào số lượng.

`POST /api/v1/crawl/batch-crawl/jobs` trả task ID ngay; xem kết quả qua URL `status_url` trả về. Cần Redis và Celery worker. Có thể dùng `/batch-crawl` đồng bộ khi debug, nhưng việc cào và AI có thể lâu hơn timeout của giao diện.

Ví dụ body tìm theo từ khóa, không bắt buộc danh mục:

```json
{"platform":"INSTAGRAM","keywords":["review mỹ phẩm"],"max_channels":3}
```

Thay platform bằng `TIKTOK`, `FACEBOOK`, hoặc `ALL`. Có thể thêm `category`.

Ví dụ username/link cụ thể:

```json
{"platform":"FACEBOOK","usernames":["https://www.facebook.com/schannel"],"max_channels":1}
```

TikTok thử tìm kiếm trực tiếp; khi nguồn lỗi sẽ thử chỉ mục tìm kiếm và xác minh profile/bài viết live. Facebook thử hashtag công khai và Pages. Instagram tìm nguồn chỉ mục, sau đó xác minh profile và chủ đề qua bài viết. Nguồn chặn hoặc không đủ bằng chứng có thể khiến không có kết quả; không bù bằng tài khoản sai chủ đề.

Một profile lỗi không làm mất các kết quả khác trong batch. Với `ALL`, lỗi một nền tảng không ngăn thử các nền tảng còn lại.

## Tự động

`start.bat` chạy API, worker, Celery Beat. Lịch hiện tại: discovery cả ba nền tảng lúc 03:00 và refresh tài khoản có sẵn lúc 04:00, giờ Việt Nam. `POST /api/v1/crawl/trigger-weekly-discovery` cho phép kích hoạt ngay cùng luồng:

```json
{"platform":"ALL","keywords":["review mỹ phẩm"],"max_new_kocs":3}
```

`POST /api/v1/crawl/enqueue` nhận platform và username/profile URL, đưa qua preflight trước khi cào sâu. Các điều kiện duyệt KOC/KOL vẫn áp dụng.

## Dữ liệu dự phòng cho demo

`POST /api/v1/crawl/collect` với body sau lấy profile thô, không tự duyệt hoặc đồng bộ Java:

```json
{"platform":"TIKTOK","username":"hannaholala","allow_cached":false}
```

Lần lấy live có bài thành công lưu snapshot cục bộ. Lần sau có thể bật `allow_cached:true` và `max_age_hours:168`. Chỉ khi live lỗi mới trả bản đã lưu còn hạn, ghi `status:cached`, `is_live:false`, giữ nguyên `collectedAt`. Dữ liệu chỉ có profile không có bài được ghi `partial`, không lưu đè snapshot đầy đủ. Chức năng này là API riêng, giao diện hiện có cần gọi endpoint này nếu muốn hiển thị dự phòng.

Trước demo phải chạy thử chính tài khoản/từ khóa định dùng. Test tự động xác minh logic và lỗi hồi quy; không đảm bảo Facebook/Instagram/TikTok luôn cho truy cập từ mạng hiện tại.
