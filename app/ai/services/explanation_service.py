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
    avg_views: float = 0.0
    avg_likes: float = 0.0
    similarity_score: float = 0.0       # Score từ vector search (0.0 – 1.0)
    campaign_brief: str = ""            # Brief của campaign (optional)
    content_summary: str = ""           # Bio/summary của creator
    top_tags: list[str] = field(default_factory=list)


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


def _calculate_engagement_score(rate: float, followers: int) -> tuple[float, str]:
    """
    Tiêu chí 1: engagementScore
    So sánh ER thực tế với ER Benchmark chuẩn của Tier.
    """
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


def _calculate_follower_score(followers: int, brief: str = "") -> tuple[float, str]:
    """
    Tiêu chí 2: followerScore
    Đánh giá độ tối ưu của phân khúc quy mô người theo dõi.
    """
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
    Cosine Similarity giữa Creator Profile và Campaign Brief * 100.
    """
    pct = round(similarity_score * 100.0, 1)
    if pct >= 75.0:
        reason = "Nội dung các bài đăng gần đây rất ăn khớp với thông điệp của chiến dịch."
    elif pct >= 50.0:
        reason = "Phong cách nội dung khá phù hợp với định hướng quảng bá của nhãn hàng."
    else:
        reason = "Nội dung có điểm chạm nhất định với chủ đề chiến dịch."
    return pct, reason


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
    eng_score, eng_reason = _calculate_engagement_score(data.engagement_rate, data.followers)
    score_breakdown["engagementScore"] = eng_score
    reasons.append(eng_reason)

    # 2. followerScore
    fol_score, fol_reason = _calculate_follower_score(data.followers, data.campaign_brief)
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
