import json
import unittest
from datetime import datetime, UTC
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.crawlers.tiktok_crawler import _setup_network_interception
from app.crawlers import preflight_fetcher
from app.services.ai_candidate_review import review_candidate
from app.ai.rules.rule_config import Decision, ProcessingStatus
from app.core.deduplicator import should_enqueue_preflight
from app.ai.rules.rule_engine import RuleDecisionResult, RuleEngine
from app.ai.rules.signal_extractor import ProfileMetadata, PostPreview


class TikTokPostCollectionTest(unittest.TestCase):
    def test_old_extraction_rejection_can_be_rechecked_without_cooldown(self):
        record = SimpleNamespace(
            decision=Decision.AUTO_REJECT, processing_status=ProcessingStatus.FAILED,
            last_checked_at=datetime.now(UTC).isoformat(),
            evidence_summary={"posts_count": 0},
            reason_codes=["POST_VALIDATION_NOT_KOC_KOL"])
        with patch("app.core.deduplicator.staging_repo.get_candidate", return_value=record):
            self.assertTrue(should_enqueue_preflight("TIKTOK", "creator")[0])
            record.reason_codes = ["BR01_REJECT_COMMERCIAL_STORE"]
            self.assertFalse(should_enqueue_preflight("TIKTOK", "creator")[0])

    def test_working_creator_without_literal_review_captions_is_referred_not_dropped(self):
        profile = ProfileMetadata(username="family_daily", followers=133200,
                                  bio="Contact for work. Xem full video unbox tại kênh")
        posts = [PostPreview(post_id=str(i), caption=caption) for i, caption in enumerate([
            "Muốn có tóc đẹp là phải chăm sóc mỗi ngày",
            "Chân láng mịn mà lại nhanh gọn lẹ",
            "Bạn nào chưa thử thì hãy thử ngay nha",
            "Khi mẹ mua nước mà quên hỏi con thích vị nào",
            "Mới hôm nào còn là cô bé bốn tuổi",
        ])]
        result = RuleEngine.evaluate(profile, posts)
        self.assertEqual(result.decision, Decision.NEEDS_MANAGER_REVIEW)

    def test_nano_creator_uses_configured_follower_threshold(self):
        result = RuleEngine.evaluate(ProfileMetadata(
            username="nano_creator", display_name="Personal creator",
            bio="Review skincare chia sẻ trải nghiệm", followers=4950), [])
        self.assertNotEqual(result.decision, Decision.AUTO_REJECT)
        self.assertNotIn("TIER0_REJECT_LOW_FOLLOWERS", result.reason_codes)

    def test_partial_network_evidence_is_supplemented_by_ssr_and_owned_dom(self):
        page = MagicMock()
        context = MagicMock()
        context.new_page.return_value = page
        response = MagicMock(url="https://www.tiktok.com/api/post/item_list/", status=200)
        response.text.return_value = json.dumps({"itemList": [
            {"id": "1", "desc": "Network one"}, {"id": "2", "desc": "Network two"}]})
        page.goto.side_effect = lambda *a, **k: page.on.call_args.args[1](response)
        ssr = {"__DEFAULT_SCOPE__": {"webapp.user-detail": {"itemList": [
            {"id": "2", "desc": "Duplicate"}, {"id": "3", "desc": "SSR three"},
            {"id": "4", "desc": "SSR four"}]}}}
        page.content.return_value = (
            '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">' + json.dumps(ssr) + '</script>'
            '<a href="/@traveler/video/5">Owned travel guide</a>'
            '<a href="/@other/video/6">Unrelated recommended video</a>')
        with patch.object(preflight_fetcher, "sync_playwright"), \
             patch.object(preflight_fetcher, "_init_playwright_context", return_value=(context, "")), \
             patch.object(preflight_fetcher, "Stealth"):
            data = preflight_fetcher.fetch_tiktok_ssr_raw_data("traveler")
        self.assertEqual([p["id"] for p in data["itemList"]], ["1", "2", "3", "4", "5"])
        context.browser.close.assert_called_once()

    def test_collects_enough_owned_posts_for_preflight_across_responses(self):
        page = MagicMock()
        posts = _setup_network_interception(page, "traveler")
        callback = page.on.call_args.args[1]
        for offset in (0, 3, 6):
            response = MagicMock(url="https://www.tiktok.com/api/post/item_list/", status=200)
            response.text.return_value = json.dumps({"itemList": [
                {"id": str(i), "author": {"uniqueId": "traveler"}, "desc": "Travel guide"}
                for i in range(offset, offset + 3)]})
            callback(response)
        self.assertEqual(len(posts), 9)
        self.assertEqual(len({p.platformPostId for p in posts}), 9)

    def test_recommendations_and_other_authors_are_not_profile_evidence(self):
        page = MagicMock()
        posts = _setup_network_interception(page, "traveler")
        callback = page.on.call_args.args[1]
        response = MagicMock(url="https://www.tiktok.com/api/recommend/item_list/", status=200)
        response.text.return_value = json.dumps({"itemList": [{"id": "1", "desc": "Other video"}]})
        callback(response)
        response.url = "https://www.tiktok.com/api/post/item_list/"
        response.text.return_value = json.dumps({"itemList": [
            {"id": "2", "author": {"uniqueId": "other"}, "desc": "Other video"}]})
        callback(response)
        self.assertEqual(posts, [])

    def test_sparse_ai_evidence_preserves_rule_referral_to_manager(self):
        result = RuleDecisionResult(decision=Decision.NEEDS_MANAGER_REVIEW,
                                    primary_type="UNKNOWN", reason_codes=["KOC_POTENTIAL"])
        llm = MagicMock()
        reviewed = review_candidate(ProfileMetadata(username="traveler", followers=25000),
                                    [PostPreview(caption="Review mountain trip")], result, llm)
        self.assertEqual(reviewed.decision, Decision.NEEDS_MANAGER_REVIEW)
        self.assertEqual(reviewed.reason_codes, ["KOC_POTENTIAL"])
        llm.assert_not_called()
