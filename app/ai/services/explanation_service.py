"""
AI Explanation Service
Sinh câu giải thích tại sao một Creator được đề xuất cho một Campaign.
Dựa trên các thành phần điểm cao nhất (engagement, niche match, follower tier...).
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
# Helper: phân loại follower tier thành label dễ đọc
# ---------------------------------------------------------------------------
def _tier_label(followers: int) -> str:
    if followers < 10_000:
        return "Nano Influencer"
    elif followers < 100_000:
        return "Micro Influencer"
    elif followers < 1_000_000:
        return "Macro Influencer"
    else:
        return "Mega Influencer / KOL"


def _engagement_label(rate: float) -> tuple[str, float]:
    """Trả về (nhãn, điểm 0-100)."""
    if rate >= 10.0:
        return "rất cao", 100.0
    elif rate >= 5.0:
        return "cao", 80.0
    elif rate >= 2.0:
        return "trung bình", 55.0
    elif rate >= 1.0:
        return "thấp", 35.0
    else:
        return "rất thấp", 15.0


def _similarity_label(score: float) -> tuple[str, float]:
    """score trong [0, 1] → nhãn + điểm 0-100."""
    pct = score * 100
    if pct >= 85:
        return "rất phù hợp", pct
    elif pct >= 65:
        return "khá phù hợp", pct
    elif pct >= 45:
        return "phù hợp một phần", pct
    else:
        return "ít phù hợp", pct


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------
def generate_explanation(data: ExplanationInput) -> ExplanationOutput:
    """
    Sinh câu giải thích AI tại sao Creator này phù hợp với Campaign.

    Args:
        data: ExplanationInput chứa thông tin creator và campaign.

    Returns:
        ExplanationOutput gồm summary, reasons, score_breakdown.
    """
    reasons: list[str] = []
    score_breakdown: dict[str, float] = {}

    # ── 1. Engagement Rate ──────────────────────────────────────────────────
    eng_label, eng_score = _engagement_label(data.engagement_rate)
    score_breakdown["engagementScore"] = round(eng_score, 1)
    reasons.append(
        f"Engagement rate {data.engagement_rate:.1f}% ({eng_label}), "
        f"cho thấy cộng đồng theo dõi tích cực."
    )

    # ── 2. Follower Tier ────────────────────────────────────────────────────
    tier = _tier_label(data.followers)
    tier_score = min(100.0, (data.followers / 1_000_000) * 100)
    score_breakdown["followerScore"] = round(tier_score, 1)
    reasons.append(
        f"Là {tier} với {data.followers:,} followers — "
        f"phù hợp với quy mô chiến dịch."
    )

    # ── 3. Semantic Similarity (nếu có) ─────────────────────────────────────
    if data.similarity_score > 0:
        sim_label, sim_score = _similarity_label(data.similarity_score)
        score_breakdown["semanticMatchScore"] = round(sim_score, 1)
        reasons.append(
            f"Nội dung creator {sim_label} với mô tả chiến dịch "
            f"(độ tương đồng ngữ nghĩa: {sim_score:.0f}%)."
        )

    # ── 4. Niche / Category Match ───────────────────────────────────────────
    niche_info = f"{data.category} / {data.niche}" if data.niche else data.category
    score_breakdown["nicheMatchScore"] = 85.0  # Rule-based, cải thiện sau
    reasons.append(
        f"Chuyên sâu về lĩnh vực {niche_info}, "
        f"tương thích trực tiếp với mục tiêu chiến dịch."
    )

    # ── 5. Creator Type ─────────────────────────────────────────────────────
    if data.creator_type and data.creator_type != "UNKNOWN":
        reasons.append(
            f"Được phân loại là {data.creator_type} — "
            f"phù hợp với chiến lược nội dung của thương hiệu."
        )

    # ── 6. Top Tags (nếu có) ────────────────────────────────────────────────
    if data.top_tags:
        tags_str = ", ".join(f"#{t}" for t in data.top_tags[:5])
        reasons.append(
            f"Các chủ đề nổi bật: {tags_str}."
        )

    # ── 7. Avg Views ────────────────────────────────────────────────────────
    if data.avg_views > 0:
        score_breakdown["avgViewScore"] = min(100.0, (data.avg_views / 100_000) * 100)
        reasons.append(
            f"Trung bình {data.avg_views:,.0f} lượt xem mỗi bài đăng, "
            f"đảm bảo khả năng tiếp cận cao."
        )

    # ── Tổng hợp Summary ────────────────────────────────────────────────────
    top_reason = reasons[0] if reasons else "Creator phù hợp với chiến dịch."
    summary = (
        f"{data.creator_name or 'Creator này'} được đề xuất vì {top_reason.lower()} "
        f"Với chuyên môn trong lĩnh vực {data.niche or data.category}, "
        f"creator có tiềm năng tiếp cận đúng tệp khách hàng mục tiêu."
    )

    return ExplanationOutput(
        summary=summary,
        reasons=reasons,
        score_breakdown=score_breakdown,
    )
