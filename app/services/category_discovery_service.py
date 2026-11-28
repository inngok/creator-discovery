import os
import re
from typing import Any
from app.core.logger import setup_logger
from app.core.crawl_progress import report_crawl_progress
from app.core.discovery_contract import CONTRACT, canonical_label, search_terms

logger = setup_logger("category_discovery_service")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")

# Chuẩn hoá danh mục theo Taxonomy chuẩn của InfluencerMatch
CATEGORY_TAXONOMY_MAP = {label["code"]: label["searchTerms"] for label in CONTRACT["categories"]}


# Legacy label aliases only; managed fields are served by /data/crawl-fields.
LEGACY_CATEGORY_CODES_BY_LABEL = {alias.casefold(): label["code"]
                                for label in CONTRACT["categories"] for alias in label["aliases"]}


class CategoryDiscoveryService:
    def get_candidates_by_category(
        self, category_code: str, limit: int = 5, platform: str = "ALL",
        keywords: list[str] | None = None
    ) -> list[dict[str, Any]]:
        from app.crawlers.discovery_crawler import (
            search_new_facebook_candidates, search_new_tiktok_candidates,
            search_new_instagram_candidates,
        )
        target = (platform or "ALL").upper().strip()
        if target not in {"TIKTOK", "FACEBOOK", "INSTAGRAM", "ALL", "BOTH"}:
            raise ValueError("Unsupported discovery platform")
        if limit <= 0:
            return []
        code = canonical_label(category_code)
        terms = keywords or search_terms(code)
        terms = list(dict.fromkeys(t.strip().lstrip("#").strip() for t in terms
                                   if isinstance(t, str) and t.strip().lstrip("#").strip()))
        if not terms:
            return []
        candidates = []
        errors = []
        runners = {"TIKTOK": search_new_tiktok_candidates,
                   "FACEBOOK": search_new_facebook_candidates,
                   "INSTAGRAM": search_new_instagram_candidates}
        for name, runner in runners.items():
            remaining = limit - len(candidates)
            if remaining <= 0:
                break
            if target not in {name, "ALL", "BOTH"}:
                continue
            try:
                report_crawl_progress(
                    f"Bắt đầu tìm ứng viên trên {name}.",
                    platform=name,
                    stage="discovery",
                )
                kwargs = dict(keywords=terms, max_per_keyword=remaining, max_total=remaining)
                kwargs["requested_category"] = code
                before_count = len(candidates)
                candidates.extend(runner(**kwargs))
                report_crawl_progress(
                    f"{name}: tìm thấy {len(candidates) - before_count} ứng viên đạt điều kiện.",
                    platform=name,
                    stage="discovery",
                )
            except Exception as error:
                report_crawl_progress(
                    f"{name}: nguồn tìm kiếm gặp lỗi, đang xử lý nền tảng tiếp theo.",
                    platform=name,
                    stage="source_error",
                )
                if target not in {"ALL", "BOTH"}:
                    raise
                errors.append(error)
                logger.warning("Discovery %s failed; continuing other platforms: %s", name, error)
        if errors and not candidates:
            from app.crawlers.tiktok_discovery import TikTokDiscoveryError
            raise TikTokDiscoveryError("Discovery unavailable: " + "; ".join(str(e) for e in errors))
        return candidates[:limit]


category_discovery_service = CategoryDiscoveryService()
