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

    def get_supported_categories(self) -> list[dict[str, str]]:
        return [
            {"code": "BEAUTY", "label": "Làm đẹp & Mỹ phẩm", "icon": "💄"},
            {"code": "TECH", "label": "Công nghệ & Điện thoại / PC", "icon": "💻"},
            {"code": "FOOD", "label": "Ẩm thực & Nhà hàng", "icon": "🍜"},
            {"code": "FASHION", "label": "Thời trang & Phụ kiện", "icon": "👗"},
            {"code": "ENTERTAINMENT", "label": "Truyền thông & Giải trí", "icon": "🎬"},
            {"code": "TRAVEL", "label": "Du lịch & Trải nghiệm", "icon": "✈️"},
            {"code": "LIFESTYLE", "label": "Đời sống, Sức khỏe & Gia đình", "icon": "🏡"},
        ]

    def get_candidates_by_category(self, category_code: str, limit: int = 5) -> list[dict[str, Any]]:
        norm_code = category_code.upper().strip()
        matched_tags = CATEGORY_TAXONOMY_MAP.get(norm_code, [norm_code])

        if self._cache_df is None or self._cache_df.empty:
            return []

        pattern = "|".join([re.escape(t) for t in matched_tags])
        sub_df = self._cache_df[
            self._cache_df["Phân loại Nhà sáng tạo"].astype(str).str.contains(pattern, case=False, na=False)
        ]

        candidates = []
        for _, row in sub_df.head(limit).iterrows():
            uname = str(row["ID Chuyên gia"]).strip().removeprefix("@")
            candidates.append({
                "username": uname,
                "display_name": str(row.get("Biệt danh chuyên gia", uname)),
                "category": str(row.get("Phân loại Nhà sáng tạo", norm_code)),
                "avatar_hint": str(row.get("Ảnh hồ sơ Chuyên gia", "")),
                "profile_url": f"https://www.tiktok.com/@{uname}",
            })

        return candidates


category_discovery_service = CategoryDiscoveryService()

