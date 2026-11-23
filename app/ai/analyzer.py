import json
import os

import numpy as np
from dotenv import load_dotenv

# Fix lỗi LiteLLM tải file cost trên Windows
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
import litellm

from app.ai.config import (
    AUTO_LABEL_MIN_FOLLOWERS,
    CATEGORY_UNKNOWN_THRESHOLD,
    MARGIN_THRESHOLD,
    NICHE_SEMANTIC_THRESHOLD,
    SEMANTIC_THRESHOLD,
)
from app.ai.schemas import PredictionResult
from app.ai.grounded_enrichment import build_content_summary, has_label_evidence, resolve_grounded_fallback_labels
from app.ai.services.embedding_service import embedding_service
from app.core.models import AIEnrichment, CreatorInfo, Post
from app.core.logger import setup_logger

logger = setup_logger("ai_analyzer")
load_dotenv()

# Tải Prototypes từ file JSON (thay cho Hardcode)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROTOTYPES_PATH = os.path.join(BASE_DIR, "data", "prototypes.json")
try:
    if os.path.exists(PROTOTYPES_PATH):
        with open(PROTOTYPES_PATH, "r", encoding="utf-8") as f:
            _protos = json.load(f)
            CATEGORY_PROTOTYPES = _protos.get("CATEGORY_PROTOTYPES", {})
            NICHE_PROTOTYPES = _protos.get("NICHE_PROTOTYPES", {})
    else:
        CATEGORY_PROTOTYPES = {}
        NICHE_PROTOTYPES = {}
except Exception as e:
    logger.warning(f"[!] Không tìm thấy prototypes.json: {e}")
    CATEGORY_PROTOTYPES = {}
    NICHE_PROTOTYPES = {}
# ==========================================
# Khoi tao Global Models thong qua Singleton
# ==========================================
from app.ai.model_manager import model_manager

cat_model = model_manager.cat_model
cat_classes = model_manager.cat_classes
ct_model = model_manager.ct_model


def _predict_category(text: str) -> PredictionResult:
    """
    Open-Set Category Classification Ensemble:
    1. ML Classifier Probability
    2. Margin (Top-1 - Top-2)
    3. Semantic Distance (Cosine Similarity to Category Prototypes)
    """
    if not cat_model:
        return PredictionResult(
            label="UNKNOWN", confidence=0.0, engine="FALLBACK", needs_review=True
        )

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

            denom = norm(x_flat) * norm(proto_emb)
            if denom > 0:
                predicted_sim = np.dot(x_flat, proto_emb) / denom

        # 3. Ensemble Decision (Open-set thresholds)
        is_unknown = False
        if top1_prob < CATEGORY_UNKNOWN_THRESHOLD or margin < MARGIN_THRESHOLD or proto_text and predicted_sim < SEMANTIC_THRESHOLD:
            is_unknown = True

        if is_unknown:
            return PredictionResult(
                label="UNKNOWN",
                confidence=float(top1_prob),
                engine="ENSEMBLE",
                needs_review=True,
            )

        return PredictionResult(
            label=predicted_label,
            confidence=float(top1_prob),
            engine="ENSEMBLE",
            needs_review=False,
        )
    except Exception as e:
        logger.info(f"Loi predict category open-set: {e}")
        return PredictionResult(
            label="UNKNOWN", confidence=0.0, engine="ERROR", needs_review=True
        )


def _predict_creator_type(
    creator_info: CreatorInfo, recent_posts: list[Post], text_combined: str
) -> str:
    """
    Rule-based CreatorType Classification (Zero AI Cost):
    BRAND/SELLER -> KOC -> KOL -> UNKNOWN
    """
    full_text = (
        f"{creator_info.username} {creator_info.displayName} {text_combined}".lower()
    )
    followers = getattr(creator_info, "followers", 0)

    # 1. BỘ LỌC ĐỎ (SELLER / BRAND)
    seller_keywords = [
        "chuyên sỉ",
        "giá xưởng",
        "sỉ lẻ",
        "tuyển ctv",
        "tuyển đại lý",
        "official shop",
        "chính hãng",
        "nhập hàng",
        "xưởng may",
        "tổng kho",
        "gov.vn",
        "an ninh mạng",
        "cục ",
        "bộ y tế",
        "truyền hình",
        "vtvcab",
        "vtv thể thao",
        "vtv giải trí",
        "tin tức 24h",
        "thể thao 247",
        "báo ",
        "tòa soạn",
        "công ty tnhh",
        "công ty cổ phần",
        "khu du lịch",
        "đại nam",
    ]
    if any(kw in full_text for kw in seller_keywords):
        return "BRAND"

    # 2. BỘ LỌC XANH DƯƠNG (KOC)
    koc_keywords = ["review", "unboxing", "mở hộp", "săn sale", "mua ở đây", "link mua"]
    safe_links = [
        "beacons.ai",
        "linktr.ee",
        "shopee.vn",
        "bio.link",
        "instabio",
        "kol.eco",
        "mycollection.shop",
    ]
    if any(link in full_text for link in safe_links) or any(
        kw in full_text for kw in koc_keywords
    ):
        return "KOC"

    # 3. BỘ LỌC XANH LÁ (KOL)
    kol_keywords = ["booking", "contact", "work", "hợp tác", "for work", "quảng cáo"]
    has_contact = bool(creator_info.publicEmail or creator_info.publicPhone)
    if followers >= 100000 and (
        has_contact or any(kw in full_text for kw in kol_keywords)
    ):
        return "KOL"

    # 4. CHƯA RÕ -> Trả về UNKNOWN (Để Manager duyệt)
    return "UNKNOWN"


import re


def _predict_niche(category: str, text: str) -> str:
    """Semantic Classification for Niche"""
    if category == "UNKNOWN" or category not in NICHE_PROTOTYPES:
        return "UNKNOWN"

    X = embedding_service.get_embedding(text)
    x_flat = np.array(X).flatten()
    from numpy.linalg import norm

    x_norm = norm(x_flat)
    if x_norm == 0:
        return "UNKNOWN"

    best_niche = "UNKNOWN"
    best_sim = 0.0

    for niche, desc in NICHE_PROTOTYPES[category].items():
        proto_emb = embedding_service.get_embedding(desc)
        proto_flat = np.array(proto_emb).flatten()
        p_norm = norm(proto_flat)
        if p_norm == 0:
            continue

        sim = np.dot(x_flat, proto_flat) / (x_norm * p_norm)
        if sim > best_sim:
            best_sim = sim
            best_niche = niche

    if best_sim > NICHE_SEMANTIC_THRESHOLD:
        return best_niche
    return "UNKNOWN"


try:
    import yake
except ImportError:
    yake = None


def _extract_tags(text: str) -> list[str]:
    """Candidate Extraction using YAKE (N-grams)"""
    if not text or len(text.strip()) == 0:
        return []

    if not yake:
        # Fallback to simple regex tokens if yake is not installed
        words = [w.strip("#@,.").lower() for w in text.split() if len(w) > 3]
        return list(dict.fromkeys(words))[:5]

    try:
        kw_extractor = yake.KeywordExtractor(
            lan="vi", n=3, dedupLim=0.9, dedupFunc="seqm", windowsSize=1, top=5
        )
        keywords = kw_extractor.extract_keywords(text)
        top_tags = [kw[0] for kw in keywords]
        return top_tags
    except Exception as e:
        logger.info(f"Lỗi extract_tags (YAKE): {e}")
        return []


from app.ai.training_db import save_training_data


def get_llm_enrichment(prompt: str) -> dict | None:
    # --- DYNAMIC MULTI-PROVIDER CASCADE (LITELLM) ---
    keys_status = {
        "GEMINI": bool(os.environ.get("GEMINI_API_KEY")),
        "GROQ": bool(os.environ.get("GROQ_API_KEY")),
        "OPENAI": bool(os.environ.get("OPENAI_API_KEY")),
    }

    fallback_models = []
    if keys_status["GEMINI"]:
        fallback_models.extend(
            [
                "gemini/gemini-3.1-flash-lite",
                "gemini/gemini-3.1-flash-lite-preview",
                "gemini/gemini-3-flash-preview",
                "gemini/gemma-4-26b-a4b-it",
            ]
        )

    if keys_status["GROQ"]:
        fallback_models.append("groq/llama-3.3-70b-versatile")
    if keys_status["OPENAI"]:
        fallback_models.append("openai/gpt-4o-mini")

    response_text = None
    if fallback_models:
        try:
            logger.info(
                f"[*] Dang yeu cau LiteLLM dieu phoi AI (Uu tien: {fallback_models[0]})..."
            )
            response = litellm.completion(
                model=fallback_models[0],
                messages=[{"role": "user", "content": prompt}],
                fallbacks=fallback_models[1:] if len(fallback_models) > 1 else [],
                timeout=30,
                num_retries=0,
            )

            if response and response.choices:
                response_text = response.choices[0].message.content
                logger.info(
                    f"[+] Da lay duoc du lieu AI thanh cong (Model thuc te phuc vu: {response.model})!"
                )
        except Exception as cascade_e:
            logger.info(f"[-] Tat ca cac phao cuu sinh AI deu that bai: {cascade_e}")

    if response_text:
        clean_text = response_text.strip()
        import re

        json_match = re.search(r"\{.*\}", clean_text, re.DOTALL)
        if json_match:
            clean_text = json_match.group(0)
        elif clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        elif clean_text.startswith("```"):
            clean_text = clean_text[3:]
        clean_text = clean_text.removesuffix("```")

        try:
            return json.loads(clean_text.strip())
        except Exception as e:
            logger.info(f"[!] Loi parse JSON tu AI: {e}")
    return None


def _save_auto_label_metrics(
    creator_info: CreatorInfo,
    text_combined: str,
    category: str,
    niche: str,
    creator_type: str,
):
    save_training_data(
        username=creator_info.username,
        platform=creator_info.platform,
        combined_text=text_combined,
        category=category,
        niche=niche,
        creator_type=creator_type,
        followers=creator_info.followers,
        source="GEMINI_AUTO_LABEL",
    )
    logger.info(
        f"[*] Da luu nhan tu dong tu Gemini vao SQLite (Category: {category}, Niche: {niche}, Type: {creator_type})"
    )


def process_ai_enrichment(
    creator_info: CreatorInfo, recent_posts: list[Post]
) -> AIEnrichment | None:
    """
    Phan tich AI voi co che Teacher-Student (Gemini day Local ML)
    """
    logger.info(
        f"\n[AI Pipeline] Dang phan tich ho so Creator: {creator_info.username}"
    )

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
    summary = build_content_summary(bio, [post.caption for post in recent_posts if post.caption])
    suggested_topic = None
    semantic_tags = []

    # 3. Gemini Fallback & Auto-Labeling (JSON Prompt)
    should_call_gemini = False
    # Product-group labels are generated for every channel, not only ML failures.
    needs_fallback = True

    followers = getattr(creator_info, "followers", 0)

    # Quy tac xac dinh co goi Gemini khong (Tiet kiem API)
    if category == "UNKNOWN" or niche == "UNKNOWN":
        should_call_gemini = True
        needs_fallback = True
    elif creator_type in ["KOL", "KOC", "BRAND"]:
        should_call_gemini = True
    elif creator_type == "USER" and followers >= AUTO_LABEL_MIN_FOLLOWERS:
        should_call_gemini = True
        needs_fallback = (
            True  # Nho Gemini kiem tra lai xem co phai KOL/KOC bi ML doan nham khong
        )

    needs_fallback = True
    should_call_gemini = True
    if text_combined and should_call_gemini:
        try:
            prompt = f"""
            Dựa trên Bio: '{bio}' và các Caption: '{captions}', và đang sở hữu {creator_info.followers} followers. 
            Phân loại kênh vào 1-3 nhóm sản phẩm thực sự xuất hiện trong nội dung.
            Mỗi nhóm phải có bằng chứng riêng, xếp nhóm chính trước; không điền đủ 3 nhóm nếu không có bằng chứng.
            Không lấy nghề nghiệp, chủ đề đời sống hay từ khóa quảng cáo chung làm nhóm sản phẩm.
            Hãy phân tích và trả về ĐÚNG định dạng JSON sau (không kèm markdown ```json hay text khác):
            {{
                "contentEvidence": ["Trích nguyên văn nội dung có thật trong Caption/Bio; không thêm chủ đề hay trải nghiệm không có trong nguồn"],
                "tags": ["3-5 từ khóa xu hướng, liền mạch (ví dụ: ẩm thực đồng quê, review xe)"],
                "semanticTags": ["3-5 thẻ ngữ nghĩa sâu xa hơn (ví dụ: gia đình, chữa lành, công nghệ cốt lõi)"],
                "topic": "Một chủ đề gợi ý (ví dụ: Ẩm thực nông thôn)",
                "isSelling": false // (Trả về true nếu nội dung có mùi bán hàng, gắn link affiliate, kêu gọi chốt đơn, PR)
            """
            if needs_fallback:
                prompt += """,
                "categoryFallback": "Một trong 10 Category (BEAUTY, TECH, FOOD, FASHION, LIFESTYLE, AUTOMOTIVE, ENTERTAINMENT, EDUCATION, SPORTS, GAMING)",
                "nicheLabels": [{"label": "Chọn đúng tên nhóm: Mỹ phẩm, Trang phục, Sức khỏe và Làm đẹp, Gia dụng, Thực phẩm, Điện tử, Mẹ và Bé", "evidence": ["Trích nguyên văn Caption/Bio chứng minh riêng nhóm này"]}],
                "labelEvidence": ["Trích nguyên văn câu trong Caption/Bio chứng minh ngành/ngách; thiếu bằng chứng trả mảng rỗng"],
                "creatorTypeFallback": "Chọn 1 trong 4 loại: KOL (người nổi tiếng, chuyên gia, nghệ sĩ, có sức ảnh hưởng, thường >100k followers), KOC (tập trung review sản phẩm, unbox, gắn link affiliate, chốt đơn, mọi mức follower), BRAND (tài khoản chính thức công ty/thương hiệu), USER (người bình thường chia sẻ đời sống cá nhân, không có yếu tố thương mại)"
                """
            prompt += "\n}"

            ai_data = get_llm_enrichment(prompt)

            if ai_data:
                is_selling = ai_data.get("isSelling", False)
                # summary remains an excerpt of the actual public source, not generated prose.

                if ai_data.get("tags"):
                    tags = ai_data["tags"]
                if ai_data.get("semanticTags"):
                    semantic_tags = ai_data["semanticTags"]
                suggested_topic = ai_data.get("topic", None)

                # Update category & niche neu can va LUU vao DB
                if needs_fallback:
                    fb_cat = ai_data.get("categoryFallback")
                    fb_niche = ai_data.get("nicheFallback")
                    fb_ctype = ai_data.get("creatorTypeFallback")

                    category, niche = resolve_grounded_fallback_labels(
                        category, niche, ai_data, [bio, *[p.caption for p in recent_posts if p.caption]]
                    )

                    if (creator_type != "BRAND" and fb_ctype in ["KOL", "KOC", "BRAND", "USER"]
                            and has_label_evidence(ai_data, [bio, *[p.caption for p in recent_posts if p.caption]])):
                        creator_type = fb_ctype

                    _save_auto_label_metrics(
                        creator_info, text_combined, category, niche, creator_type
                    )

        except Exception as e:
            logger.info(f"[Canh bao] Khong the goi Gemini hoac loi JSON: {e}")

    try:
        logger.info(
            f"[Ket qua ML] Label: {cat_result.label} | Confidence: {cat_result.confidence:.2f} | Engine: {cat_result.engine}"
        )
        logger.info(f" -> Niche: {niche} | Type: {creator_type}")
    except Exception:
        pass

    return AIEnrichment(
        niche=niche,
        category=category,
        creatorType=creator_type,
        contentSummary=summary,
        tags=tags,
        suggestedTopic=suggested_topic,
        semanticTags=semantic_tags,
    )
