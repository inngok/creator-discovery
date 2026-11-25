import unittest
import time
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch, mock_open

from app.services.discovery_verification import verify_discovery_profile, matches_requested_topic
from app.services.live_crawl_service import _apply_verified_topic_auto_accept
from app.core.crawl_progress import reset_crawl_progress_callback, set_crawl_progress_callback
from app.crawlers.tiktok_discovery import _run_profile_check, _verify_tiktok_candidate_followers
from app.ai.rules.rule_config import Decision
from app.ai.rules.rule_engine import RuleDecisionResult


class DiscoveryVerificationTest(unittest.TestCase):
    def profile(self, captions, followers=50000):
        return ({"unique_id": "creator", "author_id": "123", "followers": followers,
                 "display_name": "Personal creator", "bio": "Tôi review và chia sẻ trải nghiệm",
                 "profile_url": "https://www.tiktok.com/@creator"},
                [{"platformPostId": str(i), "caption": text} for i, text in enumerate(captions)], None)

    def verify(self, captions, followers=50000):
        candidate = {"username": "creator", "platform": "TIKTOK", "followers": followers}
        with patch("app.services.live_fetcher_service.fetch_raw_creator_profile",
                   return_value=self.profile(captions, followers)) as fetch:
            accepted = _verify_tiktok_candidate_followers(candidate)
            fetch.assert_called_once()
        return accepted, candidate

    def test_popular_personal_user_is_not_a_creator(self):
        accepted, candidate = self.verify([
            "My birthday party", "My friends", "My family", "My pet", "Hello everyone"], followers=900000)
        self.assertFalse(accepted)
        self.assertNotIn("_verified_snapshot", candidate)

    def test_tiktok_topic_lead_without_creator_signals_is_kept_for_review(self):
        events = []
        token = set_crawl_progress_callback(events.append)
        candidate = {
            "username": "creator",
            "platform": "TIKTOK",
            "topic_evidence_text": "Review điện thoại, trải nghiệm camera và pin",
            "search_keyword": "review điện thoại",
        }
        try:
            with patch("app.services.live_fetcher_service.fetch_raw_creator_profile",
                       return_value=self.profile(["Video trải nghiệm điện thoại"] * 5)):
                with patch("app.services.discovery_verification.RuleEngine.evaluate",
                           return_value=SimpleNamespace(
                               decision=Decision.SKIP_INSUFFICIENT,
                               reason_codes=["NO_CREATOR_EVIDENCE"],
                               primary_type="UNKNOWN",
                           )):
                    accepted = _run_profile_check(_verify_tiktok_candidate_followers, candidate)
        finally:
            reset_crawl_progress_callback(token)

        self.assertTrue(accepted)
        self.assertTrue(candidate["requires_manual_review"])
        self.assertEqual(len(candidate["_verified_snapshot"]["posts"]), 5)
        self.assertTrue(any(event["stage"] == "review_candidate" for event in events))

    def test_one_viral_review_does_not_establish_creator(self):
        accepted, _ = self.verify(["Review du lịch ưu điểm đẹp và nhược điểm đông"])
        self.assertFalse(accepted)

    def test_consistent_review_creator_is_verified_even_below_old_7k_threshold(self):
        accepted, candidate = self.verify([
            f"Review du lịch địa điểm {i}, ưu điểm cảnh đẹp, nhược điểm đông người" for i in range(5)
        ], followers=4950)
        self.assertTrue(accepted)
        self.assertEqual(candidate["creator_check"]["primary_type"], "KOC")
        self.assertEqual(len(candidate["_verified_snapshot"]["posts"]), 5)

    def test_fetch_identity_mismatch_is_not_accepted(self):
        data = self.profile(["Review du lịch ưu điểm đẹp"] * 5)
        data[0]["unique_id"] = "someone_else"
        with patch("app.services.live_fetcher_service.fetch_raw_creator_profile", return_value=data):
            self.assertFalse(verify_discovery_profile({"username": "creator", "platform": "TIKTOK"}))

    def test_repeated_post_id_does_not_satisfy_sample_requirement(self):
        data = self.profile(["Review du lịch ưu điểm đẹp nhược điểm đông"] * 5)
        for post in data[1]:
            post["platformPostId"] = "same-post"
        with patch("app.services.live_fetcher_service.fetch_raw_creator_profile", return_value=data):
            self.assertFalse(verify_discovery_profile({"username": "creator", "platform": "TIKTOK"}))

    def topic(self, answer, posts=None, requires_manual_review=False):
        candidate = {
            "topic_evidence_posts": posts or [
                {"postId": "1", "caption": "Travel mountain hiking guide"},
                {"postId": "2", "caption": "Travel resort review and costs"}],
            "requires_manual_review": requires_manual_review,
        }
        fake = SimpleNamespace(get_llm_enrichment=lambda _: answer)
        with patch.dict("sys.modules", {"app.ai.analyzer": fake}):
            return matches_requested_topic(candidate, "travel")

    @staticmethod
    def answer():
        return {"matches": True, "creatorType": "KOC", "confidence": 0.95,
                "topicEvidence": [{"postId": "1", "quote": "Travel mountain hiking guide"},
                                  {"postId": "2", "quote": "Travel resort review and costs"}]}

    def test_topic_requires_multiple_grounded_posts(self):
        self.assertTrue(self.topic(self.answer()))
        answer = self.answer()
        answer["topicEvidence"] = answer["topicEvidence"][:1]
        self.assertFalse(self.topic(answer))

    def test_fabricated_topic_or_user_classification_cannot_pass(self):
        for field, value in [("creatorType", "USER"), ("confidence", 0.2), ("confidence", True),
                             ("topicEvidence", [{"postId": "1", "quote": "invented mountain adventure"},
                                                {"postId": "2", "quote": "invented resort visit"}])]:
            answer = self.answer()
            answer[field] = value
            self.assertFalse(self.topic(answer))

    def test_ambiguous_unknown_creator_cannot_be_auto_accepted_by_topic_match(self):
        answer = self.answer()
        answer.update(creatorType="UNKNOWN", confidence=0.7)
        self.assertFalse(self.topic(answer, requires_manual_review=True))
        self.assertFalse(self.topic(answer))

    def test_rule_review_candidate_is_auto_accepted_after_grounded_topic_check(self):
        answer = self.answer()
        answer.update(creatorType="KOL", confidence=0.9)
        candidate = {
            "topic_evidence_posts": [
                {"postId": "1", "caption": "Travel mountain hiking guide"},
                {"postId": "2", "caption": "Travel resort review and costs"},
            ],
            "requires_manual_review": True,
        }
        fake = SimpleNamespace(get_llm_enrichment=lambda _: answer)
        with patch.dict("sys.modules", {"app.ai.analyzer": fake}):
            self.assertTrue(matches_requested_topic(candidate, "travel"))

        self.assertEqual(candidate["topic_check"]["creator_type"], "KOL")

    def test_grounded_topic_override_resolves_review_but_never_hard_reject(self):
        result = RuleDecisionResult(
            decision=Decision.NEEDS_MANAGER_REVIEW,
            primary_type="UNKNOWN",
            reason_codes=["BR05_DUAL_KOC_KOL_AMBIGUITY"],
        )
        snapshot = {
            "posts": [
                {"platformPostId": "1", "caption": "Travel mountain hiking guide"},
                {"platformPostId": "2", "caption": "Travel resort review and costs"},
            ],
            "topic_auto_accept": {
                "creator_type": "KOL",
                "confidence": 0.9,
                "evidence": [
                    {"postId": "1", "quote": "Travel mountain hiking guide"},
                    {"postId": "2", "quote": "Travel resort review and costs"},
                ],
            },
        }
        accepted = _apply_verified_topic_auto_accept(result, snapshot)
        self.assertEqual(accepted.decision, Decision.AUTO_ACCEPT_KOL)
        self.assertEqual(accepted.primary_type, "KOL")
        self.assertIn("DISCOVERY_TOPIC_AUTO_ACCEPT", accepted.reason_codes)

        rejected = result.model_copy(update={"decision": Decision.AUTO_REJECT})
        self.assertEqual(_apply_verified_topic_auto_accept(rejected, snapshot), rejected)

    def test_search_card_alone_is_not_topic_evidence(self):
        fake = SimpleNamespace(get_llm_enrichment=lambda _: self.answer())
        with patch.dict("sys.modules", {"app.ai.analyzer": fake}):
            self.assertFalse(matches_requested_topic({"topic_evidence_text": "Travel guide"}, "travel"))

    def test_model_user_disagreement_on_proven_creator_is_reviewed_not_auto_accepted_or_rejected(self):
        from app.services.live_sync_service import sync_creator_to_java_backend
        from app.core.models import AIEnrichment
        profile, posts, _ = self.profile(["Review travel guide"] * 5)
        decision = RuleDecisionResult(decision=Decision.AUTO_ACCEPT_KOC, primary_type="KOC",
                                      evidence={"posts_count": 5})
        fake = SimpleNamespace(process_ai_enrichment=lambda *_: AIEnrichment(creatorType="USER"))
        payload = {"dataConfidence": 0.9}
        with ExitStack() as stack:
            stack.enter_context(patch.dict("sys.modules", {"app.ai.analyzer": fake}))
            stack.enter_context(patch("app.services.live_sync_service.staging_repo"))
            stack.enter_context(patch("app.services.live_sync_service.append_to_crawled_results"))
            stack.enter_context(patch("app.services.live_sync_service.JavaMapper.from_ingest_payload", return_value=payload))
            send = stack.enter_context(patch("app.services.live_sync_service.java_client.send_creator_data", return_value=True))
            stack.enter_context(patch("builtins.open", mock_open()))
            _, _, result = sync_creator_to_java_backend("TIKTOK", profile, posts, decision)
        self.assertEqual(result.decision, Decision.NEEDS_MANAGER_REVIEW)
        self.assertIn("AI_CREATOR_TYPE_CONFLICT", result.reason_codes)
        self.assertLessEqual(send.call_args.args[0]["dataConfidence"], 0.65)

    def test_processing_reuses_verified_posts_instead_of_fetching_a_different_sample(self):
        from app.services.live_crawl_service import crawl_single_koc_live
        profile, posts, _ = self.profile([
            f"Review du lịch địa điểm {i}, ưu điểm cảnh đẹp, nhược điểm đông" for i in range(5)])
        profile.update(following=10, heart_count=1000, video_count=20, avatar_url="",
                       is_verified=False, latency_ms=10)
        snapshot = {"platform": "TIKTOK", "username": "creator", "profile": profile,
                    "posts": posts, "verified_at": time.time()}
        with ExitStack() as stack:
            staging = stack.enter_context(patch("app.services.live_crawl_service.staging_repo"))
            staging.get_candidate.return_value = None
            fetch = stack.enter_context(patch("app.services.live_crawl_service.fetch_raw_creator_profile"))
            stack.enter_context(patch("app.services.live_crawl_service.append_to_crawled_results"))
            stack.enter_context(patch("app.services.live_crawl_service.metrics_tracker"))
            stack.enter_context(patch("app.services.live_crawl_service.sync_creator_to_java_backend",
                side_effect=lambda **kwargs: (True, None, kwargs["decision_result"])))
            result = crawl_single_koc_live("creator", "TIKTOK", verified_snapshot=snapshot)
        fetch.assert_not_called()
        self.assertEqual(result["recent_posts"], posts)
        self.assertEqual(result["decision"], "AUTO_ACCEPT_KOC")

    def test_topic_verified_candidate_is_auto_accepted_after_live_rule_review(self):
        from app.services.live_crawl_service import crawl_single_koc_live
        profile, posts, _ = self.profile([
            "Travel mountain hiking guide with trail details",
            "Travel resort review and complete costs",
            "My travel video",
            "Travel planning notes",
            "Travel trip recap",
        ])
        profile.update(following=10, heart_count=1000, video_count=20, avatar_url="",
                       is_verified=False, latency_ms=10)
        review_result = RuleDecisionResult(
            decision=Decision.NEEDS_MANAGER_REVIEW,
            primary_type="UNKNOWN",
            reason_codes=["BR05_DUAL_KOC_KOL_AMBIGUITY"],
        )
        snapshot = {
            "platform": "TIKTOK",
            "username": "creator",
            "profile": profile,
            "posts": posts,
            "verified_at": time.time(),
            "topic_auto_accept": {
                "creator_type": "KOL",
                "confidence": 0.9,
                "evidence": [
                    {"postId": "0", "quote": "Travel mountain hiking guide"},
                    {"postId": "1", "quote": "Travel resort review and complete costs"},
                ],
            },
        }
        from app.core.models import AIEnrichment
        fake_ai = SimpleNamespace(
            get_llm_enrichment=lambda _: {},
            process_ai_enrichment=lambda *_: AIEnrichment(creatorType="KOL"),
        )
        with ExitStack() as stack:
            staging = stack.enter_context(patch("app.services.live_crawl_service.staging_repo"))
            staging.get_candidate.return_value = None
            stack.enter_context(patch("app.services.live_crawl_service.append_to_crawled_results"))
            stack.enter_context(patch("app.services.live_crawl_service.metrics_tracker"))
            stack.enter_context(patch("app.services.live_sync_service.staging_repo", staging))
            stack.enter_context(patch("app.services.live_sync_service.append_to_crawled_results"))
            stack.enter_context(patch(
                "app.services.live_crawl_service.RuleEngine.evaluate",
                return_value=review_result,
            ))
            stack.enter_context(patch.dict("sys.modules", {"app.ai.analyzer": fake_ai}))
            stack.enter_context(patch(
                "app.services.ai_candidate_review.review_candidate",
                return_value=review_result,
            ))
            stack.enter_context(patch(
                "app.services.live_sync_service.JavaMapper.from_ingest_payload",
                return_value={"dataConfidence": 0.9},
            ))
            send_to_database = stack.enter_context(patch(
                "app.services.live_sync_service.java_client.send_creator_data",
                return_value=True,
            ))
            stack.enter_context(patch("builtins.open", mock_open()))
            result = crawl_single_koc_live("creator", "TIKTOK", verified_snapshot=snapshot)

        self.assertEqual(result["decision"], Decision.AUTO_ACCEPT_KOL.value)
        self.assertTrue(result["database_synced"])
        send_to_database.assert_called_once()
