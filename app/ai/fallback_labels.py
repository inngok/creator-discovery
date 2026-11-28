from app.core.discovery_contract import CATEGORY_CODES, canonical_label

VALID_CATEGORIES = CATEGORY_CODES


def resolve_fallback_labels(category, niche, ai_data):
    candidate = canonical_label(ai_data.get("categoryFallback"))
    if candidate in VALID_CATEGORIES:
        category = candidate
    candidate_niche = ai_data.get("nicheFallback")
    if category in VALID_CATEGORIES and isinstance(candidate_niche, str):
        candidate_niche = candidate_niche.strip()
        candidate_niche = {
            "WELLNESS": "Chăm sóc sức khỏe",
            "SKINCARE": "Chăm sóc da",
            "HOME_APPLIANCE_REVIEW": "Đánh giá đồ gia dụng",
            "PRODUCT_REVIEW": "Đánh giá sản phẩm",
        }.get(candidate_niche.upper(), candidate_niche)
        if candidate_niche and candidate_niche.upper() not in {"UNKNOWN", "NULL", "NONE", "N/A"}:
            niche = candidate_niche
    return category, niche
