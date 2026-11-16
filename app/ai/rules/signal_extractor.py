from typing import Any
from pydantic import BaseModel, Field

from app.ai.rules.rule_config import (
    AFFILIATE_DOMAINS,
    REGEX_COMMERCIAL_SELLER,
    REGEX_KOC_OPINION,
    REGEX_KOC_REVIEW,
    REGEX_KOL_EXPERTISE,
    REGEX_KOL_INFLUENCE,
    REGEX_SPAM_CATALOG,
    REGEX_STATE_MEDIA,
)


class PostPreview(BaseModel):
    """Mô hình dữ liệu tóm tắt cho bài đăng ở bước Preflight."""
    post_id: str = ""
    caption: str = ""
    created_at: str | None = None
    views: int | None = 0
    likes: int | None = 0


class ProfileMetadata(BaseModel):
    """Mô hình thông tin profile cơ bản."""
    platform: str = "TIKTOK"
    external_id: str = ""
    username: str = ""
    display_name: str = ""
    bio: str = ""
    followers: int = 0
    bio_links: list[str] = Field(default_factory=list)
    public_email: str | None = None
    public_phone: str | None = None
    is_verified: bool = False


class CandidateEvidence(BaseModel):
    """
    Tập hợp tất cả các bằng chứng định lượng và định tính được bóc tách độc lập.
    """
    total_posts: int = 0

    # 1. Entity Signals (Brand, Shop, Media, Spam)
    has_commercial_bio: bool = False
    has_own_shop_or_selling: bool = False
    commercial_post_count: int = 0
    has_state_media_bio: bool = False
    state_media_post_count: int = 0
    spam_catalog_post_count: int = 0

    # 2. Creator Identity Signals
    has_creator_persona: bool = False
    has_personal_name_or_face: bool = False
    personal_expression_post_count: int = 0

    # 3. KOC Signals
    has_koc_review_bio: bool = False
    has_affiliate_link: bool = False
    koc_review_post_count: int = 0
    koc_opinion_post_count: int = 0

    # 4. KOL Signals
    has_expertise_title: bool = False
    has_influence_signal: bool = False
    has_work_contact: bool = False
    expertise_post_count: int = 0

    # Danh sách chi tiết các tín hiệu nhận diện được
    matched_signals: list[str] = Field(default_factory=list)
    raw_metrics: dict[str, Any] = Field(default_factory=dict)

    @property
    def review_post_ratio(self) -> float:
        if self.total_posts <= 0:
            return 0.0
        return self.koc_review_post_count / self.total_posts

    @property
    def commercial_post_ratio(self) -> float:
        if self.total_posts <= 0:
            return 0.0
        return self.commercial_post_count / self.total_posts

    @property
    def expertise_post_ratio(self) -> float:
        if self.total_posts <= 0:
            return 0.0
        return self.expertise_post_count / self.total_posts


class SignalExtractor:
    """
    Trích xuất bằng chứng độc lập từ Profile và danh sách Post Previews.
    Phân tách rạch ròi giữa Profile Signals và Content Signals.
    """

    @classmethod
    def extract_evidence(
        cls, profile: ProfileMetadata, posts: list[PostPreview]
    ) -> CandidateEvidence:
        evidence = CandidateEvidence(total_posts=len(posts))
        matched = []

        bio_clean = (profile.bio or "").lower().strip()
        display_name_clean = (profile.display_name or "").lower().strip()
        username_clean = (profile.username or "").lower().strip()
        profile_text = f"{username_clean} {display_name_clean} {bio_clean}"

        # ----------------------------------------------------
        # 1. BÓC TÁCH ENTITY SIGNALS (Thương mại / Báo đài / Spam)
        # ----------------------------------------------------
        # Kiểm tra Bio Profile
        if REGEX_COMMERCIAL_SELLER.search(profile_text):
            evidence.has_commercial_bio = True
            matched.append("BIO_HAS_COMMERCIAL_SELLER")

        if REGEX_STATE_MEDIA.search(profile_text):
            evidence.has_state_media_bio = True
            matched.append("BIO_HAS_STATE_MEDIA")

        # Kiểm tra từng Post Preview
        for idx, post in enumerate(posts):
            caption_lower = (post.caption or "").lower()
            if not caption_lower:
                continue

            if REGEX_COMMERCIAL_SELLER.search(caption_lower):
                evidence.commercial_post_count += 1

            if REGEX_STATE_MEDIA.search(caption_lower):
                evidence.state_media_post_count += 1

            if REGEX_SPAM_CATALOG.search(caption_lower):
                evidence.spam_catalog_post_count += 1

        if evidence.commercial_post_count > 0:
            matched.append(f"POSTS_HAS_COMMERCIAL_{evidence.commercial_post_count}")
        if evidence.state_media_post_count > 0:
            matched.append(f"POSTS_HAS_STATE_MEDIA_{evidence.state_media_post_count}")
        if evidence.spam_catalog_post_count > 0:
            matched.append(f"POSTS_HAS_SPAM_CATALOG_{evidence.spam_catalog_post_count}")

        # Dấu hiệu sở hữu shop cá nhân hoặc hoạt động bán hàng kèm theo
        own_shop_keywords = ["shop", "cửa hàng", "có bán", "đặt hàng", "mua hàng tại", "gian hàng"]
        has_own_shop_text = any(w in profile_text for w in own_shop_keywords)
        evidence.has_own_shop_or_selling = bool(
            evidence.has_commercial_bio
            or has_own_shop_text
            or evidence.commercial_post_count > 0
            or evidence.spam_catalog_post_count > 0
        )
        if evidence.has_own_shop_or_selling:
            matched.append("HAS_OWN_SHOP_OR_SELLING")

        # ----------------------------------------------------
        # 2. BÓC TÁCH CREATOR IDENTITY SIGNALS
        # ----------------------------------------------------
        # Dấu hiệu cá nhân: tên riêng, xưng hô cá nhân (mình, tớ, em, tôi, share, vlog, daily)
        personal_indicators = ["mình", "tớ", "em", "tôi", "chia sẻ", "vlog", "daily", "life", "family"]
        has_personal_pronouns = any(
            p in bio_clean for p in personal_indicators
        ) or not evidence.has_commercial_bio

        evidence.has_creator_persona = has_personal_pronouns
        if has_personal_pronouns:
            matched.append("CREATOR_PERSONA_DETECTED")

        # ----------------------------------------------------
        # 3. BÓC TÁCH KOC SIGNALS
        # ----------------------------------------------------
        # Bio review
        if REGEX_KOC_REVIEW.search(profile_text):
            evidence.has_koc_review_bio = True
            matched.append("BIO_HAS_KOC_REVIEW_KEYWORDS")

        # Affiliate Link trong Bio Links hoặc Bio text
        all_links = list(profile.bio_links)
        if profile.bio:
            all_links.append(profile.bio)

        for link in all_links:
            if any(dom in link.lower() for dom in AFFILIATE_DOMAINS):
                evidence.has_affiliate_link = True
                matched.append("HAS_AFFILIATE_LINK")
                break

        # Đánh giá nội dung từng Post Preview cho KOC
        for post in posts:
            caption_lower = (post.caption or "").lower()
            if not caption_lower:
                continue

            is_review = bool(REGEX_KOC_REVIEW.search(caption_lower))
            is_opinion = bool(REGEX_KOC_OPINION.search(caption_lower))

            if is_review:
                evidence.koc_review_post_count += 1
            if is_opinion:
                evidence.koc_opinion_post_count += 1

        if evidence.koc_review_post_count > 0:
            matched.append(f"KOC_REVIEW_POSTS_{evidence.koc_review_post_count}")
        if evidence.koc_opinion_post_count > 0:
            matched.append(f"KOC_OPINION_POSTS_{evidence.koc_opinion_post_count}")

        # ----------------------------------------------------
        # 4. BÓC TÁCH KOL SIGNALS
        # ----------------------------------------------------
        # Chức danh chuyên môn
        if REGEX_KOL_EXPERTISE.search(profile_text):
            evidence.has_expertise_title = True
            matched.append("BIO_HAS_KOL_EXPERTISE_TITLE")

        # Tín hiệu ảnh hưởng, diễn giả, workshop
        if REGEX_KOL_INFLUENCE.search(profile_text):
            evidence.has_influence_signal = True
            matched.append("BIO_HAS_INFLUENCE_SIGNAL")

        # Booking Contact chuyên nghiệp
        if profile.public_email or profile.public_phone:
            evidence.has_work_contact = True
            matched.append("HAS_WORK_CONTACT")

        # Đánh giá nội dung Post Preview cho KOL
        for post in posts:
            caption_lower = (post.caption or "").lower()
            if not caption_lower:
                continue

            if REGEX_KOL_EXPERTISE.search(caption_lower) or REGEX_KOL_INFLUENCE.search(caption_lower):
                evidence.expertise_post_count += 1

        if evidence.expertise_post_count > 0:
            matched.append(f"KOL_EXPERTISE_POSTS_{evidence.expertise_post_count}")

        evidence.matched_signals = matched
        evidence.raw_metrics = {
            "followers": profile.followers,
            "posts_count": len(posts),
            "review_ratio": round(evidence.review_post_ratio, 2),
            "commercial_ratio": round(evidence.commercial_post_ratio, 2),
            "expertise_ratio": round(evidence.expertise_post_ratio, 2),
            "opinion_count": evidence.koc_opinion_post_count,
        }

        return evidence
