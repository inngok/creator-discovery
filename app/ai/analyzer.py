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

from app.core.models import CreatorInfo, Post, AIEnrichment
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

def _predict_creator_type(creator_info: CreatorInfo, recent_posts: List[Post], text_combined: str) -> str:
    """
    Rule-based CreatorType Classification (Zero AI Cost):
    BRAND/SELLER -> KOC -> KOL -> UNKNOWN
    """
    full_text = f"{creator_info.username} {creator_info.displayName} {text_combined}".lower()
    followers = getattr(creator_info, 'followers', 0)
    
    # 1. BỘ LỌC ĐỎ (SELLER / BRAND)
    seller_keywords = ["chuyên sỉ", "giá xưởng", "sỉ lẻ", "tuyển ctv", "tuyển đại lý", "official shop", "chính hãng", "nhập hàng"]
    if any(kw in full_text for kw in seller_keywords):
        return "BRAND"
        
    # 2. BỘ LỌC XANH DƯƠNG (KOC)
    koc_keywords = ["review", "unboxing", "mở hộp", "săn sale", "mua ở đây", "link mua"]
    safe_links = ["beacons.ai", "linktr.ee", "shopee.vn", "bio.link", "instabio", "kol.eco", "mycollection.shop"]
    if any(link in full_text for link in safe_links) or any(kw in full_text for kw in koc_keywords):
        return "KOC"
        
    # 3. BỘ LỌC XANH LÁ (KOL)
    kol_keywords = ["booking", "contact", "work", "hợp tác", "for work", "quảng cáo"]
    has_contact = bool(creator_info.publicEmail or creator_info.publicPhone)
    if followers >= 100000 and (has_contact or any(kw in full_text for kw in kol_keywords)):
        return "KOL"
        
    # 4. CHƯA RÕ -> Trả về UNKNOWN (Để Manager duyệt)
    return "UNKNOWN"

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
                "summary": "Viết 1-2 câu TÓM TẮT nội dung kênh (Tuyệt đối KHÔNG copy lại thông tin liên hệ, SĐT, Email, hay giá booking từ Bio). Nếu không đủ dữ liệu, ghi: 'Kênh chia sẻ nội dung đời sống và giải trí.'",
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
            
            # --- DYNAMIC MULTI-PROVIDER CASCADE (LITELLM) ---
            keys_status = {
                "GEMINI": bool(os.environ.get("GEMINI_API_KEY")),
                "GROQ": bool(os.environ.get("GROQ_API_KEY")),
                "OPENAI": bool(os.environ.get("OPENAI_API_KEY")),
            }
            
            fallback_models = []
            if keys_status["GEMINI"]:
                # --- PHUC HOI "BI THUAT" TU DONG QUET 33+ MODELS CUA GEMINI ---
                try:
                    import google.generativeai as genai
                    genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
                    for m in genai.list_models():
                        if 'generateContent' in m.supported_generation_methods:
                            # Chuyen doi ten "models/gemini-pro" thanh chuan cua LiteLLM la "gemini/gemini-pro"
                            lite_name = m.name.replace("models/", "gemini/")
                            if lite_name not in fallback_models:
                                fallback_models.append(lite_name)
                    print(f"[*] Da tu dong nap {len(fallback_models)} models cua Gemini vao Phao cuu sinh!")
                except Exception as e:
                    print(f"[-] Quet Gemini Models that bai, dung tam Hardcode. Loi: {e}")
                    fallback_models.extend(["gemini/gemini-1.5-flash", "gemini/gemini-1.5-pro", "gemini/gemini-pro"])
            
            if keys_status["GROQ"]: fallback_models.append("groq/llama-3.3-70b-versatile")
            if keys_status["OPENAI"]: fallback_models.append("openai/gpt-4o-mini")
            
            response_text = None
            if fallback_models:
                try:
                    print(f"[*] Dang yeu cau LiteLLM dieu phoi AI (Uu tien: {fallback_models[0]})...")
                    response = litellm.completion(
                        model=fallback_models[0],
                        messages=[{"role": "user", "content": prompt}],
                        fallbacks=fallback_models[1:] if len(fallback_models) > 1 else [],
                    )
                    
                    if response and response.choices:
                        response_text = response.choices[0].message.content
                        print(f"[+] Da lay duoc du lieu AI thanh cong (Model thuc te phuc vu: {response.model})!")
                except Exception as cascade_e:
                    print(f"[-] Tat ca cac phao cuu sinh AI deu that bai: {cascade_e}")
            
            if response_text:
                clean_text = response_text.strip()
                if clean_text.startswith("```json"): clean_text = clean_text[7:]
                elif clean_text.startswith("```"): clean_text = clean_text[3:]
                if clean_text.endswith("```"): clean_text = clean_text[:-3]
                
                ai_data = json.loads(clean_text.strip())
                
                is_selling = ai_data.get("isSelling", False)
                summary = ai_data.get("summary", summary)
                
                # CHẶN AI COPY BIO: Kiểm tra xem summary có chứa SĐT, email hay từ khóa rác không
                if summary == bio or "for work" in summary.lower() or "@" in summary or re.search(r'\d{8,}', summary):
                    summary = f"Creator tập trung vào ngách {niche.upper()}, thường xuyên đăng tải nội dung về: {', '.join(tags) if tags else 'đa dạng chủ đề'}."
                
                # if is_selling:
                #     summary += " (Phát hiện: KOC có dấu hiệu thương mại, thường xuyên chèo kéo bán hàng hoặc review PR sản phẩm)."
                
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
                    print(f"[*] Da luu nhan tu dong tu Gemini vao SQLite (Category: {category}, Niche: {niche}, Type: {creator_type})")
                        
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
