"""
AI Explanation Service
Sinh câu giải thích tại sao một Creator được đề xuất cho một Campaign.
Chấm điểm 4 tiêu chí cốt lõi:
1. engagementScore: Đo chất lượng tương tác so với benchmark chuẩn theo Follower Tier.
2. followerScore: Đánh giá độ phù hợp của quy mô phân khúc Follower Tier với chiến dịch.
3. semanticMatchScore: Độ tương đồng Cosine Similarity giữa nội dung Creator và Campaign Brief.
4. nicheMatchScore: Đánh giá độ trùng khớp danh mục / nhóm ngành (Exact match, Related, Out-of-niche).
"""

from dataclasses import dataclass, field


@dataclass
class ExplanationInput:
    """Dữ liệu đầu vào để sinh explanation."""
    creator_name: str
    followers: int
    engagement_rate: float
    niche: str
    category: str
    creator_type: str                   # KOL / KOC / CELEBRITY
    engagement_rate_views: float | None = None  # View-based ER (%) cho TikTok/Reels/Shorts
    platform: str = "TIKTOK"
    avg_views: float = 0.0
    avg_likes: float = 0.0
    similarity_score: float = 0.0       # Score từ vector search (0.0 – 1.0)
    campaign_brief: str = ""            # Brief của campaign (optional)
    content_summary: str = ""           # Bio/summary của creator
    top_tags: list[str] = field(default_factory=list)
    target_followers: int | None = None
    min_followers: int | None = None
    max_followers: int | None = None


@dataclass
class ExplanationOutput:
    summary: str                        # Câu tóm tắt ngắn gọn (1 câu)
    reasons: list[str]                  # Danh sách lý do chi tiết
    score_breakdown: dict               # Điểm từng tiêu chí

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "reasons": self.reasons,
            "scoreBreakdown": self.score_breakdown,
        }


# ---------------------------------------------------------------------------
# Declarative Configs & Benchmarks (Industry Standard)
# ---------------------------------------------------------------------------
# (Ngưỡng followers, Tên Tier, ER Benchmark chuẩn, Điểm Tier cơ sở)
FOLLOWER_TIERS = (
    (10_000, "Nano Influencer", 4.5, 75.0),
    (100_000, "Micro Influencer", 3.0, 95.0),
    (1_000_000, "Macro Influencer", 2.0, 85.0),
    (float("inf"), "Mega Influencer / KOL", 1.2, 90.0),
)

SIMILARITY_LEVELS = (
    (85.0, "rất phù hợp"),
    (65.0, "khá phù hợp"),
    (45.0, "phù hợp một phần"),
    (0.0, "ít phù hợp"),
)

IGNORED_NICHES = frozenset({"general", "unknown", "khác", "khac", ""})


def _tier_info(followers: int) -> tuple[str, float, float]:
    """Trả về (tier_label, benchmark_er, base_score) tương ứng với số followers."""
    return next((label, benchmark, base_score) for limit, label, benchmark, base_score in FOLLOWER_TIERS if followers < limit)


def _calculate_engagement_score(
    rate: float,
    followers: int,
    er_views: float | None = None,
    platform: str = "TIKTOK",
    avg_views: float = 0.0,
    avg_likes: float = 0.0,
) -> tuple[float, str]:
    """
    Tiêu chí 1: engagementScore
    - Nền tảng video ngắn (TikTok, Reels, Shorts): Ưu tiên tính theo View-based Engagement Rate (ER_views)
      ER_views = (avgLikes + avgComments + avgShares) / avgViews * 100%
      Benchmark chuẩn video ngắn: 5.0%
    - Nền tảng truyền thống / Không có views: So sánh với Follower-based ER benchmark theo Tier.
    """
    is_short_video = str(platform or "").upper() in {"TIKTOK", "REELS", "SHORTS", "YOUTUBE_SHORTS"}

    computed_er_views = er_views
    if computed_er_views is None and avg_views > 0 and avg_likes > 0:
        computed_er_views = (avg_likes / avg_views) * 100.0

    if is_short_video and computed_er_views is not None and computed_er_views > 0:
        benchmark = 5.0  # Benchmark chuẩn video ngắn
        score = min(100.0, (computed_er_views / benchmark) * 100.0)
        score = round(score, 1)
        if computed_er_views >= benchmark:
            reason = f"Tỷ lệ tương tác theo lượt xem ({computed_er_views:.1f}%) vượt chuẩn {benchmark}% của video ngắn."
        elif score >= 70.0:
            reason = f"Tỷ lệ tương tác theo lượt xem ({computed_er_views:.1f}%) duy trì tốt, đạt mức ổn định của video ngắn."
        else:
            reason = f"Tỷ lệ tương tác theo lượt xem ({computed_er_views:.1f}%) ở mức vừa phải."
        return score, reason

    # Fallback Follower-based ER theo Tier
    tier_label, benchmark, _ = _tier_info(followers)
    score = min(100.0, (rate / benchmark) * 100.0) if benchmark > 0 else 50.0
    score = round(score, 1)

    if rate >= benchmark:
        reason = f"Tỷ lệ tương tác {rate:.1f}% vượt trội so với mặt bằng chung của các kênh cùng quy mô."
    elif score >= 70.0:
        reason = f"Tỷ lệ tương tác {rate:.1f}% duy trì ổn định, bám sát mức trung bình của các kênh cùng quy mô."
    else:
        reason = f"Tỷ lệ tương tác {rate:.1f}% ở mức vừa phải so với các kênh cùng quy mô."
    return score, reason



def _calculate_follower_score(
    followers: int,
    brief: str = "",
    target_followers: int | None = None,
    min_followers: int | None = None,
    max_followers: int | None = None,
) -> tuple[float, str]:
    """
    Tiêu chí 2: followerScore
    Đánh giá độ tối ưu của phân khúc quy mô người theo dõi.
    Sử dụng thang đo Logarithm (log10) và phạt phi đối xứng (Asymmetric Penalty)
    để khắc phục lỗi phạt dốc rớt về 0 khi Followers vượt quá Target.
    """
    import math

    fl = max(1, followers)

    # 1. Trường hợp có khoảng mục tiêu [min_followers, max_followers]
    if min_followers and max_followers and min_followers <= max_followers:
        if min_followers <= fl <= max_followers:
            return 100.0, f"Lượng theo dõi {followers:,} người nằm trọn vẹn trong khoảng mục tiêu ({min_followers:,} - {max_followers:,})."
        elif fl < min_followers:
            penalty = (math.log10(min_followers) - math.log10(fl)) * 50.0
            score = round(max(0.0, 100.0 - penalty), 1)
            return score, f"Lượng theo dõi {followers:,} người thấp hơn mức tối thiểu ({min_followers:,}) của chiến dịch."
        else:
            # Vượt max: phạt nhẹ (hệ số 30, sàn 60.0) vì kênh lớn vẫn mang lại độ phủ
            penalty = (math.log10(fl) - math.log10(max_followers)) * 30.0
            score = round(max(60.0, 100.0 - penalty), 1)
            return score, f"Lượng theo dõi {followers:,} người vượt mức tối đa ({max_followers:,}), gia tăng độ phủ lớn cho chiến dịch."

    # 2. Trường hợp có mục tiêu cụ thể target_followers
    if target_followers and target_followers > 0:
        if fl == target_followers:
            return 100.0, f"Lượng theo dõi {followers:,} người khớp chính xác mục tiêu ({target_followers:,})."
        elif fl < target_followers:
            penalty = (math.log10(target_followers) - math.log10(fl)) * 50.0
            score = round(max(0.0, 100.0 - penalty), 1)
            return score, f"Lượng theo dõi {followers:,} người đạt {score} điểm so với mục tiêu ({target_followers:,})."
        else:
            # Vượt target: phạt nhẹ (hệ số 30, sàn 50.0), không bao giờ rơi về 0
            penalty = (math.log10(fl) - math.log10(target_followers)) * 30.0
            score = round(max(50.0, 100.0 - penalty), 1)
            return score, f"Lượng theo dõi {followers:,} người vượt mục tiêu ({target_followers:,}), giúp khuếch đại độ phủ thương hiệu."

    # 3. Trường hợp không có Target cụ thể -> Dùng phân khúc Tier chuẩn
    tier_label, _, base_score = _tier_info(followers)
    brief_lower = brief.lower()
    tier_keyword = tier_label.split()[0].lower()

    matched = tier_keyword in brief_lower or ("koc" in brief_lower and followers < 100_000)
    score = 100.0 if matched else base_score
    if matched:
        reason = f"Lượng theo dõi {followers:,} người vừa tầm tiếp cận, đúng tệp khán giả mục tiêu của chiến dịch."
    else:
        reason = f"Lượng theo dõi {followers:,} người, quy mô phù hợp để tối ưu chi phí và độ phủ."
    return score, reason


def _calculate_semantic_score(similarity_score: float) -> tuple[float, str]:
    """
    Tiêu chí 3: semanticMatchScore
    Áp dụng Min-Max Rescaling quy đổi khoảng Cosine Similarity thực tế [0.30, 0.85]
    của các mô hình Vector Embedding tiếng Việt (BKAI Bi-Encoder) về thang điểm [0, 100].
    """
    SIM_MIN = 0.30
    SIM_MAX = 0.85
    sim = max(0.0, min(1.0, float(similarity_score or 0.0)))

    if sim <= SIM_MIN:
        score = 0.0
    elif sim >= SIM_MAX:
        score = 100.0
    else:
        score = round(((sim - SIM_MIN) / (SIM_MAX - SIM_MIN)) * 100.0, 1)

    if score >= 75.0:
        reason = f"Nội dung các bài đăng gần đây rất ăn khớp với thông điệp của chiến dịch (độ tương đồng {score:.1f}/100)."
    elif score >= 50.0:
        reason = f"Phong cách nội dung khá phù hợp với định hướng quảng bá của nhãn hàng (độ tương đồng {score:.1f}/100)."
    else:
        reason = f"Nội dung có điểm chạm nhất định với chủ đề chiến dịch (độ tương đồng {score:.1f}/100)."
    return score, reason


def _calculate_niche_score(data: ExplanationInput) -> tuple[float, str]:
    """
    Tiêu chí 4: nicheMatchScore
    Tính toán động không hardcode từ điển:
    - Exact match Niche trong Brief: 100 điểm.
    - Tag match trong Brief: 90 điểm.
    - Category match trong Brief: 85 điểm.
    - Related match: dựa theo Semantic Similarity Score từ Vector Search.
    """
    brief = (data.campaign_brief or "").strip().lower()
    niche = (data.niche or "").strip().lower()
    cat = (data.category or "").strip().lower()
    niche_info = f"{data.category} / {data.niche}" if data.niche else data.category

    if not brief:
        base_score = 85.0 if niche not in IGNORED_NICHES else 60.0
        score = round(data.similarity_score * 100.0, 1) if data.similarity_score > 0 else base_score
        return score, f"Hoạt động chuyên môn trong lĩnh vực {niche_info}."

    # 1. Exact Match Niche trong brief -> 100 điểm
    if niche not in IGNORED_NICHES and (niche in brief or any(w in brief for w in niche.split() if len(w) > 2)):
        return 100.0, f"Chuyên sâu về lĩnh vực {niche_info}, đúng trọng tâm mặt hàng cần quảng bá."

    # 2. Tag match trong brief -> 90 điểm
    matched_tags = [t for t in data.top_tags if t.lower() in brief]
    if matched_tags:
        return 90.0, f"Thường xuyên chia sẻ nội dung về #{matched_tags[0]}, rất khớp với chủ đề chiến dịch."

    # 3. Category match trong brief -> 85 điểm
    if cat not in IGNORED_NICHES and (cat in brief or any(w in brief for w in cat.split() if len(w) > 2)):
        return 85.0, f"Danh mục {cat} của creator phù hợp trực tiếp với định hướng chiến dịch."

    # 4. Độ liên quan động dựa trên Semantic Similarity Score từ Vector Search
    if data.similarity_score >= 0.65:
        score = round(data.similarity_score * 100.0, 1)
        return score, f"Lĩnh vực {niche_info} có tệp khán giả liên quan mật thiết đến sản phẩm của chiến dịch."

    if data.similarity_score >= 0.40:
        score = round(data.similarity_score * 100.0, 1)
        return score, f"Lĩnh vực {niche_info} có sự tương đồng nhất định với chủ đề chiến dịch."

    # 5. Khác biệt lĩnh vực
    score = round(max(20.0, data.similarity_score * 100.0), 1)
    return score, f"Lĩnh vực {niche_info} có thể giúp mở rộng tiếp cận sang tệp khách hàng tiềm năng mới."


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------
def generate_explanation(data: ExplanationInput) -> ExplanationOutput:
    """
    Sinh câu giải thích AI tại sao Creator này phù hợp với Campaign.
    """
    reasons: list[str] = []
    score_breakdown: dict[str, float] = {}

    # 1. engagementScore
    eng_score, eng_reason = _calculate_engagement_score(
        rate=data.engagement_rate,
        followers=data.followers,
        er_views=data.engagement_rate_views,
        platform=data.platform,
        avg_views=data.avg_views,
        avg_likes=data.avg_likes,
    )
    score_breakdown["engagementScore"] = eng_score
    reasons.append(eng_reason)

    # 2. followerScore
    fol_score, fol_reason = _calculate_follower_score(
        data.followers,
        data.campaign_brief,
        data.target_followers,
        data.min_followers,
        data.max_followers,
    )
    score_breakdown["followerScore"] = fol_score
    reasons.append(fol_reason)

    # 3. semanticMatchScore
    if data.similarity_score > 0:
        sem_score, sem_reason = _calculate_semantic_score(data.similarity_score)
        score_breakdown["semanticMatchScore"] = sem_score
        reasons.append(sem_reason)

    # 4. nicheMatchScore
    niche_score, niche_reason = _calculate_niche_score(data)
    score_breakdown["nicheMatchScore"] = niche_score
    reasons.append(niche_reason)

    # Bổ sung lý do về Creator Type
    type_descriptions = {
        "KOC": "Phong cách đánh giá chân thực, gần gũi, giúp tạo dựng lòng tin với người mua.",
        "KOL": "Độ nhận diện cao, giúp lan tỏa nhanh thông điệp thương hiệu đến cộng đồng.",
        "CELEBRITY": "Tầm ảnh hưởng người nổi tiếng giúp bùng nổ hiệu ứng truyền thông diện rộng.",
    }
    if data.creator_type and data.creator_type in type_descriptions:
        reasons.append(type_descriptions[data.creator_type])

    # Tổng hợp câu Summary tự nhiên, súc tích (1 câu)
    creator_display = data.creator_name or "Creator"
    domain = data.niche if data.niche and data.niche not in IGNORED_NICHES else data.category
    summary = (
        f"{creator_display} là lựa chọn phù hợp nhờ nội dung đúng ngách {domain}, "
        f"tương tác tốt và lượng người theo dõi vừa tầm chiến dịch."
    )

    return ExplanationOutput(
        summary=summary,
        reasons=reasons,
        score_breakdown=score_breakdown,
    )
