import os
import re
from typing import Any
from app.core.logger import setup_logger

logger = setup_logger("category_discovery_service")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")

# Chuẩn hoá danh mục theo Taxonomy chuẩn của InfluencerMatch
CATEGORY_TAXONOMY_MAP = {
    "BEAUTY": ["Làm đẹp", "Mỹ phẩm", "Chăm sóc cá nhân"],
    "FASHION": ["Quần áo & Phụ kiện", "Thời trang", "Giày dép"],
    "FOOD": ["Thực phẩm & Đồ uống", "Thực phẩm & Nấu ăn", "Ăn uống"],
    "TECH": ["IT & Cao Kỹ Thuật", "Máy móc & Thiết bị", "Công nghệ", "Điện tử"],
    "ENTERTAINMENT": ["Truyền thông & Giải trí", "MCN", "Hài hước", "Game"],
    "TRAVEL": ["Đi lại & Du lịch", "Du lịch"],
    "LIFESTYLE": ["Nhà cửa, Nội thất & Thiết bị gia dụng", "Sức khỏe & Sống khỏe", "Đời sống"],
    "EDUCATION": ["Giáo dục & Đào tạo"],
}


# Legacy label aliases only; managed fields are served by /data/crawl-fields.
LEGACY_CATEGORY_CODES_BY_LABEL = {
    'làm đẹp & mỹ phẩm': 'BEAUTY',
    'công nghệ & điện thoại / pc': 'TECH',
    'ẩm thực & nhà hàng': 'FOOD',
    'thời trang & phụ kiện': 'FASHION',
    'truyền thông & giải trí': 'ENTERTAINMENT',
    'du lịch & trải nghiệm': 'TRAVEL',
    'đời sống, sức khỏe & gia đình': 'LIFESTYLE',
}


class CategoryDiscoveryService:
    def get_candidates_by_category(
        self, category_code: str, limit: int = 5, platform: str = "ALL",
        keywords: list[str] | None = None
    ) -> list[dict[str, Any]]:
        from app.crawlers.discovery_crawler import (
            search_new_facebook_candidates, search_new_tiktok_candidates,
        )
        target = (platform or "ALL").upper().strip()
        if target not in {"TIKTOK", "FACEBOOK", "ALL", "BOTH"}:
            raise ValueError("Unsupported discovery platform")
        if limit <= 0:
            return []
        code = LEGACY_CATEGORY_CODES_BY_LABEL.get(category_code.strip().casefold(),
                                                 category_code.upper().strip())
        terms = keywords or CATEGORY_TAXONOMY_MAP.get(code, [category_code])
        terms = list(dict.fromkeys(t.strip().lstrip("#").strip() for t in terms
                                   if isinstance(t, str) and t.strip().lstrip("#").strip()))
        if not terms:
            return []
        candidates = []
        if target in {"TIKTOK", "ALL", "BOTH"}:
            candidates.extend(search_new_tiktok_candidates(
                keywords=terms, max_per_keyword=limit, max_total=limit,
                requested_category=category_code))
        remaining = limit - len(candidates)
        if remaining > 0 and target in {"FACEBOOK", "ALL", "BOTH"}:
            candidates.extend(search_new_facebook_candidates(
                keywords=terms, max_per_keyword=remaining, max_total=remaining,
                requested_category=category_code))
        return candidates[:limit]


category_discovery_service = CategoryDiscoveryService()
