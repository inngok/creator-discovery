import os
import sys
import json
import numpy as np
import joblib
from typing import List, Optional
import google.generativeai as genai
from dotenv import load_dotenv

from app.core.models import CreatorInfo, Post, AIEnrichment
from app.ai.schemas import PredictionResult
from app.ai.services.embedding_service import embedding_service
from app.ai.config import CATEGORY_UNKNOWN_THRESHOLD

load_dotenv()

# ==========================================
# Khoi tao Global Models (Chi chay 1 lan khi import vao Web Server)
# ==========================================
print("[AI Worker] Dang tai cac mo hinh ML (Category) vao bo nho...")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CATEGORY_MODEL_DIR = os.path.join(BASE_DIR, 'models', 'category')
MODEL_PATH = os.path.join(CATEGORY_MODEL_DIR, 'category_model.joblib')
CLASSES_PATH = os.path.join(CATEGORY_MODEL_DIR, 'label_classes.json')

cat_model = None
cat_classes = []

try:
    if os.path.exists(MODEL_PATH) and os.path.exists(CLASSES_PATH):
        cat_model = joblib.load(MODEL_PATH)
        with open(CLASSES_PATH, 'r', encoding='utf-8') as f:
            cat_classes = json.load(f)
        print("[AI Worker] Da tai mo hinh Category (v1.0.0) thanh cong!")
    else:
        print(f"[Canh bao] Khong tim thay {MODEL_PATH}! Vui long chay qua trinh Train va Export.")
        
    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key and api_key != "your_api_key_here":
        genai.configure(api_key=api_key)
        gemini_model = genai.GenerativeModel('gemini-1.5-flash')
    else:
        gemini_model = None
        
except Exception as e:
    print(f"[Loi nghiem trong] Khong the khoi tao AI: {e}")

def _predict_category(text: str) -> PredictionResult:
    """
    Su dung mo hinh ML vua duoc huan luyen de du doan Chu de (Category)
    """
    if not cat_model:
        return PredictionResult(label="UNKNOWN", confidence=0.0, engine="FALLBACK", needs_review=True)
        
    try:
        # Encode text thanh Vector
        X = embedding_service.get_embedding(text)
        
        # Predict xac suat
        probs = cat_model.predict_proba(X)[0]
        max_idx = np.argmax(probs)
        max_prob = probs[max_idx]
        predicted_label = cat_classes[max_idx]
        
        # Kiem tra xem co dat Nguong tin cay khong? (Xu ly du lieu OOD)
        if max_prob < CATEGORY_UNKNOWN_THRESHOLD:
            return PredictionResult(
                label="UNKNOWN", 
                confidence=float(max_prob), 
                engine="ML", 
                needs_review=True
            )
            
        return PredictionResult(
            label=predicted_label, 
            confidence=float(max_prob), 
            engine="ML", 
            needs_review=False
        )
    except Exception as e:
        print(f"Loi predict category: {e}")
        return PredictionResult(label="UNKNOWN", confidence=0.0, engine="ERROR", needs_review=True)

def process_ai_enrichment(creator_info: CreatorInfo, recent_posts: List[Post]) -> Optional[AIEnrichment]:
    """
    Ham phan tich chinh (API Endpoint): Ket hop Machine Learning va LLM (Gemini)
    """
    print(f"\n[AI Pipeline] Dang phan tich ho so Creator: {creator_info.username}")
    
    # 1. Chuan bi Text (Gop Bio + Caption lai thanh 1 doan van)
    bio = creator_info.bio or ""
    captions = " | ".join([post.caption for post in recent_posts if post.caption])
    text_combined = f"{bio} {captions}"
    
    # 2. Goi Bo Nao AI da hoc de phan loai Category chinh xac 100% data thuc
    cat_result = _predict_category(text_combined)
    category = cat_result.label
    
    # (Ghi chu: Voi Niche va Creator Type, do chung ta chua Train mo hinh nen xai tam Rule-based)
    text_lower = text_combined.lower()
    creator_type = "USER"
    if "official store" in text_lower or "chinh hang" in text_lower or "sale" in text_lower:
        creator_type = "BRAND"
    elif "booking" in text_lower or "review" in text_lower:
        creator_type = "KOC"
        
    niche = "General"
    if category == "BEAUTY": niche = "Skincare"
    elif category == "FASHION": niche = "Streetwear"
    elif category == "PARENTING": niche = "Mom & Baby"
    elif category == "HEALTH_FITNESS": niche = "Gym"

    # 3. Tom tat noi dung bang Chatbot Gemini 
    summary = ""
    if gemini_model:
        try:
            prompt = f"Dua vao thong tin sau, viet 1 cau ngan gon tom tat noi dung kenh Tiktok nay.\nBio: {bio}\nPosts: {captions}"
            response = gemini_model.generate_content(prompt)
            summary = response.text.strip()
        except Exception as e:
            print(f"[AI Pipeline] Loi Gemini: {e}")
            summary = "Khong the tom tat."
            
    # 4. Tra ket qua (Xuat form JSON de tra ve cho Backend Java)
    print(f"[Ket qua ML] Label: {cat_result.label} | Confidence: {cat_result.confidence:.2f} | Engine: {cat_result.engine}")
    
    return AIEnrichment(
        niche=niche,
        category=category,
        creatorType=creator_type,
        contentSummary=summary,
        tags=[]
    )
