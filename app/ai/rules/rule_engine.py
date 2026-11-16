from typing import Any
from pydantic import BaseModel, Field

from app.ai.rules.rule_config import (
    KOC_MIN_OPINION_POSTS,
    KOC_REVIEW_RATIO_THRESHOLD,
    KOL_REPRESENTATIVE_RATIO_THRESHOLD,
    MIN_POST_PREVIEWS,
    RULE_VERSION,
    Decision,
)
from app.ai.rules.signal_extractor import (
    CandidateEvidence,
    PostPreview,
    ProfileMetadata,
    SignalExtractor,
)


class RuleDecisionResult(BaseModel):
    """
    Kết quả quyết định cuối cùng từ Rule Engine.
    """
    decision: Decision
    primary_type: str  # "KOC", "KOL", "BRAND", "ORGANIZATION", "SPAM", "UNKNOWN"
    reason_codes: list[str] = Field(default_factory=list)
    matched_signals: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    rule_version: str = RULE_VERSION


class RuleEngine:
    """
    Bộ quy tắc nghiệp vụ High-Precision KOC/KOL Classification (BR-01 đến BR-06).
    """

    @classmethod
    def evaluate(
        cls, profile: ProfileMetadata, posts: list[PostPreview]
    ) -> RuleDecisionResult:
        evidence = SignalExtractor.extract_evidence(profile, posts)
        reasons = []

        # ------------------------------------------------------------------
        # BƯỚC 1: BR-01 — ENTITY HARD REJECT GATE
        # ------------------------------------------------------------------
        # A. Kiểm tra Brand / Official Store / Shop / Merchant Bán hàng
        if evidence.has_commercial_bio or evidence.commercial_post_count >= 2:
            # Nếu có ít nhất 3 bài review chuyên sâu, creator có shop riêng được chuyển sang BR-06 xem xét
            if evidence.koc_review_post_count < 3:
                reasons.append("BR01_REJECT_COMMERCIAL_STORE")
                return RuleDecisionResult(
                    decision=Decision.AUTO_REJECT,
                    primary_type="BRAND",
                    reason_codes=reasons,
                    matched_signals=evidence.matched_signals,
                    evidence=evidence.raw_metrics,
                )

        # B. Kiểm tra Cơ quan nhà nước, Báo chí, Đài truyền hình
        if evidence.has_state_media_bio or evidence.state_media_post_count >= 3:
            reasons.append("BR01_REJECT_ORGANIZATION")
            return RuleDecisionResult(
                decision=Decision.AUTO_REJECT,
                primary_type="ORGANIZATION",
                reason_codes=reasons,
                matched_signals=evidence.matched_signals,
                evidence=evidence.raw_metrics,
            )

        # C. Kiểm tra Spam Catalog / Reup
        if evidence.spam_catalog_post_count >= 4 and not evidence.has_creator_persona:
            reasons.append("BR01_REJECT_SPAM_CATALOG")
            return RuleDecisionResult(
                decision=Decision.AUTO_REJECT,
                primary_type="SPAM",
                reason_codes=reasons,
                matched_signals=evidence.matched_signals,
                evidence=evidence.raw_metrics,
            )

        # ------------------------------------------------------------------
        # BƯỚC 2: BR-02 — CREATOR IDENTITY GATE
        # ------------------------------------------------------------------
        # Khi cào live hồ sơ chưa có bài post: Đánh giá theo Tier-0 (Ngưỡng followers và nhận diện cá nhân)
        if evidence.total_posts == 0:
            # A store-like handle is ambiguous without posts, never auto-accept it.
            # It is not sufficient evidence to classify an account as a merchant.
            handle = profile.username.strip().lstrip("@").lower()
            if "shop" in handle or "store" in handle:
                return RuleDecisionResult(
                    decision=Decision.SKIP_INSUFFICIENT,
                    primary_type="UNKNOWN",
                    reason_codes=["TIER0_STORE_HANDLE_REQUIRES_POST_EVIDENCE"],
                    matched_signals=evidence.matched_signals,
                    evidence=evidence.raw_metrics,
                )
            if profile.followers < 7000:
                reasons.append("TIER0_REJECT_LOW_FOLLOWERS")
                return RuleDecisionResult(
                    decision=Decision.AUTO_REJECT,
                    primary_type="UNKNOWN",
                    reason_codes=reasons,
                    matched_signals=evidence.matched_signals,
                    evidence=evidence.raw_metrics,
                )

            # Bio mơ hồ, không có từ khóa review / unboxing / chia sẻ và không có tick xanh -> SKIP_INSUFFICIENT
            if not evidence.has_koc_review_bio and not (profile.is_verified or evidence.has_influence_signal or evidence.has_expertise_title):
                reasons.append("BR08_INSUFFICIENT_DATA")
                return RuleDecisionResult(
                    decision=Decision.SKIP_INSUFFICIENT,
                    primary_type="UNKNOWN",
                    reason_codes=reasons,
                    matched_signals=evidence.matched_signals,
                    evidence=evidence.raw_metrics,
                )

            # Chỉ công nhận KOL khi tài khoản được tick xanh chính thức (verified) hoặc có danh xưng uy tín/chuyên gia
            is_genuine_kol = (
                profile.followers >= 200000
                and (profile.is_verified or evidence.has_influence_signal or evidence.has_expertise_title)
            )

            if is_genuine_kol:
                reasons.append("TIER0_AUTO_ACCEPT_KOL")
                return RuleDecisionResult(
                    decision=Decision.AUTO_ACCEPT_KOL,
                    primary_type="KOL",
                    reason_codes=reasons,
                    matched_signals=evidence.matched_signals + ["TIER0_KOL_MET"],
                    evidence=evidence.raw_metrics,
                )
            else:
                reasons.append("TIER0_AUTO_ACCEPT_KOC")
                return RuleDecisionResult(
                    decision=Decision.AUTO_ACCEPT_KOC,
                    primary_type="KOC",
                    reason_codes=reasons,
                    matched_signals=evidence.matched_signals + ["TIER0_KOC_MET"],
                    evidence=evidence.raw_metrics,
                )

        # ------------------------------------------------------------------
        # BƯỚC 3: BR-03 — KOC EVALUATION (Độc lập)
        # ------------------------------------------------------------------
        is_koc_candidate = False
        koc_reasons = []

        # Điều kiện KOC-02: Đủ số bài preview
        has_enough_posts_for_koc = evidence.total_posts >= MIN_POST_PREVIEWS

        if has_enough_posts_for_koc:
            # Điều kiện KOC-03: Tối thiểu 3/5 bài review
            cond_koc_03 = (
                evidence.koc_review_post_count >= 3
                or evidence.review_post_ratio >= KOC_REVIEW_RATIO_THRESHOLD
            )
            # Điều kiện KOC-04: Tối thiểu 2 bài có nhận xét cụ thể ưu/nhược
            cond_koc_04 = evidence.koc_opinion_post_count >= KOC_MIN_OPINION_POSTS
            # Điều kiện KOC-05: Có bằng chứng độc lập (bio review hoặc affiliate link)
            cond_koc_05 = evidence.has_koc_review_bio or evidence.has_affiliate_link

            if cond_koc_03 and cond_koc_04 and cond_koc_05:
                is_koc_candidate = True
                koc_reasons.extend(["KOC_03_PASSED", "KOC_04_PASSED", "KOC_05_PASSED"])

        # ------------------------------------------------------------------
        # BƯỚC 4: BR-04 — KOL EVALUATION (Độc lập)
        # ------------------------------------------------------------------
        is_kol_candidate = False
        kol_reasons = []

        has_enough_posts_for_kol = evidence.total_posts >= MIN_POST_PREVIEWS

        if has_enough_posts_for_kol:
            # Điều kiện KOL-03: Tối thiểu 3/5 bài chuyên môn/ảnh hưởng nhất quán
            cond_kol_03 = (
                evidence.expertise_post_count >= 3
                or evidence.expertise_post_ratio >= KOL_REPRESENTATIVE_RATIO_THRESHOLD
            )
            # Điều kiện KOL-04 & KOL-05: Có bằng chứng chuyên môn (Bác sĩ, TS...) hoặc Ảnh hưởng độc lập
            cond_kol_expertise = evidence.has_expertise_title and cond_kol_03
            cond_kol_influence = evidence.has_influence_signal and cond_kol_03 and evidence.has_work_contact

            # Chú ý: Tuyệt đối KHÔNG tự nhận là KOL nếu chỉ có followers cao + email booking
            if cond_kol_expertise:
                is_kol_candidate = True
                kol_reasons.extend(["KOL_EXPERTISE_PASSED", "KOL_03_PASSED"])
            elif cond_kol_influence:
                is_kol_candidate = True
                kol_reasons.extend(["KOL_INFLUENCE_PASSED", "KOL_03_PASSED"])

        # ------------------------------------------------------------------
        # BƯỚC 5: BR-05 & BR-06 — EVALUATION, CONFLICT RESOLUTION & MIXED SHOP
        # ------------------------------------------------------------------
        # Nếu creator vừa có cửa hàng riêng/bán sản phẩm vừa có review -> Không Auto-Accept ngay mà chuyển Manager Review
        has_mixed_shop_koc = evidence.has_own_shop_or_selling and is_koc_candidate
        if has_mixed_shop_koc:
            reasons.extend(koc_reasons)
            reasons.append("BR06_SELECTIVE_REVIEW_CREATOR_WITH_SHOP")
            return RuleDecisionResult(
                decision=Decision.NEEDS_MANAGER_REVIEW,
                primary_type="UNKNOWN",
                reason_codes=reasons,
                matched_signals=evidence.matched_signals,
                evidence=evidence.raw_metrics,
            )

        if is_koc_candidate and not is_kol_candidate:
            reasons.extend(koc_reasons)
            reasons.append("BR05_AUTO_ACCEPT_KOC")
            return RuleDecisionResult(
                decision=Decision.AUTO_ACCEPT_KOC,
                primary_type="KOC",
                reason_codes=reasons,
                matched_signals=evidence.matched_signals,
                evidence=evidence.raw_metrics,
            )

        if is_kol_candidate and not is_koc_candidate:
            reasons.extend(kol_reasons)
            reasons.append("BR05_AUTO_ACCEPT_KOL")
            return RuleDecisionResult(
                decision=Decision.AUTO_ACCEPT_KOL,
                primary_type="KOL",
                reason_codes=reasons,
                matched_signals=evidence.matched_signals,
                evidence=evidence.raw_metrics,
            )

        if is_koc_candidate and is_kol_candidate:
            reasons.extend(koc_reasons)
            reasons.extend(kol_reasons)
            reasons.append("BR05_DUAL_KOC_KOL_AMBIGUITY")
            # Chuyển Data Manager phân xử nhãn chính
            return RuleDecisionResult(
                decision=Decision.NEEDS_MANAGER_REVIEW,
                primary_type="UNKNOWN",
                reason_codes=reasons,
                matched_signals=evidence.matched_signals,
                evidence=evidence.raw_metrics,
            )

        # ------------------------------------------------------------------
        # BƯỚC 6: BR-06 — SELECTIVE DATA_MANAGER REVIEW GATE
        # ------------------------------------------------------------------
        # Chỉ chuyển review khi có ít nhất 2 nhóm bằng chứng tích cực nhưng chưa chạm ngưỡng Auto Accept
        has_koc_potential = (
            evidence.koc_review_post_count >= 2
            and (evidence.has_affiliate_link or evidence.has_koc_review_bio)
        )
        has_kol_potential = (
            (evidence.has_expertise_title or evidence.has_influence_signal)
            and evidence.expertise_post_count >= 1
        )
        has_mixed_shop_creator = (
            evidence.has_commercial_bio
            and evidence.koc_review_post_count >= 2
        )

        if has_koc_potential:
            reasons.append("BR06_SELECTIVE_REVIEW_KOC_POTENTIAL")
            return RuleDecisionResult(
                decision=Decision.NEEDS_MANAGER_REVIEW,
                primary_type="UNKNOWN",
                reason_codes=reasons,
                matched_signals=evidence.matched_signals,
                evidence=evidence.raw_metrics,
            )

        if has_kol_potential:
            reasons.append("BR06_SELECTIVE_REVIEW_KOL_POTENTIAL")
            return RuleDecisionResult(
                decision=Decision.NEEDS_MANAGER_REVIEW,
                primary_type="UNKNOWN",
                reason_codes=reasons,
                matched_signals=evidence.matched_signals,
                evidence=evidence.raw_metrics,
            )

        if has_mixed_shop_creator:
            reasons.append("BR06_SELECTIVE_REVIEW_CREATOR_WITH_SHOP")
            return RuleDecisionResult(
                decision=Decision.NEEDS_MANAGER_REVIEW,
                primary_type="UNKNOWN",
                reason_codes=reasons,
                matched_signals=evidence.matched_signals,
                evidence=evidence.raw_metrics,
            )

        # ------------------------------------------------------------------
        # BƯỚC 7: FALLBACK — KHÔNG ĐỦ DỮ LIỆU HOẶC KHÔNG PHẢI CREATOR
        # ------------------------------------------------------------------
        if evidence.total_posts < MIN_POST_PREVIEWS:
            reasons.append("INSUFFICIENT_POST_PREVIEWS")
            return RuleDecisionResult(
                decision=Decision.SKIP_INSUFFICIENT,
                primary_type="UNKNOWN",
                reason_codes=reasons,
                matched_signals=evidence.matched_signals,
                evidence=evidence.raw_metrics,
            )

        reasons.append("NO_CREATOR_EVIDENCE")
        return RuleDecisionResult(
            decision=Decision.SKIP_INSUFFICIENT,
            primary_type="UNKNOWN",
            reason_codes=reasons,
            matched_signals=evidence.matched_signals,
            evidence=evidence.raw_metrics,
        )
