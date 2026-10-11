import os
import sys
import json
import numpy as np
import joblib
from typing import List, Optional
import google.generativeai as genai
from dotenv import load_dotenv

# Fix lỗi LiteLLM tải file cost trên Windows
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
import litellm

from app.core.models import CreatorInfo, Post, AIEnrichment, Platform
from app.ai.schemas import PredictionResult
from app.ai.services.embedding_service import embedding_service
from app.ai.config import CATEGORY_UNKNOWN_THRESHOLD

load_dotenv()

# Tải Prototypes từ file JSON (thay cho Hardcode)
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROTOTYPES_PATH = os.path.join(BASE_DIR, 'data', 'prototypes.json')
try:
    with open(PROTOTYPES_PATH, 'r', encoding='utf-8') as f:
        _protos = json.load(f)
        CATEGORY_PROTOTYPES = _protos.get("CATEGORY_PROTOTYPES", {})
        NICHE_PROTOTYPES = _protos.get("NICHE_PROTOTYPES", {})
except Exception as e:
    print(f"[!] Không tìm thấy prototypes.json: {e}")
    CATEGORY_PROTOTYPES = {}
    NICHE_PROTOTYPES = {}
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
        try:
            X_feats = extract_creator_features(creator_info, recent_posts, text_combined)
            
            # Tự động cắt bớt feature nếu Model cũ chỉ train trên 384 (Text) thay vì 391 (Text + Hành vi)
            expected_features = getattr(ct_model, "n_features_in_", 384)
            X_feats_for_model = X_feats[:expected_features]
            
            predicted_label = ct_model.predict(X_feats_for_model.reshape(1, -1))[0]
            return str(predicted_label)
        except Exception as e:
            print(f"[!] Lỗi khi chạy ML CreatorType: {e}")
            return None
    
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

import yake

def _extract_tags(text: str) -> List[str]:
    """Candidate Extraction using YAKE (N-grams)"""
    if not text or len(text.strip()) == 0:
        return []
    
    try:
        # Sử dụng thuật toán YAKE để lấy cụm từ (Phrases) thay vì từng chữ rời rạc
        kw_extractor = yake.KeywordExtractor(lan="vi", n=3, dedupLim=0.9, dedupFunc='seqm', windowsSize=1, top=5)
        keywords = kw_extractor.extract_keywords(text)
        
        # YAKE trả về danh sách các tuple (từ_khóa, điểm_số). Điểm số càng thấp càng tốt.
        # Rút trích chỉ lấy các từ khóa.
        top_tags = [kw[0] for kw in keywords]
        return top_tags
    except Exception as e:
        print(f"Lỗi extract_tags (YAKE): {e}")
        return []

from app.ai.training_db import save_training_data

def process_ai_enrichment(creator_info: CreatorInfo, recent_posts: List[Post]) -> Optional[AIEnrichment]:
    """
    Phan tich AI voi co che Teacher-Student (Gemini day Local ML)
    """
    print(f"\n[AI Pipeline] Dang phan tich ho so Creator: {creator_info.username}")
    
    # 1. Chuan bi Text (Gop Bio + Caption lai thanh 1 doan van)
    bio = creator_info.bio or ""
    captions = " | ".join([post.caption for post in recent_posts if post.caption])
    text_combined = f"{bio} {captions}".strip()
    
    # 2. Local ML du doan truoc
    cat_result = _predict_category(text_combined)
    category = cat_result.label
    niche = _predict_niche(category, text_combined)
    creator_type_ml = _predict_creator_type(creator_info, recent_posts, text_combined)
    creator_type = creator_type_ml if creator_type_ml else "USER"
    tags = _extract_tags(text_combined)
    
    # Khoi tao bien mac dinh
    tag_str = ", ".join(tags) if tags else "nhiều chủ đề đa dạng"
    summary = f"Creator tập trung vào ngách {niche.upper()}, thường xuyên đăng nội dung liên quan tới: {tag_str}."
    suggested_topic = None
    semantic_tags = []
    
    # 3. Gemini Fallback & Auto-Labeling (JSON Prompt)
    should_call_gemini = False
    needs_fallback = False
    
    followers = getattr(creator_info, 'followers', 0)
    
    # Quy tac xac dinh co goi Gemini khong (Tiet kiem API)
    if category == "UNKNOWN" or niche == "UNKNOWN":
        should_call_gemini = True
        needs_fallback = True
    elif creator_type in ["KOL", "KOC", "BRAND"]:
        should_call_gemini = True
    elif creator_type == "USER" and followers >= 15000:
        should_call_gemini = True
        needs_fallback = True  # Nho Gemini kiem tra lai xem co phai KOL/KOC bi ML doan nham khong
        
    if text_combined and should_call_gemini:
        try:
            prompt = f"""
            Dựa trên Bio: '{bio}' và các Caption: '{captions}', và đang sở hữu {creator_info.followers} followers. 
            Hãy phân tích và trả về ĐÚNG định dạng JSON sau (không kèm markdown ```json hay text khác):
            {{
                "summary": "Tóm tắt 1-2 câu về nội dung và giá trị kênh mang lại",
                "tags": ["3-5 từ khóa xu hướng, liền mạch (ví dụ: ẩm thực đồng quê, review xe)"],
                "semanticTags": ["3-5 thẻ ngữ nghĩa sâu xa hơn (ví dụ: gia đình, chữa lành, công nghệ cốt lõi)"],
                "topic": "Một chủ đề gợi ý (ví dụ: Ẩm thực nông thôn)",
                "isSelling": false // (Trả về true nếu nội dung có mùi bán hàng, gắn link affiliate, kêu gọi chốt đơn, PR)
            """
            if needs_fallback:
                prompt += """,
                "categoryFallback": "Một trong 10 Category (BEAUTY, TECH, FOOD, FASHION, LIFESTYLE, AUTOMOTIVE, ENTERTAINMENT, EDUCATION, SPORTS, GAMING)",
                "nicheFallback": "Một ngách phù hợp bằng tiếng Anh viết hoa (ví dụ: VLOG, COMEDY, SKINCARE, v.v...)",
                "creatorTypeFallback": "Chọn 1 trong 4 loại: KOL (người nổi tiếng, chuyên gia, nghệ sĩ, có sức ảnh hưởng, thường >100k followers), KOC (tập trung review sản phẩm, unbox, gắn link affiliate, chốt đơn, mọi mức follower), BRAND (tài khoản chính thức công ty/thương hiệu), USER (người bình thường chia sẻ đời sống cá nhân, không có yếu tố thương mại)"
                """
            prompt += "\n}"
            
            # Xac dinh Gemini API Key (Facebook co key rieng de tranh het quota)
            gemini_key = os.environ.get("GEMINI_API_KEY")
            if getattr(creator_info, 'platform', None) == Platform.FACEBOOK and os.environ.get("GEMINI_API_KEY_FACEBOOK"):
                gemini_key = os.environ.get("GEMINI_API_KEY_FACEBOOK")

            # --- DYNAMIC MULTI-PROVIDER CASCADE (LITELLM) ---
            keys_status = {
                "GEMINI": bool(gemini_key),
                "GROQ": bool(os.environ.get("GROQ_API_KEY")),
                "OPENAI": bool(os.environ.get("OPENAI_API_KEY")),
            }
            
            fallback_models = []
            if keys_status["GEMINI"]:
                try:
                    import google.generativeai as genai
                    genai.configure(api_key=gemini_key)
                    # Uu tien cac model Flash the he moi nhat ho tro Text va con Quota
                    priority_models = [
                        "gemini/gemini-3.8-flash",
                        "gemini/gemini-3.5-flash",
                        "gemini/gemini-3-flash-preview",
                        "gemini/gemini-flash-latest",
                        "gemini/gemini-flash-lite-latest",
                        "gemini/gemini-1.5-flash",
                        "gemini/gemini-2.0-flash",
                    ]
                    for pm in priority_models:
                        if pm not in fallback_models:
                            fallback_models.append(pm)

                    for m in genai.list_models():
                        if 'generateContent' in m.supported_generation_methods:
                            lite_name = m.name.replace("models/", "gemini/")
                            # Loại trừ các model TTS, Audio, Embedding, Image
                            if any(x in lite_name.lower() for x in ['tts', 'audio', 'embedding', 'image', 'imagen', 'aqa']):
                                continue
                            if lite_name not in fallback_models:
                                fallback_models.append(lite_name)
                except Exception as e:
                    fallback_models.extend(["gemini/gemini-3.8-flash", "gemini/gemini-1.5-flash", "gemini/gemini-pro"])
            
            if keys_status["GROQ"]: fallback_models.append("groq/llama-3.3-70b-versatile")
            if keys_status["OPENAI"]: fallback_models.append("openai/gpt-4o-mini")
            
            response_text = None
            if fallback_models:
                try:
                    response = litellm.completion(
                        model=fallback_models[0],
                        messages=[{"role": "user", "content": prompt}],
                        fallbacks=fallback_models[1:] if len(fallback_models) > 1 else [],
                        api_key=gemini_key
                    )
                    
                    if response and response.choices:
                        response_text = response.choices[0].message.content
                except Exception as cascade_e:
                    print(f"[-] AI cascade error: {cascade_e}")
            
            if response_text:
                clean_text = response_text.strip()
                if clean_text.startswith("```json"): clean_text = clean_text[7:]
                elif clean_text.startswith("```"): clean_text = clean_text[3:]
                if clean_text.endswith("```"): clean_text = clean_text[:-3]
                
                ai_data = json.loads(clean_text.strip())
                
                is_selling = ai_data.get("isSelling", False)
                summary = ai_data.get("summary", summary)
                if is_selling:
                    summary += " (Phát hiện: KOC có dấu hiệu thương mại, thường xuyên chèo kéo bán hàng hoặc review PR sản phẩm)."
                
                if ai_data.get("tags"): tags = ai_data["tags"]
                if ai_data.get("semanticTags"): semantic_tags = ai_data["semanticTags"]
                suggested_topic = ai_data.get("topic", None)
                
                # Update category & niche neu can va LUU vao DB
                if needs_fallback:
                    fb_cat = ai_data.get("categoryFallback")
                    fb_niche = ai_data.get("nicheFallback")
                    fb_ctype = ai_data.get("creatorTypeFallback")
                    
                    if fb_cat and fb_cat in CATEGORY_PROTOTYPES:
                        category = fb_cat
                        if fb_niche:
                            niche = fb_niche
                    
                    if fb_ctype in ["KOL", "KOC", "BRAND", "USER"]:
                        creator_type = fb_ctype
                        
                    # LUƯ DATABASE DE TRAIN LOCAL ML SAU NAY
                    save_training_data(
                        username=creator_info.username,
                        platform=creator_info.platform,
                        combined_text=text_combined,
                        category=category,
                        niche=niche,
                        creator_type=creator_type,
                        followers=creator_info.followers,
                        source="GEMINI_AUTO_LABEL"
                    )
                        
        except Exception as e:
            print(f"[Canh bao] Khong the goi Gemini hoac loi JSON: {e}")
            
    try:
        print(f"[Ket qua ML] Label: {cat_result.label} | Confidence: {cat_result.confidence:.2f} | Engine: {cat_result.engine}")
        print(f" -> Niche: {niche} | Type: {creator_type}")
    except Exception:
        pass
    
    return AIEnrichment(
        niche=niche,
        category=category,
        creatorType=creator_type,
        contentSummary=summary,
        tags=tags,
        suggestedTopic=suggested_topic,
        semanticTags=semantic_tags
    )
