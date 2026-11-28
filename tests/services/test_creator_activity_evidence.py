import unittest
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import patch

from bs4 import BeautifulSoup
from app.core.models import CreatorInfo, IngestPayload, Platform, Post
from app.core.post_dates import normalize_post_date
from app.crawlers.facebook_crawler import extract_facebook_posts
from app.mappers.java_mapper import JavaMapper


class CreatorActivityEvidenceTest(unittest.TestCase):
    def post(self, identity, date):
        return Post(platformPostId=identity, postUrl=f"https://example.test/{identity}",
                    caption="Creator post", postedAt=date)

    def test_normalizes_epoch_seconds_milliseconds_offsets_and_naive_dates(self):
        for value in (1704067200, "1704067200000", "2024-01-01", "2024-01-01T07:00:00+07:00"):
            self.assertEqual(normalize_post_date(value), "2024-01-01T00:00:00Z")

    def test_missing_relative_invalid_and_future_dates_remain_unknown(self):
        for value in (None, "", True, "yesterday", "3 days ago", "2024-99-99", "1e50",
                      (datetime.now(UTC) + timedelta(days=1)).isoformat()):
            self.assertIsNone(normalize_post_date(value), value)

    def test_post_model_normalizes_all_crawl_paths_without_fabricating_dates(self):
        self.assertEqual(self.post("known", 1704067200000).postedAt, "2024-01-01T00:00:00Z")
        self.assertIsNone(self.post("unknown", "2 hours ago").postedAt)

    def map_posts(self, posts):
        payload = IngestPayload(creator=CreatorInfo(platform=Platform.TIKTOK, externalId="123",
            username="creator", displayName="Creator", profileUrl="https://example.test/creator", followers=1000),
            source="PUBLIC", recentPosts=posts,
            observedAt=datetime(2024, 1, 1, 7, tzinfo=timezone(timedelta(hours=7))))
        with patch("app.mappers.java_mapper.embedding_service.get_search_embedding", return_value=[0.0] * 768):
            return JavaMapper.from_ingest_payload(payload)

    def test_mapper_rejects_an_embedding_with_wrong_dimensions(self):
        payload = IngestPayload(creator=CreatorInfo(platform=Platform.TIKTOK, externalId="123",
            username="creator", displayName="Creator", profileUrl="https://example.test/creator", followers=1000),
            source="PUBLIC")
        with patch("app.mappers.java_mapper.embedding_service.get_search_embedding", return_value=[]):
            with self.assertRaisesRegex(ValueError, "exactly 768 dimensions"):
                JavaMapper.from_ingest_payload(payload)

    def test_mapper_surfaces_embedding_generation_failure(self):
        payload = IngestPayload(creator=CreatorInfo(platform=Platform.TIKTOK, externalId="123",
            username="creator", displayName="Creator", profileUrl="https://example.test/creator", followers=1000),
            source="PUBLIC")
        with patch("app.mappers.java_mapper.embedding_service.get_search_embedding",
                   side_effect=RuntimeError("model unavailable")):
            with self.assertRaisesRegex(RuntimeError, "model unavailable"):
                JavaMapper.from_ingest_payload(payload)

    def test_pinned_posts_do_not_hide_newer_activity_from_java_snapshot(self):
        posts = [self.post(str(i), f"2024-01-0{i}T00:00:00Z") for i in range(1, 5)]
        mapped = self.map_posts(posts)
        self.assertEqual([p["platformPostId"] for p in mapped["recentPosts"][:3]], ["4", "3", "2"])
        self.assertEqual(mapped["observedAt"], "2024-01-01T00:00:00")
        self.assertEqual(len(mapped["recentPosts"]), 3)
        self.assertEqual([p.platformPostId for p in posts], ["1", "2", "3", "4"])

    def test_undated_evidence_does_not_displace_dated_recent_posts(self):
        mapped = self.map_posts([*[self.post(str(i), "2024-01-01") for i in range(4)],
                                 self.post("undated", None)])
        self.assertNotIn("undated", [p["platformPostId"] for p in mapped["recentPosts"][:3]])
        self.assertEqual(len(mapped["recentPosts"]), 3)

    def test_facebook_uses_explicit_article_timestamps(self):
        for element in ('<time datetime="2024-01-01T07:00:00+07:00"></time>',
                        '<abbr data-utime="1704067200"></abbr>',
                        '<meta itemprop="datePublished" content="2024-01-01T00:00:00Z">'):
            soup = BeautifulSoup(f'<div role="article"><div dir="auto">A genuine creator post with enough caption text</div>'
                                 f'<a href="https://www.facebook.com/creator/posts/123">Post</a>{element}</div>', "html.parser")
            self.assertEqual(extract_facebook_posts(soup, "creator")[0].postedAt, "2024-01-01T00:00:00Z")

    def test_facebook_relative_labels_do_not_become_crawl_time(self):
        soup = BeautifulSoup('<div role="article"><div dir="auto">A creator post with an undated caption</div>'
                             '<a href="https://www.facebook.com/creator/posts/123">Yesterday</a></div>', "html.parser")
        self.assertIsNone(extract_facebook_posts(soup, "creator")[0].postedAt)


if __name__ == "__main__":
    unittest.main()
