# Hướng Dẫn Bắt Đầu Dành Cho Người Mới Học AI (Beginner)

Xin chào! Thư mục `app/ai_development/` này được tạo ra như một "sân chơi" (sandbox) hoàn toàn tách biệt. Mã nguồn ở đây **không đụng chạm hay phá hỏng** bất kỳ phần nào của hệ thống Backend, API hay Crawler hiện tại. Bạn có thể thoải mái vọc vạch ở đây.

## Thư mục này giải quyết bài toán gì?
Như chúng ta đã bàn, để làm AI xịn, bạn cần có một mô hình riêng (Custom ML) để phân loại Creator siêu nhanh. Nhưng để luyện (Train) mô hình đó, bạn cần hàng trăm dữ liệu đã được **dán nhãn (Labeled)**. 
Ví dụ: Trông thấy chữ "Review kem chống nắng" -> Phải biết nhãn là "SKINCARE".

Thay vì bạn phải tự mở Excel lên đọc 1000 dòng và tự gõ chữ SKINCARE bằng tay (rất mệt), chúng ta sẽ dùng Gemini làm con "robot" đọc và điền Excel hộ bạn. Thuật ngữ gọi là **Auto-Labeling (Gán nhãn tự động)**.

## Các file trong thư mục này:
1. `raw_data_sample.csv`: Đây là file **Mock Data**. Hãy tưởng tượng người làm Crawler đã chạy xong và gửi cho bạn file này. Nó chứa dữ liệu thô (tên, người theo dõi, bio, và 3 dòng trạng thái gần nhất).
2. `1_auto_label.py`: Đây là trái tim của thư mục này. Tập lệnh Python này sẽ đọc file CSV thô, gọi điện lên Google Gemini để hỏi nhãn, và ghi kết quả ra một file CSV mới.

## Giải thích chi tiết mã nguồn trong `1_auto_label.py`

### Kỹ thuật Structured Output (Ép kiểu AI)
Ngày xưa, khi gọi AI, ta phải viết một câu Prompt rất dài như: *"Trả lời theo định dạng JSON có dấu ngoặc kép, không được nói thêm từ nào khác nhé"*. Thỉnh thoảng AI vẫn nói thêm *"Chào bạn, đây là kết quả:"* làm sập hệ thống code của chúng ta.

Trong file này, ta dùng class `CreatorLabel(BaseModel)` của thư viện `pydantic`. Đây là kỹ thuật ép kiểu tiên tiến nhất. Ta nói với Gemini: *"Đây là cấu trúc `CreatorLabel`, mi bắt buộc phải trả về đúng cấu trúc này cho ta"*. Và Gemini sẽ nghe lời 100%.

### Kỹ thuật Weak Supervision (Giám sát yếu)
Đôi khi Gemini sẽ phân vân không biết Creator này là thuộc ngách nào. Ta đã thiết kế thêm trường `confidence_score` (Điểm tự tin từ 1-10).
Nếu điểm tự tin >= 9, ta giữ lại làm tài liệu để học. 
Nếu điểm tự tin < 9, dòng code `df_clean = df[df['confidence_score'] >= 9]` sẽ thẳng tay xóa Creator đó. Nhờ vậy, file output cuối cùng (Tập huấn luyện của bạn) sẽ sạch sẽ 100%.

## Cách chạy thử code
1. Mở Terminal (Command Prompt) tại gốc dự án (folder chứa file `.env`).
2. Cài đặt các thư viện cần thiết (nếu chưa có):
   ```bash
   pip install pandas pydantic google-generativeai python-dotenv
   ```
3. Chuyển vào thư mục này:
   ```bash
   cd app/ai_development
   ```
4. Chạy file script:
   ```bash
   python 1_auto_label.py
   ```
5. Sau khi code chạy xong, bạn sẽ thấy một file mới tên là `labeled_clean_data.csv` xuất hiện. Mở file đó ra xem thành quả AI tự dán nhãn nhé!
