import math
import unittest
from unittest.mock import patch

from app.core.models import CreatorInfo, IngestPayload, Platform, Post
from app.mappers.java_mapper import JavaMapper


class ErUnitContractRegressionTest(unittest.TestCase):
    def _build_payload(self, followers: int, likes: int, views: int) -> IngestPayload:
        return IngestPayload(
            creator=CreatorInfo(
                platform=Platform.TIKTOK,
                externalId="er-unit-test-1",
                username="er_unit_creator",
                displayName="ER Unit Creator",
                profileUrl="https://example.test/er_unit_creator",
                followers=followers,
            ),
            source="PUBLIC",
            recentPosts=[
                Post(
                    platformPostId="post-1",
                    postUrl="https://example.test/post-1",
                    caption="Test post",
                    views=views,
                    likes=likes,
                    comments=0,
                    shares=0,
                    postedAt="2026-11-01T00:00:00Z",
                )
            ],
        )

    def test_java_mapper_converts_percentage_er_to_canonical_decimal_ratio(self):
        # 500 likes / 10,000 followers = 5.0% in IngestPayload -> must map to 0.05 for Java BE
        payload = self._build_payload(followers=10_000, likes=500, views=20_000)
        self.assertAlmostEqual(payload.engagement_rate, 5.0, places=6)
        self.assertAlmostEqual(payload.engagement_rate_views, 2.5, places=6)

        with patch("app.mappers.java_mapper.embedding_service.get_search_embedding", return_value=[]):
            java_payload = JavaMapper.from_ingest_payload(payload)

        self.assertAlmostEqual(java_payload["engagementRate"], 0.05, places=6)
        self.assertAlmostEqual(java_payload["engagementRateViews"], 0.025, places=6)

    def test_java_mapper_preserves_zero_er_without_error(self):
        payload = self._build_payload(followers=10_000, likes=0, views=20_000)
        with patch("app.mappers.java_mapper.embedding_service.get_search_embedding", return_value=[]):
            java_payload = JavaMapper.from_ingest_payload(payload)

        self.assertEqual(java_payload["engagementRate"], 0.0)
        self.assertEqual(java_payload["engagementRateViews"], 0.0)

    def test_java_mapper_handles_missing_or_non_finite_er_safely(self):
        payload = self._build_payload(followers=0, likes=500, views=0)
        with patch("app.mappers.java_mapper.embedding_service.get_search_embedding", return_value=[]):
            java_payload = JavaMapper.from_ingest_payload(payload)

        self.assertIsNone(java_payload["engagementRate"])


if __name__ == "__main__":
    unittest.main()
