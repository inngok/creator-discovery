import re
from typing import Any

from app.ai.rules.rule_config import (
    AFFILIATE_DOMAINS,
    KOC_OPINION_PHRASES,
    KOC_REVIEW_ACTION_PHRASES,
    KOL_EXPERTISE_TITLES,
    KOL_INFLUENCE_PHRASES,
    COMMERCIAL_SELLER_PHRASES,
)


def classify_creator_type_smart(
    username: str = "",
    display_name: str = "",
    bio: str = "",
    followers: int = 0,
    recent_posts: list[Any] = None,
    bio_links: list[str] = None,
    category: str = "",
    niche: str = "",
) -> str:
    """
    Phân loại thông minh KOC vs KOL dựa trên NỘI DUNG VÀ HÀNH VI (Content & Behavior-based).
    Không phụ thuộc cứng vào số follower.
    """
    if recent_posts is None:
        recent_posts = []
    if bio_links is None:
        bio_links = []

    # 1. Thu thập toàn bộ văn bản từ Profile và Captions của các video gần đây
    captions = []
    for p in recent_posts:
        if isinstance(p, dict):
            cap = p.get("caption", "")
        else:
            cap = getattr(p, "caption", "")
        if cap:
            captions.append(str(cap))

    all_captions_text = " ".join(captions).lower()
    profile_text = f"{username} {display_name} {bio} {category} {niche}".lower()
    full_text = f"{profile_text} {all_captions_text}"

    # 2. Kiểm tra dấu hiệu Affiliate & Review đặc thù của KOC
    has_affiliate = False
    for domain in AFFILIATE_DOMAINS:
        if domain in bio.lower() or any(domain in link.lower() for link in bio_links):
            has_affiliate = True
            break

    # Đếm số lượng tín hiệu Review & Nhận xét trải nghiệm người dùng
    koc_review_matches = sum(1 for kw in KOC_REVIEW_ACTION_PHRASES if kw in full_text)
    koc_opinion_matches = sum(1 for kw in KOC_OPINION_PHRASES if kw in full_text)

    # 3. Kiểm tra dấu hiệu Chuyên môn & Tầm ảnh hưởng đặc thù của KOL
    kol_expertise_matches = sum(1 for kw in KOL_EXPERTISE_TITLES if kw in profile_text)
    kol_influence_matches = sum(1 for kw in KOL_INFLUENCE_PHRASES if kw in full_text)
    has_work_contact = bool(
        re.search(r"[\w\.-]+@[\w\.-]+\.\w+", bio)
        or any(w in profile_text for w in ["booking", "work", "hợp tác", "contact", "liên hệ công việc"])
    )

    # 4. QUY TẮC PHÂN LOẠI THÔNG MINH:

    # A. Nếu có chức danh chuyên môn rõ rệt (Bác sĩ, Diễn viên, Giám đốc, Ca sĩ, MC, Thạc sĩ...)
    # -> Đây là KOL (kể cả follower chỉ từ 50k - 200k)
    if kol_expertise_matches > 0 and (has_work_contact or followers >= 50000):
        # Trừ khi kênh này 100% làm affiliate bán hàng rẻ tiền
        if not (has_affiliate and koc_review_matches >= 3 and kol_influence_matches == 0):
            return "KOL"

    # B. Nếu có dấu hiệu KOC rõ rệt: Có link giỏ hàng/affiliate HOẶC có từ khóa review/unboxing/săn sale
    # Bất kể follower 500k, 1M hay 2M -> Vẫn là KOC (Key Opinion Consumer)
    if has_affiliate or (koc_review_matches >= 2 and koc_opinion_matches >= 1):
        # Nếu có lượng follower cực khủng (> 2M) và được coi là Đại sứ thương hiệu lớn
        if followers >= 2000000 and kol_influence_matches >= 2:
            return "KOL"
        return "KOC"

    # C. Nếu có hoạt động ảnh hưởng lớn (Đại sứ, Diễn giả, Fashion week, Giám khảo...)
    if kol_influence_matches >= 1 and (has_work_contact or followers >= 100000):
        return "KOL"

    # D. Kênh nội dung giải trí / Lifestyle thông thường:
    # Nếu kênh rất lớn (>= 1M followers) và có quản lý/booking chuyên nghiệp -> KOL
    if followers >= 1000000 and has_work_contact:
        return "KOL"

    # E. Mặc định các kênh sáng tạo nội dung cá nhân, làm video đời sống, chia sẻ trải nghiệm -> KOC
    return "KOC"
