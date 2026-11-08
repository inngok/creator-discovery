import asyncio
import unittest

from app.ai.services.explanation_service import (
    ExplanationInput,
    _calculate_engagement_score,
    _calculate_follower_score,
    _calculate_semantic_score,
    generate_explanation,
)
from app.api.routers.ai_router import ExplainRequest, explain_creator_recommendation


class TestExplanationService(unittest.TestCase):

    # =========================================================================
    # 1. KIỂM TRA DTO EXPLAIN_REQUEST
    # =========================================================================
    def test_explain_request_dto_fields(self):
        """Kiểm tra DTO ExplainRequest nhận diện đúng cả camelCase và các trường mới."""
        payload = {
            "creatorName": "Linh Đan Skincare",
            "followers": 50000,
            "engagementRate": 4.5,
            "engagementRateViews": 6.2,
            "platform": "TIKTOK",
            "niche": "Beauty",
            "category": "Cosmetics",
            "creatorType": "KOC",
            "avgViews": 100000,
            "avgLikes": 5000,
            "similarityScore": 0.75,
            "campaignBrief": "Cần tìm KOC làm đẹp da dầu mụn",
            "contentSummary": "Chuyên review mỹ phẩm",
            "topTags": ["skincare", "beauty"],
            "minFollowers": 20000,
            "maxFollowers": 80000,
            "targetFollowers": 50000,
        }
        req = ExplainRequest(**payload)
        self.assertEqual(req.creatorName, "Linh Đan Skincare")
        self.assertEqual(req.minFollowers, 20000)
        self.assertEqual(req.maxFollowers, 80000)
        self.assertEqual(req.targetFollowers, 50000)
        self.assertEqual(req.platform, "TIKTOK")
        self.assertEqual(req.avgViews, 100000)
        self.assertEqual(req.avgLikes, 5000)

    # =========================================================================
    # 2. KIỂM CHUẨN _calculate_follower_score()
    # =========================================================================
    def test_follower_score_in_range(self):
        """Trong khoảng [min, max] -> Đạt tuyệt đối 100 điểm."""
        score, reason = _calculate_follower_score(
            followers=50000, min_followers=20000, max_followers=100000
        )
        self.assertEqual(score, 100.0)
        self.assertIn("trọn vẹn trong khoảng mục tiêu", reason)

    def test_follower_score_below_min_asymmetric(self):
        """Thiếu hụt followers so với min -> Phạt dốc hệ số 50, chặn sàn 0đ."""
        # 1. Thiếu vừa phải: 10,000 so với min 50,000 -> 100 - (log10(50000) - log10(10000)) * 50 = 100 - 0.69897 * 50 = 65.1
        score, reason = _calculate_follower_score(
            followers=10000, min_followers=50000, max_followers=150000
        )
        self.assertAlmostEqual(score, 65.1, delta=0.5)
        self.assertIn("thấp hơn mức tối thiểu", reason)

        # 2. Thiếu cực lớn: 100 followers so với min 1,000,000 -> Bị trừ quá 100, chặn sàn 0.0
        score_floor, _ = _calculate_follower_score(
            followers=100, min_followers=1000000, max_followers=2000000
        )
        self.assertEqual(score_floor, 0.0)

    def test_follower_score_above_max_asymmetric(self):
        """Vượt max followers -> Phạt nhẹ phi đối xứng hệ số 30, CHẶN SÀN 60đ."""
        # 1. Vượt vừa phải: 200,000 so với max 100,000 -> 100 - (log10(200000) - log10(100000)) * 30 = 100 - 0.30103 * 30 = 91.0
        score, reason = _calculate_follower_score(
            followers=200000, min_followers=20000, max_followers=100000
        )
        self.assertAlmostEqual(score, 91.0, delta=0.5)
        self.assertIn("vượt mức tối đa", reason)

        # 2. Vượt cực lớn (siêu sao 50M so với max 10k) -> Chặn sàn đúng 60.0 điểm
        score_floor_max, _ = _calculate_follower_score(
            followers=50000000, min_followers=5000, max_followers=10000
        )
        self.assertEqual(score_floor_max, 60.0)

    def test_follower_score_target_exact_and_penalty(self):
        """Kiểm tra khi có target_followers cụ thể."""
        # Khớp chính xác target
        score_exact, _ = _calculate_follower_score(followers=100000, target_followers=100000)
        self.assertEqual(score_exact, 100.0)

        # Vượt target cực lớn -> Chặn sàn 60.0 điểm
        score_over, _ = _calculate_follower_score(followers=10000000, target_followers=50000)
        self.assertEqual(score_over, 60.0)

        # Thấp hơn target cực nhiều -> Chặn sàn 0.0 điểm
        score_under, _ = _calculate_follower_score(followers=500, target_followers=500000)
        self.assertEqual(score_under, 0.0)

    # =========================================================================
    # 3. KIỂM CHUẨN _calculate_engagement_score()
    # =========================================================================
    def test_engagement_score_short_video_platform(self):
        """
        Nền tảng video ngắn (TikTok, Reels, Shorts):
        Tự động chia cho chuẩn 5.0%.
        """
        # 1. TikTok với er_views = 5.0% -> Đạt 100.0 điểm
        score, reason = _calculate_engagement_score(
            rate=2.0, followers=50000, er_views=5.0, platform="TIKTOK"
        )
        self.assertEqual(score, 100.0)
        self.assertIn("vượt chuẩn 5.0% của video ngắn", reason)

        # 2. TikTok với er_views = 2.5% -> 50.0 điểm
        score_half, _ = _calculate_engagement_score(
            rate=1.0, followers=50000, er_views=2.5, platform="TIKTOK"
        )
        self.assertEqual(score_half, 50.0)

        # 3. Reels tự tính từ avg_likes (3,000) / avg_views (50,000) = 6.0% -> Vượt chuẩn 5.0% -> 100.0 điểm
        score_calc, _ = _calculate_engagement_score(
            rate=2.0, followers=50000, er_views=None, platform="INSTAGRAM_REELS",
            avg_views=50000, avg_likes=3000
        )
        self.assertEqual(score_calc, 100.0)

        # 4. Shorts không có views -> Dùng rate (4.0%) chia chuẩn 5.0% = 80.0 điểm
        score_rate_only, _ = _calculate_engagement_score(
            rate=4.0, followers=50000, er_views=None, platform="YOUTUBE_SHORTS"
        )
        self.assertEqual(score_rate_only, 80.0)

    def test_engagement_score_traditional_platform(self):
        """
        Nền tảng truyền thống (Facebook, YouTube, Instagram feed):
        Chia cho Benchmark Tier:
        - Nano (<10k): 4.5%
        - Micro (10k-100k): 3.0%
        - Macro (100k-1M): 2.0%
        - Mega (>=1M): 1.2%
        """
        # 1. Facebook Micro Creator (50k followers, benchmark 3.0%)
        # rate = 3.0% -> 100.0 điểm
        score_fb, _ = _calculate_engagement_score(rate=3.0, followers=50000, platform="FACEBOOK")
        self.assertEqual(score_fb, 100.0)

        # rate = 1.5% -> 50.0 điểm
        score_fb_half, _ = _calculate_engagement_score(rate=1.5, followers=50000, platform="FACEBOOK")
        self.assertEqual(score_fb_half, 50.0)

        # 2. YouTube Macro Creator (500k followers, benchmark 2.0%)
        # rate = 2.0% -> 100.0 điểm
        score_yt, _ = _calculate_engagement_score(rate=2.0, followers=500000, platform="YOUTUBE")
        self.assertEqual(score_yt, 100.0)

        # 3. Instagram Mega Creator (2M followers, benchmark 1.2%)
        # rate = 0.6% -> 50.0 điểm
        score_mega, _ = _calculate_engagement_score(rate=0.6, followers=2000000, platform="INSTAGRAM")
        self.assertEqual(score_mega, 50.0)

    # =========================================================================
    # 4. KIỂM CHUẨN _calculate_semantic_score()
    # =========================================================================
    def test_semantic_score_min_max_rescaling(self):
        """
        Áp dụng Min-Max Rescaling dải [0.30, 0.85] -> [0.0, 100.0]:
        - Cosine <= 0.30 -> 0.0
        - Cosine >= 0.85 -> 100.0
        - Cosine 0.575 -> 50.0
        - Cosine 0.65 -> 63.6
        - Cosine 0.75 -> 81.8
        """
        # Dưới ngưỡng min 0.30
        score_low, _ = _calculate_semantic_score(0.25)
        self.assertEqual(score_low, 0.0)

        # Đúng ngưỡng min 0.30
        score_min, _ = _calculate_semantic_score(0.30)
        self.assertEqual(score_min, 0.0)

        # Đúng ngưỡng max 0.85
        score_max, _ = _calculate_semantic_score(0.85)
        self.assertEqual(score_max, 100.0)

        # Vượt ngưỡng max 0.90
        score_high, _ = _calculate_semantic_score(0.90)
        self.assertEqual(score_high, 100.0)

        # Điểm giữa: 0.575 -> 50.0
        score_mid, _ = _calculate_semantic_score(0.575)
        self.assertAlmostEqual(score_mid, 50.0, delta=0.2)

        # Cosine 0.65 -> 63.6
        score_65, _ = _calculate_semantic_score(0.65)
        self.assertAlmostEqual(score_65, 63.6, delta=0.2)

        # Cosine 0.75 -> 81.8
        score_75, _ = _calculate_semantic_score(0.75)
        self.assertAlmostEqual(score_75, 81.8, delta=0.2)

    # =========================================================================
    # 5. TEST ENDPOINT /api/v1/ai/explain QUA HTTP
    # =========================================================================
    def test_api_explain_endpoint_integration(self):
        """Kiểm tra gọi trực tiếp API /api/v1/ai/explain với payload chuẩn từ Java."""
        payload = {
            "creatorName": "Bác sĩ Nam Da Liễu",
            "followers": 45000,
            "engagementRate": 5.5,
            "engagementRateViews": 5.5,
            "platform": "TIKTOK",
            "niche": "Da Liễu",
            "category": "Health",
            "creatorType": "KOL",
            "avgViews": 80000,
            "avgLikes": 4400,
            "similarityScore": 0.75,
            "campaignBrief": "Chiến dịch da liễu quảng bá kem trị mụn y khoa",
            "contentSummary": "Bác sĩ chuyên khoa da liễu chia sẻ kiến thức",
            "topTags": ["dalieu", "trimun"],
            "minFollowers": 20000,
            "maxFollowers": 100000,
            "targetFollowers": 50000,
        }
        req = ExplainRequest(**payload)
        data = asyncio.run(explain_creator_recommendation(req))
        self.assertEqual(data["status"], "success")
        self.assertIn("summary", data)
        self.assertIn("reasons", data)
        self.assertIn("scoreBreakdown", data)

        breakdown = data["scoreBreakdown"]
        # engagementScore cho TikTok với ER 5.5% / 5.0% -> 100.0
        self.assertEqual(breakdown["engagementScore"], 100.0)
        # followerScore: 45,000 nằm trong [20,000, 100,000] -> 100.0
        self.assertEqual(breakdown["followerScore"], 100.0)
        # semanticMatchScore: Cosine 0.75 rescaled [0.30, 0.85] -> ~81.8
        self.assertAlmostEqual(breakdown["semanticMatchScore"], 81.8, delta=0.2)
        # nicheMatchScore: Lĩnh vực trùng khớp -> >= 85.0
        self.assertGreaterEqual(breakdown["nicheMatchScore"], 85.0)


if __name__ == "__main__":
    unittest.main()
