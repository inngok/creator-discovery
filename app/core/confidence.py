from datetime import datetime, timezone
import re

SOURCE_WEIGHTS = {
    # 1. API chính thức: 1.00
    "OFFICIAL_API": 1.00,
    "API": 1.00,
    "TIKTOK_API": 1.00,
    "INSTAGRAM_GRAPH_API": 1.00,
    "YOUTUBE_DATA_API": 1.00,
    # 2. Creator tự xác minh / OAuth: 0.85
    "AUTHORIZED": 0.85,
    "CLAIMED": 0.85,
    "OAUTH": 0.85,
    # 3. Cào Web public / Import: 0.70
    "PUBLIC": 0.70,
    "WEB_CRAWL": 0.70,
    "PLAYWRIGHT_STEALTH": 0.70,
    "EXCEL_IMPORT": 0.70,
    "PLAYWRIGHT_STEALTH_TIKTOK": 0.70,
}

CONTACT_REGEX = re.compile(
    r"(?i)(booking|contact|zalo|liên hệ|work|hợp tác|[\w\.-]+@[\w\.-]+\.\w+|0\d{9,10})"
)


def compute_data_confidence(
    source_type: str = "PUBLIC",
    observed_at=None,
    avatar_url: str = "",
    bio: str = "",
    followers: int = 0,
    avg_views: float = 0.0,
    avg_likes: float = 0.0,
    category: str = "",
    niche: str = "",
    contact: str = "",
    recent_posts: list = None,
    er: float = 0.0,
) -> float:
    """
    Data Confidence = (35% x Nguồn) + (30% x Độ tươi) + (20% x Độ đầy đủ) + (15% x Hợp lý)
    """
    posts = recent_posts or []
    bio_text = str(bio or "").strip()

    # 1. Nguồn dữ liệu (35%) - Dùng dictionary lookup thay vì if-else
    src_key = str(source_type or "").upper()
    source_score = SOURCE_WEIGHTS.get(src_key, 0.40)

    # 2. Độ tươi (30%) - Tính toán khoảng cách ngày
    freshness_score = 1.00
    if observed_at:
        try:
            dt = datetime.fromisoformat(observed_at.replace("Z", "+00:00")) if isinstance(observed_at, str) else observed_at
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            days = max(0, (datetime.now(timezone.utc) - dt).days)
            freshness_score = 1.00 if days <= 7 else (round(1.0 - 0.5 * ((days - 7) / 23.0), 2) if days <= 30 else 0.20)
        except Exception:
            freshness_score = 1.00

    # 3. Độ đầy đủ (20%) - Tỷ lệ 7 trường cốt lõi không rỗng
    has_contact = bool(contact or CONTACT_REGEX.search(bio_text))
    completeness_fields = [
        bool(avatar_url and str(avatar_url).strip()),
        bool(bio_text),
        followers > 0,
        avg_views > 0,
        avg_likes > 0,
        category not in ["", "UNKNOWN", "Khác"] and niche not in ["", "UNKNOWN", "General"],
        has_contact,
    ]
    completeness_score = sum(completeness_fields) / len(completeness_fields)

    # 4. Tính hợp lý (15%) - Phát hiện chỉ số ảo/buff ảo nhưng không phạt oan KOC viral tự nhiên
    # Trên TikTok/Reels, video viral có thể đạt lượt xem gấp hàng trăm lần followers (ví dụ 2k followers, 300k views).
    # Chỉ coi là view ảo / bot buff nếu lượt xem cực cao mà lượt like/tương tác bằng 0 hoặc dưới 0.1% views.
    er_views = ((avg_likes / avg_views) * 100.0) if avg_views > 0 else 0.0
    is_fake_engagement = (
        (avg_views > 0 and er_views > 50.0)
        or (avg_views == 0 and er > 100.0)
        or (bool(posts) and avg_views > 0 and avg_likes == 0 and er == 0.0)
    )
    is_fake_views = (
        (followers > 1000 and avg_views > followers * 1000 and avg_likes < avg_views * 0.001)
        or (avg_views > 5000 and avg_likes == 0)
        or (avg_views == 0 and avg_likes > 1000)
    )
    anomalies = [
        is_fake_engagement,
        is_fake_views,
        followers <= 0,
        not posts,
    ]
    sanity_score = max(0.0, 1.0 - sum(anomalies) * 0.25)

    # Tổng trọng số chuẩn hóa [0.0, 1.0]
    total_conf = (
        0.35 * source_score
        + 0.30 * freshness_score
        + 0.20 * completeness_score
        + 0.15 * sanity_score
    )
    return round(min(max(total_conf, 0.0), 1.0), 2)
