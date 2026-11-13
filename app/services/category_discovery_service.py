import os
import re
from typing import Any
import pandas as pd
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
    def __init__(self):
        self._cache_df: pd.DataFrame | None = None
        self._load_directory()

    def _load_directory(self):
        excel_files = [f for f in os.listdir(DATA_DIR) if f.endswith(".xlsx") and "TÌm_kiếm" in f]
        dfs = []
        for ef in excel_files:
            try:
                full_path = os.path.join(DATA_DIR, ef)
                dfs.append(pd.read_excel(full_path))
            except Exception as e:
                logger.warning(f"Không thể đọc file {ef}: {e}")

        if dfs:
            combined = pd.concat(dfs, ignore_index=True)
            if "ID Chuyên gia" in combined.columns:
                self._cache_df = combined.drop_duplicates(subset=["ID Chuyên gia"]).dropna(subset=["ID Chuyên gia"])
                logger.info(f"[CATEGORY SERVICE] Đã tải danh mục {len(self._cache_df)} KOCs từ hệ thống.")

    def get_candidates_by_category(
        self, category_code: str, limit: int = 5, platform: str = "ALL",
        keywords: list[str] | None = None
    ) -> list[dict[str, Any]]:
        norm_code = category_code.upper().strip()
        plat_upper = (platform or "ALL").upper().strip()

        if plat_upper == "FACEBOOK":
            from app.crawlers.discovery_crawler import search_new_facebook_candidates
            search_terms = list(dict.fromkeys(term.strip().lstrip("#").strip()
                for term in (keywords or [category_code]) if term.strip().lstrip("#").strip()))
            return search_new_facebook_candidates(
                keywords=search_terms, max_per_keyword=limit, max_total=limit)

        candidates: list[dict[str, Any]] = []

        # 1. Lấy dữ liệu thực từ cache hệ thống (Dataset đã tải)
        if self._cache_df is not None and not self._cache_df.empty:
            # Managed category names are search terms; legacy codes remain supported.
            legacy_code = LEGACY_CATEGORY_CODES_BY_LABEL.get(category_code.strip().casefold(), norm_code)
            matched_tags = CATEGORY_TAXONOMY_MAP.get(legacy_code)
            if matched_tags is None:
                matched_tags = [term.strip() for term in re.split(r"[&,/;]+", category_code)
                                if term.strip()]
            matched_tags = list(matched_tags) + [word.strip() for word in (keywords or []) if word.strip()]
            if not matched_tags:
                return []
            pattern = "|".join([re.escape(t) for t in matched_tags])
            sub_df = self._cache_df[
                self._cache_df["Phân loại Nhà sáng tạo"].astype(str).str.contains(pattern, case=False, na=False)
            ]

            SHOP_KEYWORDS = [
                "shop", "store", "tongkho", "bansi", "sile", "khohang", "order", "boutique",
                "cuahang", "sieu.tichcuc", "donhang", "hoantien", "1doi1", "xakho", "thanhly"
            ]
            for _, row in sub_df.iterrows():
                uname = str(row["ID Chuyên gia"]).strip().removeprefix("@")
                dname = str(row.get("Biệt danh chuyên gia", uname))
                check_text = f"{uname} {dname}".lower()
                if any(k in check_text for k in SHOP_KEYWORDS):
                    continue
                candidates.append({
                    "username": uname,
                    "display_name": dname,
                    "category": str(row.get("Phân loại Nhà sáng tạo", norm_code)),
                    "platform": "TIKTOK",
                    "avatar_hint": str(row.get("Ảnh hồ sơ Chuyên gia", "")),
                    "profile_url": f"https://www.tiktok.com/@{uname}",
                })
                if len(candidates) >= limit:
                    break

        # 2. Nếu người dùng chỉ định FACEBOOK: Tìm trong Staging DB thực tế
        return candidates[:limit]


category_discovery_service = CategoryDiscoveryService()
