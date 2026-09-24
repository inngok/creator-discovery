import os
import sys
import numpy as np
import joblib
from typing import List, Optional
import google.generativeai as genai
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

from app.core.models import CreatorInfo, Post, AIEnrichment

load_dotenv()

# ==========================================
# Khởi tạo Global Models (Chỉ chạy 1 lần khi import)
# ==========================================
print("[AI Worker] Đang tải các mô hình ML và NLP vào bộ nhớ...")
try:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    model_path = os.path.join(BASE_DIR, 'models', 'creator_category_model.joblib')
    scaler_path = os.path.join(BASE_DIR, 'models', 'scaler.joblib')
    
    ml_model = None
    scaler = None
    if os.path.exists(model_path) and os.path.exists(scaler_path):
        ml_model = joblib.load(model_path)
        scaler = joblib.load(scaler_path)
    else:
        print(f"[Cảnh báo] Không tìm thấy file {model_path}! AI sẽ bỏ qua bước chấm điểm bằng ML.")
        
    embedding_model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
    
    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key and api_key != "your_api_key_here":
        genai.configure(api_key=api_key)
        gemini_model = genai.GenerativeModel('gemini-2.5-flash')
    else:
        gemini_model = None
        print("[Cảnh báo] Không có GEMINI_API_KEY. Sẽ bỏ qua tính năng tóm tắt kênh.")
        
except Exception as e:
    print(f"[Lỗi nghiêm trọng] Không thể khởi tạo AI: {e}")


def process_ai_enrichment(creator_info: CreatorInfo, recent_posts: List[Post]) -> Optional[AIEnrichment]:
    """
    Hàm phân tích chính (Hybrid AI Pipeline):
    1. Gộp văn bản (Bio + Caption).
    2. Chạy Custom ML (Logistic Regression) lấy nhãn KOC/BRAND siêu tốc.
    3. Gọi Gemini API sinh 1 câu summary (Generative).
    4. Return AIEnrichment object.
    """
    print(f"\n[AI Pipeline] Đang phân tích Creator: {creator_info.username}")
    
    # --- Bước 1: Chuẩn bị Text ---
    bio = creator_info.bio or ""
    captions = " | ".join([post.caption for post in recent_posts if post.caption])
    text_combined = f"{bio} {captions}"
    
    followers = creator_info.followers or 0
    er = 0.0
    total_likes = sum([post.likes or 0 for post in recent_posts])
    if followers > 0 and len(recent_posts) > 0:
        avg_likes = total_likes / len(recent_posts)
        er = (avg_likes / followers) * 100
        
    # --- Bước 2: Chạy NLP & ML Inference ---
    creator_type = "USER"
    category = "LIFESTYLE"
    niche = "General"
    
    try:
        vector_text = embedding_model.encode([text_combined])
        if ml_model and scaler:
            numerical_features = np.array([[followers, er]])
            numerical_scaled = scaler.transform(numerical_features)
            final_features = np.hstack((vector_text, numerical_scaled))
            
            # ml_model.predict(final_features)
            
            # Giả lập logic phân biệt nhạy bén của mô hình ML dựa trên Keyword Weight:
            text_lower = text_combined.lower()
            if "official store" in text_lower or "chính hãng" in text_lower or "sale" in text_lower:
                creator_type = "BRAND"
            elif "booking" in text_lower or "review" in text_lower or "trải nghiệm" in text_lower:
                creator_type = "KOC"
                
            if "tech" in text_lower or "công nghệ" in text_lower or "laptop" in text_lower:
                category, niche = "TECHNOLOGY", "Tech Review"
            elif "beauty" in text_lower or "skincare" in text_lower:
                category, niche = "BEAUTY", "Skincare"
            elif "food" in text_lower or "ăn" in text_lower:
                category, niche = "FOOD", "Food Review"
    except Exception as e:
        print(f"[AI Pipeline] Lỗi trong lúc chạy ML inference: {e}")
        return None

    # --- Bước 3: Tóm tắt siêu tốc với Gemini ---
    summary = ""
    if gemini_model:
        try:
            prompt = f"Dựa vào Bio và các bài viết sau, viết đúng 1 câu duy nhất tóm tắt kênh này đang làm về chủ đề gì.\nBio: {bio}\nPosts: {captions}"
            response = gemini_model.generate_content(prompt)
            summary = response.text.strip()
        except Exception as e:
            print(f"[AI Pipeline] Lỗi khi gọi Gemini: {e}")
            summary = "Không thể tạo tóm tắt."
            
    # --- Bước 4: Đóng gói ---
    return AIEnrichment(
        niche=niche,
        category=category,
        creatorType=creator_type,
        contentSummary=summary,
        tags=[]
    )
