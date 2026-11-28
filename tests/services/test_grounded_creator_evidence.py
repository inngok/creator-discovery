import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from app.ai.grounded_enrichment import build_content_summary, resolve_grounded_fallback_labels, INSUFFICIENT_SUMMARY
from app.ai.creator_classifier import classify_creator_type_smart


class GroundedCreatorEvidenceTest(unittest.TestCase):
    def test_pet_and_home_labels_are_accepted_with_the_same_contract_as_discovery(self):
        for code, source in [("PETS", "Review thức ăn dành riêng cho mèo nhà mình"),
                             ("HOME", "Review máy hút bụi dùng trong căn bếp nhỏ")]:
            answer = {"categoryFallback": code, "labelEvidence": [source],
                      "nicheLabels": [{"label": code, "evidence": [source]}]}
            self.assertEqual(resolve_grounded_fallback_labels("UNKNOWN", "UNKNOWN", answer, [source]), (code, code))

    def test_summary_uses_actual_skincare_captions_not_invented_travel(self):
        caption = "Review kem chống nắng cho da dầu và cách dùng mỗi ngày"
        summary = build_content_summary("Booking: person@example.test", [caption])
        self.assertIn(caption, summary)
        self.assertNotIn("du lịch", summary)
        self.assertNotIn("example.test", summary)

    def test_contact_only_profile_does_not_become_lifestyle_evidence(self):
        self.assertEqual(build_content_summary("person@example.test", []), INSUFFICIENT_SUMMARY)

    def test_fallback_labels_require_real_source_quotes(self):
        source = "Tôi chia sẻ cách chăm sóc da và review kem chống nắng"
        answer = {"categoryFallback": "BEAUTY", "nicheFallback": "Chăm sóc da"}
        self.assertEqual(resolve_grounded_fallback_labels("UNKNOWN", "UNKNOWN", answer, [source]),
                         ("UNKNOWN", "UNKNOWN"))
        answer["labelEvidence"] = ["Nội dung du lịch không tồn tại ở nguồn"]
        self.assertEqual(resolve_grounded_fallback_labels("UNKNOWN", "UNKNOWN", answer, [source]),
                         ("UNKNOWN", "UNKNOWN"))
        answer["labelEvidence"] = [source]
        self.assertEqual(resolve_grounded_fallback_labels("UNKNOWN", "UNKNOWN", answer, [source]),
                         ("BEAUTY", "Chăm sóc da"))

    def test_known_brand_cannot_be_overridden_by_affiliate_or_review_signals(self):
        self.assertEqual(classify_creator_type_smart(
            username="brand", bio="review sản phẩm link shopee.vn", followers=200000,
            classified_type="BRAND"), "BRAND")
        self.assertEqual(classify_creator_type_smart(
            username="brand", bio="Official brand account. review sản phẩm shopee.vn", followers=200000), "BRAND")

    def test_unclassified_account_is_not_automatically_koc(self):
        self.assertEqual(classify_creator_type_smart(username="plain", followers=200000,
                         bio="Chào mừng bạn đến với kênh"), "UNKNOWN")

    def test_creator_reviewing_a_brand_is_not_the_brand(self):
        self.assertEqual(classify_creator_type_smart(username="person", bio="Tôi review sản phẩm",
                         recent_posts=[{"caption": "Review official brand account"}]), "UNKNOWN")

    def test_real_affiliate_reviewer_still_qualifies(self):
        self.assertEqual(classify_creator_type_smart(
            username="reviewer", bio="Tôi review và trải nghiệm sản phẩm. shopee.vn",
            recent_posts=[{"caption": "Mình review kem chống nắng sau một tuần sử dụng"}]), "KOC")


class AnalyzerGroundingIntegrationTest(unittest.TestCase):
    def test_local_llm_selection_never_falls_back_to_cloud_providers(self):
        from app.ai import analyzer
        response = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='{"category":"FOOD"}'))])
        with patch.dict("os.environ", {
            "LOCAL_LLM_MODEL": "qwen3:4b",
            "OLLAMA_API_BASE": "http://127.0.0.1:11434",
            "GEMINI_API_KEY": "must-not-be-used",
            "GROQ_API_KEY": "must-not-be-used",
            "OPENAI_API_KEY": "must-not-be-used",
        }), patch.object(analyzer.litellm, "completion", return_value=response) as completion:
            result = analyzer.get_llm_enrichment("local test prompt")

        self.assertEqual(result, {"category": "FOOD"})
        self.assertEqual(completion.call_count, 1)
        self.assertEqual(completion.call_args.kwargs["model"], "ollama/qwen3:4b")
        self.assertEqual(completion.call_args.kwargs["api_base"], "http://127.0.0.1:11434")
        self.assertFalse(completion.call_args.kwargs["think"])
        self.assertEqual(completion.call_args.kwargs["temperature"], 0)
        self.assertNotIn("fallbacks", completion.call_args.kwargs)

    def test_local_llm_failure_does_not_send_prompt_to_cloud(self):
        from app.ai import analyzer
        with patch.dict("os.environ", {
            "LOCAL_LLM_MODEL": "qwen3:4b",
            "GEMINI_API_KEY": "must-not-be-used",
            "GROQ_API_KEY": "must-not-be-used",
            "OPENAI_API_KEY": "must-not-be-used",
        }), patch.object(analyzer.litellm, "completion", side_effect=RuntimeError("offline")) as completion:
            result = analyzer.get_llm_enrichment("private caption")

        self.assertIsNone(result)
        self.assertEqual(completion.call_count, 1)
        self.assertEqual(completion.call_args.kwargs["model"], "ollama/qwen3:4b")

    def enrich(self, creator_type, with_quotes):
        from app.ai import analyzer
        from app.core.models import CreatorInfo
        caption = "Review kem chống nắng cho da dầu sau một tuần sử dụng"
        profile = CreatorInfo(platform="TIKTOK", externalId="fixture", username="fixture",
                              displayName="Fixture", profileUrl="https://example.test/fixture",
                              followers=200000, bio="Official brand account")
        answer = {"summary": "Kênh du lịch trekking khám phá thiên nhiên",
                  "categoryFallback": "BEAUTY", "nicheFallback": "Chăm sóc da",
                  "creatorTypeFallback": "KOC"}
        if with_quotes:
            answer["labelEvidence"] = [caption]
        with ExitStack() as stack:
            stack.enter_context(patch.object(analyzer, "_predict_category", return_value=SimpleNamespace(
                label="UNKNOWN", confidence=0.1, engine="fixture")))
            stack.enter_context(patch.object(analyzer, "_predict_niche", return_value="UNKNOWN"))
            stack.enter_context(patch.object(analyzer, "_predict_creator_type", return_value=creator_type))
            stack.enter_context(patch.object(analyzer, "_extract_tags", return_value=[]))
            stack.enter_context(patch.object(analyzer, "get_llm_enrichment", return_value=answer))
            stack.enter_context(patch.object(analyzer, "_save_auto_label_metrics"))
            return analyzer.process_ai_enrichment(profile, [SimpleNamespace(caption=caption)])

    def test_brand_remains_brand_even_when_llm_suggests_koc_and_invents_summary(self):
        result = self.enrich("BRAND", with_quotes=True)
        self.assertEqual(result.creatorType, "BRAND")
        self.assertIn("kem chống nắng", result.contentSummary)
        self.assertNotIn("du lịch", result.contentSummary)

    def test_unquoted_fallback_cannot_assign_type_or_niche(self):
        result = self.enrich("USER", with_quotes=False)
        self.assertEqual(result.creatorType, "USER")
        self.assertEqual(result.niche, "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
