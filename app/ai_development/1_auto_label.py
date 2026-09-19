import os
import sys
import io
import json
import pandas as pd
import google.generativeai as genai
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Fix lỗi in tiếng Việt trên Windows (UnicodeEncodeError)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ==========================================
# PHẦN 1: THIẾT LẬP MÔI TRƯỜNG & AI
# ==========================================
# Load biến môi trường từ file .env (nơi chứa GEMINI_API_KEY)
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env'))

# Cấu hình API Key cho Gemini
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("Lỗi: Không tìm thấy GEMINI_API_KEY trong file .env")
genai.configure(api_key=api_key)

# ==========================================
# PHẦN 2: ĐỊNH NGHĨA CẤU TRÚC JSON MUỐN AI TRẢ VỀ
# ==========================================
# Ở đây ta dùng Pydantic. Đây là cách "ép" Gemini phải trả về đúng định dạng này.
# Nhờ vậy, ta không cần phải ngồi code xử lý chuỗi phức tạp.
class CreatorLabel(BaseModel):
    category: str = Field(description="Danh mục lớn của Creator (vd: BEAUTY, TECHNOLOGY, FOOD, FITNESS)")
    niche: str = Field(description="Ngách nội dung nhỏ hơn (vd: SKINCARE, GADGET_REVIEW, STREET_FOOD, HOME_WORKOUT)")
    creatorType: str = Field(description="Phân loại: KOC (review sản phẩm), KOL (người nổi tiếng), EXPERT (chuyên gia)")
    confidence_score: int = Field(description="Điểm tự tin của AI vào phán đoán này (từ 1 đến 10)")
    reasoning: str = Field(description="Giải thích ngắn gọn 1 câu tại sao lại chọn các nhãn này")

# ==========================================
# PHẦN 3: HÀM GỌI GEMINI GÁN NHÃN
# ==========================================
def get_ai_labels(bio: str, posts: str) -> CreatorLabel:
    """
    Hàm này gửi Bio và Bài viết cho Gemini, yêu cầu phân loại.
    """
    # Khởi tạo model.
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    # Chuẩn bị câu lệnh (Prompt)
    prompt = f"""
    Bạn là một chuyên gia phân tích Influencer. 
    Hãy phân loại Creator sau dựa trên Bio và nội dung bài viết của họ.
    
    Thông tin Creator:
    - Bio: {bio}
    - Các bài viết gần đây: {posts}
    """
    
    try:
        # Gọi Gemini với tính năng `response_schema` để ép kiểu trả về
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                response_schema=CreatorLabel,
                temperature=0  # Nhiệt độ = 0 giúp AI trả lời rập khuôn, logic và bớt sáng tạo linh tinh
            )
        )
        
        # Kết quả trả về là một chuỗi JSON chuẩn. Ta parse nó ra dictionary của Python.
        result_dict = json.loads(response.text)
        # Ép vào class Pydantic để đảm bảo tính toàn vẹn dữ liệu
        return CreatorLabel(**result_dict)
    except Exception as e:
        print(f"Lỗi khi gọi AI: {e}")
        return None

# ==========================================
# PHẦN 4: LUỒNG XỬ LÝ CHÍNH (ĐỌC CSV -> AI -> LƯU CSV MỚI)
# ==========================================
def main():
    input_file = "raw_data_sample.csv"
    output_file = "labeled_clean_data.csv"
    
    print(f"Bắt đầu đọc file {input_file}...")
    df = pd.read_csv(input_file)
    
    # Tạo các cột trống để chuẩn bị điền dữ liệu AI vào
    df['category'] = ""
    df['niche'] = ""
    df['creatorType'] = ""
    df['confidence_score'] = 0
    df['reasoning'] = ""
    
    # Lặp qua từng dòng trong file Excel/CSV
    for index, row in df.iterrows():
        username = row['username']
        print(f"\nĐang phân tích Creator: @{username} ...")
        
        # Gửi dữ liệu cho AI
        ai_result = get_ai_labels(bio=row['bio'], posts=row['recent_posts'])
        
        if ai_result:
            print(f" -> Xong! Category: {ai_result.category}, Điểm: {ai_result.confidence_score}/10")
            print(f" -> Lý do: {ai_result.reasoning}")
            
            # Lưu kết quả vào biến df
            df.at[index, 'category'] = ai_result.category
            df.at[index, 'niche'] = ai_result.niche
            df.at[index, 'creatorType'] = ai_result.creatorType
            df.at[index, 'confidence_score'] = ai_result.confidence_score
            df.at[index, 'reasoning'] = ai_result.reasoning
        else:
            print(" -> AI thất bại ở người này.")
            
    # Lọc lại dữ liệu: CHỈ lấy những dòng mà AI tự tin >= 9 điểm
    print("\nĐang lọc dữ liệu chất lượng cao (Confidence >= 9)...")
    df_clean = df[df['confidence_score'] >= 9]
    
    # Lưu ra file CSV mới
    df_clean.to_csv(output_file, index=False)
    print(f"\nThành công! Đã lưu {len(df_clean)}/{len(df)} mẫu dữ liệu sạch vào file {output_file}.")

if __name__ == "__main__":
    # Đảm bảo bạn đang chạy file này ở đúng thư mục app/ai_development/
    main()
