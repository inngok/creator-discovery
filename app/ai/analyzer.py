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

# Prototype semantic descriptions for each category
CATEGORY_PROTOTYPES = {
    "BEAUTY": "skincare makeup cosmetics hair care fragrance beauty review mỹ phẩm làm đẹp chăm sóc da kem chống nắng nước hoa son môi deal mĩ phẩm",
    "TECH": "technology smartphone laptop gadget software AI electronics review công nghệ điện thoại máy tính phần mềm linh kiện đồ điện tử",
    "FOOD": "food restaurant cooking eating culinary food review ăn uống nấu ăn quán ngon ẩm thực review đồ ăn nhà hàng",
    "FASHION": "clothing apparel fashion style outfit lookbook clothing brand thời trang quần áo phối đồ giày dép túi xách phong cách local brand",
    "LIFESTYLE": "lifestyle daily life vlog personal routine đời sống hàng ngày thói quen gia đình chia sẻ cuộc sống mẹ bỉm",
    "AUTOMOTIVE": "car motorbike electric vehicle automotive review ô tô xe máy xe điện review xe độ xe bảo dưỡng",
    "ENTERTAINMENT": "comedy funny entertainment vlog jokes prank humor hài hước giải trí vlog troll drama hát nhảy chốt đơn săn sale",
    "EDUCATION": "education learning school study knowledge teaching facts giáo dục học tập kiến thức chia sẻ học thuật tiếng anh",
    "SPORTS": "sports football soccer basketball fitness gym workout thể thao bóng đá gym thể hình chạy bộ rèn luyện sức khỏe",
    "GAMING": "gaming gameplay stream gamer esports video games game trò chơi điện tử streamer liên quân tốc chiến pubg"
}

# Prototype semantic descriptions for niches
NICHE_PROTOTYPES = {
    "BEAUTY": {
        "SKINCARE": "skin care sunscreen acne serum moisturizer cleanser skin treatment chăm sóc da trị mụn dưỡng da tẩy trang sữa rửa mặt kem dưỡng",
        "MAKEUP": "makeup cosmetics lipstick foundation mascara eyeshadow trang điểm son môi phấn nền phấn mắt phấn phủ mĩ phẩm",
        "HAIRCARE": "hair care shampoo conditioner hair styling haircut chăm sóc tóc dầu gội dưỡng tóc làm tóc tạo kiểu",
    },
    "TECH": {
        "SMARTPHONE": "smartphone mobile phone iphone android review điện thoại smartphone đánh giá iphone samsung",
        "PC_LAPTOP": "pc laptop computer hardware gaming setup máy tính laptop pc đồ họa cấu hình bàn phím chuột",
        "SOFTWARE": "software apps tools ai programming coding phần mềm ứng dụng lập trình thủ thuật ai công nghệ",
    },
    "FOOD": {
        "RESTAURANT_REVIEW": "restaurant food review eating out dining cafe review quán ăn món ngon quán cafe ẩm thực đường phố nhà hàng",
        "COOKING": "cooking recipe homemade kitchen baking culinary nấu ăn công thức làm bánh bếp núc mâm cơm vào bếp",
    },
    "FASHION": {
        "STREETWEAR": "streetwear local brand street style hoodie sneakers áo thun phong cách đường phố",
        "HIGH_FASHION": "high fashion luxury brands designer clothes thời trang cao cấp hàng hiệu túi xách sang trọng",
        "ACCESSORIES": "jewelry watches bags sunglasses phụ kiện đồng hồ trang sức kính mắt",
    },
    "LIFESTYLE": {
        "VLOG": "daily vlog personal life routine chia sẻ cuộc sống nhật ký hàng ngày",
        "PARENTING": "parenting mom baby family mẹ và bé bỉm sữa chăm con gia đình",
        "TRAVEL": "travel tourism trip check-in du lịch phượt khám phá resort",
    },
    "AUTOMOTIVE": {
        "CARS": "car review auto supercar ô tô siêu xe review xe bốn bánh",
        "MOTORBIKES": "motorbike motorcycle review xe máy mô tô phân khối lớn độ xe",
    },
    "ENTERTAINMENT": {
        "COMEDY": "comedy funny jokes prank troll hài hước giải trí buồn cười",
        "DRAMA": "drama gossip news showbiz hóng hớt tin tức xôn xao",
        "MUSIC_DANCE": "music dance cover hát nhảy vũ đạo âm nhạc",
    },
    "EDUCATION": {
        "LANGUAGE": "english learning study ielts toeic tiếng anh học ngoại ngữ ngữ pháp",
        "ACADEMIC": "academic school university kiến thức toán lý hóa đại học",
        "SKILLS": "soft skills career tips kỹ năng mềm định hướng nghề nghiệp phát triển bản thân",
    },
    "SPORTS": {
        "FITNESS": "fitness gym workout yoga thể hình tập gym giảm cân yoga",
        "FOOTBALL": "football soccer match bóng đá thể thao cúp ngoại hạng",
    },
    "GAMING": {
        "PC_GAMING": "pc game steam lol fps game pc liên minh huyền thoại",
        "MOBILE_GAMING": "mobile game esports liên quân tốc chiến pubg mobile",
    }
}
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

CREATOR_TYPE_MODEL_DIR = os.path.join(BASE_DIR, 'models', 'creator_type')
CT_MODEL_PATH = os.path.join(CREATOR_TYPE_MODEL_DIR, 'creator_type_model.joblib')

ct_model = None

try:
    if os.path.exists(MODEL_PATH) and os.path.exists(CLASSES_PATH):
        cat_model = joblib.load(MODEL_PATH)
        with open(CLASSES_PATH, 'r', encoding='utf-8') as f:
            cat_classes = json.load(f)
        print("[AI Worker] Da tai mo hinh Category (v1.0.0) thanh cong!")
    else:
        print(f"[Canh bao] Khong tim thay {MODEL_PATH}! Vui long chay qua trinh Train va Export.")
        
    if os.path.exists(CT_MODEL_PATH):
        ct_model = joblib.load(CT_MODEL_PATH)
        print("[AI Worker] Da tai mo hinh CreatorType thanh cong!")
    else:
        print(f"[Canh bao] Khong tim thay {CT_MODEL_PATH}! Vui long chay qua trinh Train va Export.")
        
    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key and api_key != "your_api_key_here":
        genai.configure(api_key=api_key)
        gemini_model = genai.GenerativeModel('gemini-3.8-flash')
    else:
        gemini_model = None
        
except Exception as e:
    print(f"[Loi nghiem trong] Khong the khoi tao AI: {e}")

def _predict_category(text: str) -> PredictionResult:
    """
    Open-Set Category Classification Ensemble:
    1. ML Classifier Probability
    2. Margin (Top-1 - Top-2)
    3. Semantic Distance (Cosine Similarity to Category Prototypes)
    """
    if not cat_model:
        return PredictionResult(label="UNKNOWN", confidence=0.0, engine="FALLBACK", needs_review=True)
        
    try:
        # Encode text thanh Vector
        X = embedding_service.get_embedding(text)
        X_array = np.array(X)
        if len(X_array.shape) == 1:
            X_array = X_array.reshape(1, -1)
            
        # Predict xac suat
        probs = cat_model.predict_proba(X_array)[0]
        sorted_idx = np.argsort(probs)[::-1]
        
        top1_idx = sorted_idx[0]
        top2_idx = sorted_idx[1] if len(sorted_idx) > 1 else sorted_idx[0]
        
        top1_prob = probs[top1_idx]
        top2_prob = probs[top2_idx]
        predicted_label = cat_classes[top1_idx]
        
        # 1. Probability margin
        margin = top1_prob - top2_prob
        
        # 2. Semantic distance (Cosine Similarity)
        from numpy.linalg import norm
        predicted_sim = 0.0
        
        proto_text = CATEGORY_PROTOTYPES.get(predicted_label, "")
        if proto_text:
            proto_emb = embedding_service.get_embedding(proto_text)
            proto_emb = np.array(proto_emb).flatten()
            x_flat = X_array.flatten()
            
            denom = (norm(x_flat) * norm(proto_emb))
            if denom > 0:
                predicted_sim = np.dot(x_flat, proto_emb) / denom
                
        # 3. Ensemble Decision (Open-set thresholds)
        ml_confidence_threshold = CATEGORY_UNKNOWN_THRESHOLD # from config, e.g., 0.5
        margin_threshold = 0.05
        semantic_threshold = 0.35
        
        is_unknown = False
        if top1_prob < ml_confidence_threshold:
            is_unknown = True
        elif margin < margin_threshold:
            is_unknown = True
        elif proto_text and predicted_sim < semantic_threshold:
            is_unknown = True
            
        if is_unknown:
            return PredictionResult(
                label="UNKNOWN", 
                confidence=float(top1_prob), 
                engine="ENSEMBLE", 
                needs_review=True
            )
            
        return PredictionResult(
            label=predicted_label, 
            confidence=float(top1_prob), 
            engine="ENSEMBLE", 
            needs_review=False
        )
    except Exception as e:
        print(f"Loi predict category open-set: {e}")
        return PredictionResult(label="UNKNOWN", confidence=0.0, engine="ERROR", needs_review=True)

def extract_creator_features(creator_info: CreatorInfo, recent_posts: List[Post], text_combined: str) -> np.ndarray:
    """Trích xuất Semantic Features (384-d) + Behavioral Features (7-d)"""
    X_text = embedding_service.get_embedding(text_combined)
    X_text_array = np.array(X_text).flatten()
    
    # Behavioral signals
    log_followers = np.log1p(creator_info.followers)
    eng_rates = []
    promo_count = review_count = cta_count = brand_mention_count = 0
    
    promo_kws = ["sponsorship", "pr", "affiliate", "quảng cáo", "tài trợ"]
    review_kws = ["review", "đánh giá", "trên tay", "dùng thử", "trải nghiệm"]
    cta_kws = ["link", "bio", "giỏ hàng", "mua", "order", "inbox"]
    
    for p in recent_posts:
        views = p.views or 1
        eng = (p.likes or 0) + (p.comments or 0) + (p.shares or 0)
        eng_rates.append(eng / views if views > 0 else 0)
        
        cap = (p.caption or "").lower()
        if any(kw in cap for kw in promo_kws): promo_count += 1
        if any(kw in cap for kw in review_kws): review_count += 1
        if any(kw in cap for kw in cta_kws): cta_count += 1
        if "@" in cap or "#" in cap: brand_mention_count += 1
        
    avg_eng = np.mean(eng_rates) if eng_rates else 0.0
    n_posts = len(recent_posts) or 1
    
    numerical_features = np.array([
        log_followers,
        avg_eng,
        promo_count / n_posts,
        review_count / n_posts,
        cta_count / n_posts,
        brand_mention_count / n_posts,
        0.5 # content_diversity (mock score)
    ])
    
    return np.concatenate([X_text_array, numerical_features])

def _predict_creator_type(creator_info: CreatorInfo, recent_posts: List[Post], text_combined: str) -> Optional[str]:
    """
    CreatorType Architecture:
    1. Strong platform metadata / rules -> BRAND
    2. CreatorType ML -> BRAND / KOC / KOL / UNKNOWN
    """
    bio = (creator_info.bio or "").lower()
    
    # 1. High-precision override for BRAND
    brand_keywords = ["official shop", "chính hãng", "customer service", "official corporate", "verified brand"]
    if any(kw in bio for kw in brand_keywords):
        return "BRAND"
        
    # 2. CreatorType ML Classifier
    if ct_model:
        X_feats = extract_creator_features(creator_info, recent_posts, text_combined)
        predicted_label = ct_model.predict(X_feats.reshape(1, -1))[0]
        return str(predicted_label)
    
    return None # Return None de Fallback qua LLM (Gemini) do chua train xong Model

import re
from collections import Counter

def _predict_niche(category: str, text: str) -> str:
    """Semantic Classification for Niche"""
    if category == "UNKNOWN" or category not in NICHE_PROTOTYPES:
        return "UNKNOWN"
        
    X = embedding_service.get_embedding(text)
    x_flat = np.array(X).flatten()
    from numpy.linalg import norm
    x_norm = norm(x_flat)
    if x_norm == 0: return "UNKNOWN"
    
    best_niche = "UNKNOWN"
    best_sim = 0.0
    
    for niche, desc in NICHE_PROTOTYPES[category].items():
        proto_emb = embedding_service.get_embedding(desc)
        proto_flat = np.array(proto_emb).flatten()
        p_norm = norm(proto_flat)
        if p_norm == 0: continue
        
        sim = np.dot(x_flat, proto_flat) / (x_norm * p_norm)
        if sim > best_sim:
            best_sim = sim
            best_niche = niche
            
    if best_sim > 0.35: # Semantic Niche threshold
        return best_niche
    return "UNKNOWN"

def _extract_tags(text: str) -> List[str]:
    """Candidate Extraction + Embedding Reranking"""
    # 1. Candidate extraction (Simple words/phrases extraction)
    words = re.findall(r'\b[a-zA-Zàáãạảăắằẳẵặâấầẩẫậèéẹẻẽêềếểễệđìíĩỉịòóõọỏôốồổỗộơớờởỡợùúũụủưứừửữựỳỵỷỹý_]+\b', text.lower())
    stopwords = {"và", "là", "của", "cho", "để", "có", "trong", "với", "các", "những", "đây", "mình", "bạn", "người", "không", "thì", "mà", "lại", "được"}
    words = [w for w in words if w not in stopwords and len(w) > 2]
    
    if not words: return []
    
    counter = Counter(words)
    candidates = [word for word, count in counter.most_common(20)]
    
    # 2. Embedding Reranking
    X = embedding_service.get_embedding(text)
    x_flat = np.array(X).flatten()
    from numpy.linalg import norm
    x_norm = norm(x_flat)
    if x_norm == 0: return candidates[:3]
    
    scored_candidates = []
    for cand in candidates:
        cand_emb = embedding_service.get_embedding(cand)
        cand_flat = np.array(cand_emb).flatten()
        c_norm = norm(cand_flat)
        if c_norm == 0: continue
        sim = np.dot(x_flat, cand_flat) / (x_norm * c_norm)
        scored_candidates.append((sim, cand))
        
    scored_candidates.sort(reverse=True, key=lambda x: x[0])
    top_tags = [cand for sim, cand in scored_candidates[:5]]
    return top_tags

def process_ai_enrichment(creator_info: CreatorInfo, recent_posts: List[Post]) -> Optional[AIEnrichment]:
    """
    Ham phan tich (API Endpoint): Zero-API Local Pipeline (Khong call LLM)
    """
    print(f"\n[AI Pipeline] Dang phan tich ho so Creator: {creator_info.username}")
    
    # 1. Chuan bi Text (Gop Bio + Caption lai thanh 1 doan van)
    bio = creator_info.bio or ""
    captions = " | ".join([post.caption for post in recent_posts if post.caption])
    text_combined = f"{bio} {captions}"
    
    # 2. Goi Open-Set Ensemble de phan loai Category
    cat_result = _predict_category(text_combined)
    category = cat_result.label
    
    # 3. Goi Local Semantic Niche Classification
    niche = _predict_niche(category, text_combined)
    
    # 4. Goi Local ML CreatorType Pipeline
    creator_type_ml = _predict_creator_type(creator_info, recent_posts, text_combined)
    creator_type = creator_type_ml if creator_type_ml else "USER"
    
    # 5. Extract Tags (Candidate Extraction + Embedding Reranking)
    tags = _extract_tags(text_combined)
    
    # 6. Content Summary
    tag_str = ", ".join(tags) if tags else "nhiều chủ đề đa dạng"
    summary = f"Creator tập trung vào ngách {niche.upper()}, thường xuyên đăng nội dung liên quan tới: {tag_str}."
    
    # Neu co Gemini thi su dung de sinh summary hay hon
    if gemini_model:
        try:
            prompt = (f"Hãy viết một đoạn tóm tắt ngắn gọn (1-2 câu) về nội dung của kênh "
                      f"mạng xã hội này dựa trên bio: '{bio}' và các caption: '{captions}'. "
                      f"Tập trung vào giá trị kênh mang lại.")
            response = gemini_model.generate_content(prompt)
            if response and response.text:
                summary = response.text.strip()
        except Exception as e:
            print(f"[Canh bao] Khong the goi Gemini: {e}")
            
    try:
        print(f"[Ket qua ML] Label: {cat_result.label} | Confidence: {cat_result.confidence:.2f} | Engine: {cat_result.engine}")
        print(f" -> Niche: {niche} | Type: {creator_type}")
    except Exception:
        pass # Bo qua loi in ky tu tieng Viet tren terminal Windows
    
    return AIEnrichment(
        niche=niche,
        category=category,
        creatorType=creator_type,
        contentSummary=summary,
        tags=tags,
        suggestedTopic=None,
        semanticTags=[]
    )
