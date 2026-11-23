import json
import unittest
from bs4 import BeautifulSoup

from app.crawlers.meta_public_data import facebook_structured_posts, instagram_profile, facebook_plugin
from app.core.vietnam_evidence import vietnam_market_evidence


class MetaPublicDataTest(unittest.TestCase):
    def soup(self, data):
        return BeautifulSoup('<script type="application/json">' + json.dumps(data) + '</script>', 'html.parser')

    def test_facebook_posts_use_own_story_metrics_not_comments_or_shared_stories(self):
        own = {"post_id": "123", "creation_time": 1700000000, "actors": [{"url": "https://www.facebook.com/reviewer"}],
               "permalink_url": "https://www.facebook.com/reviewer/posts/123", "message": {"text": "Review original"},
               "feedback": {"reaction_count": {"count": 14}, "comment_rendering_instance": {"comments": {"total_count": 6}}, "share_count": {"count": 2}},
               "interesting_top_level_comments": [{"reaction_count": {"count": 999}}],
               "attached_story": {"post_id": "999", "creation_time": 1700000000, "actors": [{"url": "https://www.facebook.com/other"}], "message": {"text": "Not this creator"}}}
        posts = facebook_structured_posts(self.soup({"data": [own]}), "reviewer")
        self.assertEqual(len(posts), 1)
        self.assertEqual((posts[0].likes, posts[0].comments, posts[0].shares), (14, 6, 2))
        self.assertEqual(posts[0].platformPostId, "123")
        self.assertIsNone(posts[0].views)

    def test_instagram_profile_identity_not_recommended_account(self):
        data = [{"username": "requested", "follower_count": 12345, "pk": "77"}, {"username": "other", "follower_count": 999999}]
        self.assertEqual(instagram_profile(self.soup(data), "requested")["pk"], "77")

    def test_page_plugin_json_is_parsed_without_running_javascript(self):
        payload = {"props": {"pageID": "77", "timelinePosts": [{"message": "Original", "reactionCount": 3}]}}
        soup = BeautifulSoup('<script>requireLazy([],function(){s.handle(' + json.dumps(payload) + ');});</script>', 'html.parser')
        self.assertEqual(facebook_plugin(soup)["pageID"], "77")

    def test_vietnam_requires_profile_or_repeated_content_evidence(self):
        self.assertFalse(vietnam_market_evidence({"display_name": "Nguyễn Việt Nam", "username": "saigon", "bio": "Lifestyle creator"}, [])['matches'])
        self.assertFalse(vietnam_market_evidence({"bio": "Travel to Vietnam for a holiday"}, [])['matches'])
        self.assertTrue(vietnam_market_evidence({"country_code": "VN", "bio": "Creator"}, [])['matches'])
        self.assertFalse(vietnam_market_evidence({"bio": "Based in Singapore. Visiting Vietnam"}, [])['matches'])

    def test_vietnamese_bio_and_distinct_posts_support_vietnam_market(self):
        bio = "Mình chia sẻ trải nghiệm làm đẹp cùng các bạn"
        posts = [{"caption": "Mình chia sẻ với các bạn trải nghiệm mỹ phẩm này"},
                 {"caption": "Những sản phẩm mình dùng cho các bạn tham khảo"}]
        self.assertTrue(vietnam_market_evidence({"bio": bio}, posts)['matches'])
        self.assertFalse(vietnam_market_evidence({"bio": bio}, [posts[0], posts[0]])['matches'])
        self.assertFalse(vietnam_market_evidence({"bio": "Lifestyle creator"}, [{"caption": "#ViệtNam #làmđẹp"}] * 3)['matches'])

    def test_foreign_country_cannot_be_overridden_by_vietnamese_captions(self):
        evidence = vietnam_market_evidence({"country_code": "US", "bio": "Mình chia sẻ làm đẹp cùng các bạn tại Việt Nam"},
                                          [{"caption": "Mình chia sẻ với các bạn trải nghiệm mỹ phẩm này"}] * 4)
        self.assertFalse(evidence['matches'])

    def test_discovery_market_check_cannot_use_search_snippet(self):
        from unittest.mock import patch
        from app.services.discovery_verification import verify_discovery_profile
        profile = {"unique_id": "creator", "author_id": "123", "followers": 100000, "bio": "International beauty creator"}
        candidate = {"username": "creator", "platform": "INSTAGRAM", "bio_snippet": "Vietnam Hanoi", "search_keyword": "Việt Nam"}
        with patch('app.services.live_fetcher_service.fetch_raw_creator_profile', return_value=(profile, [], None)):
            self.assertFalse(verify_discovery_profile(candidate))
        self.assertEqual(candidate['market_check']['market'], 'UNKNOWN')

    def test_old_foreign_snapshot_cannot_bypass_market_filter(self):
        import asyncio
        from unittest.mock import patch, AsyncMock
        from app.core.models import CreatorInfo, IngestPayload, Platform, Post
        from app.services.collection_service import collect_profile
        payload = IngestPayload(creator=CreatorInfo(platform=Platform.INSTAGRAM, externalId='77', username='creator', displayName='Creator', followers=10000,
            profileUrl='https://www.instagram.com/creator/', bio='Based in Singapore'),
            recentPosts=[Post(platformPostId='123', postUrl='https://www.instagram.com/p/123/', caption='Beauty review')], source='OLD_SNAPSHOT')
        with patch('app.crawlers.instagram_crawler.crawl_instagram_profile', new=AsyncMock(side_effect=RuntimeError('blocked'))), patch(
                'app.services.collection_service.load_snapshot', return_value=payload):
            result = asyncio.run(collect_profile('INSTAGRAM', 'creator', allow_cached=True))
        self.assertEqual(result['status'], 'error')
        self.assertIsNone(result['data'])


if __name__ == '__main__':
    unittest.main()
