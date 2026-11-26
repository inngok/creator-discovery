import unittest
from unittest.mock import patch

from app.core.models import CreatorInfo, IngestPayload, Platform, Post
from app.mappers.java_mapper import JavaMapper


class MissingVsObservedZeroTest(unittest.TestCase):
    def _creator(self, followers: int = 10_000) -> CreatorInfo:
        return CreatorInfo(
            platform=Platform.TIKTOK,
            externalId="i02-test-1",
            username="i02_creator",
            displayName="I02 Creator",
            profileUrl="https://example.test/i02_creator",
            followers=followers,
        )

    def test_post_defaults_missing_metrics_to_none_not_zero(self):
        post = Post(platformPostId="p1", postUrl="https://example.test/p1", caption="Photo post")
        self.assertIsNone(post.views)
        self.assertIsNone(post.likes)
        self.assertIsNone(post.comments)
        self.assertIsNone(post.shares)

    def test_post_preserves_explicit_observed_zero(self):
        post = Post(
            platformPostId="p1",
            postUrl="https://example.test/p1",
            caption="Zero post",
            views=0,
            likes=0,
            comments=0,
            shares=0,
        )
        self.assertEqual(post.views, 0)
        self.assertEqual(post.likes, 0)
        self.assertEqual(post.comments, 0)
        self.assertEqual(post.shares, 0)

    def test_all_missing_views_produce_none_aggregate_not_zero(self):
        payload = IngestPayload(
            creator=self._creator(),
            source="PUBLIC",
            recentPosts=[
                Post(platformPostId="p1", postUrl="https://example.test/p1", caption="A", views=None, likes=100),
                Post(platformPostId="p2", postUrl="https://example.test/p2", caption="B", views=None, likes=200),
            ],
        )
        self.assertIsNone(payload.avg_views)
        self.assertIsNone(payload.engagement_rate_views)

        with patch("app.mappers.java_mapper.embedding_service.get_search_embedding", return_value=[]):
            mapped = JavaMapper.from_ingest_payload(payload)

        self.assertIsNone(mapped["avgViews"])
        self.assertIsNone(mapped["engagementRateViews"])
        self.assertEqual([p["views"] for p in mapped["recentPosts"]], [None, None])
        self.assertIsNone(mapped["recentPosts"][0]["comments"])
        self.assertIsNone(mapped["recentPosts"][0]["shares"])

    def test_partial_missing_views_are_not_counted_as_zero_in_denominator(self):
        payload = IngestPayload(
            creator=self._creator(),
            source="PUBLIC",
            recentPosts=[
                Post(platformPostId="p1", postUrl="https://example.test/p1", caption="A", views=100, likes=10),
                Post(platformPostId="p2", postUrl="https://example.test/p2", caption="B", views=None, likes=20),
                Post(platformPostId="p3", postUrl="https://example.test/p3", caption="C", views=200, likes=30),
            ],
        )
        self.assertEqual(payload.avg_views, 150.0)

    def test_observed_zero_views_and_interactions_remain_zero(self):
        payload = IngestPayload(
            creator=self._creator(followers=10_000),
            source="PUBLIC",
            recentPosts=[
                Post(
                    platformPostId="p1",
                    postUrl="https://example.test/p1",
                    caption="Zero video",
                    views=0,
                    likes=0,
                    comments=0,
                    shares=0,
                )
            ],
        )
        self.assertEqual(payload.avg_views, 0.0)
        self.assertEqual(payload.avg_likes, 0.0)
        self.assertEqual(payload.engagement_rate, 0.0)
        self.assertIsNone(payload.engagement_rate_views)

        with patch("app.mappers.java_mapper.embedding_service.get_search_embedding", return_value=[]):
            mapped = JavaMapper.from_ingest_payload(payload)

        self.assertEqual(mapped["avgViews"], 0.0)
        self.assertEqual(mapped["engagementRate"], 0.0)
        self.assertIsNone(mapped["engagementRateViews"])
        self.assertEqual(mapped["recentPosts"][0]["views"], 0)

    def test_missing_er_when_followers_non_positive_or_no_interactions(self):
        no_followers = IngestPayload(
            creator=self._creator(followers=0),
            source="PUBLIC",
            recentPosts=[
                Post(platformPostId="p1", postUrl="https://example.test/p1", caption="A", views=100, likes=10)
            ],
        )
        self.assertIsNone(no_followers.engagement_rate)

        no_interactions = IngestPayload(
            creator=self._creator(followers=10_000),
            source="PUBLIC",
            recentPosts=[
                Post(platformPostId="p1", postUrl="https://example.test/p1", caption="A", views=100)
            ],
        )
        self.assertIsNone(no_interactions.avg_likes)
        self.assertIsNone(no_interactions.engagement_rate)


if __name__ == "__main__":
    unittest.main()
