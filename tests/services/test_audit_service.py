import os
import unittest
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ["TESTING"] = "1"
os.environ["DISABLE_CRAWL_PACING"] = "1"

from app.services.audit_service import AuditService, audit_service
from fastapi.testclient import TestClient
from main import app


class TestAuditService(unittest.TestCase):
    def setUp(self):
        self.service = AuditService()
        self.client = TestClient(app)
        normalization_key = patch.dict(os.environ, {"DB_NORMALIZATION_API_KEY": "normalization-test-key"})
        normalization_key.start()
        self.addCleanup(normalization_key.stop)
        staging = patch("app.services.audit_service.staging_repo")
        self.staging = staging.start()
        self.staging.get_candidate.return_value = None
        self.addCleanup(staging.stop)
        report = patch.object(self.service, "_save_report")
        report.start()
        self.addCleanup(report.stop)
        embedding = patch("app.mappers.java_mapper.embedding_service.get_search_embedding", return_value=[0.05] * 768)
        embedding.start()
        self.addCleanup(embedding.stop)

    @patch("app.services.audit_service.embedding_service.get_embedding")
    @patch("app.services.audit_service.java_client.send_creator_data")
    def test_audit_single_creator_koc(self, mock_java, mock_embed):
        mock_embed.return_value = [0.05] * 768
        mock_java.return_value = {"status": "success"}

        creator = {
            "creator_id": "test_1",
            "platform": "TIKTOK",
            "user_name": "linh_beauty_review",
            "display_name": "Linh Trải Nghiệm Mỹ Phẩm",
            "avatar_url": "https://example.com/avatar.jpg",
            "profile_url": "https://tiktok.com/@linh_beauty_review",
            "followers": 45000,
            "category": "Beauty",
            "niche": "Skincare Review",
            "contact": "linh@gmail.com",
            "content_summary": "Kênh review mỹ phẩm và hướng dẫn dưỡng da cá nhân",
            "observed_at": "2026-11-19T10:00:00Z",
            "data_confidence": 0.5,
            "posts": [
                {
                    "platform_post_id": "p1",
                    "post_url": "https://tiktok.com/@linh/video/1",
                    "caption": "Trải nghiệm thực tế kem chống nắng cho da dầu mụn",
                    "views": 25000,
                    "likes": 1200,
                    "comments": 85,
                    "shares": 10,
                    "posted_at": "2026-11-18T10:00:00Z",
                }
            ],
        }

        creator["posts"] = [{**creator["posts"][0], "platform_post_id": f"p{i}",
                             "caption": f"Review kem chống nắng loại {i}, ưu điểm thấm nhanh, nhược điểm hơi dính"}
                            for i in range(5)]
        result = self.service.audit_single_creator(creator, dry_run=False, auto_quarantine=True)

        self.assertEqual(result["status"], "VALID_KOC")
        self.assertEqual(result["creatorType"], "KOC")
        self.assertFalse(result["isJunkOrShop"])
        self.assertGreater(result["newConfidence"], 0.6)
        self.assertTrue(result["hasEmbedding"])
        self.assertTrue(result["syncedToJava"])
        mock_java.assert_called_once()

    @patch("app.services.audit_service.embedding_service.get_embedding")
    @patch("app.services.audit_service.staging_repo.upsert_candidate")
    @patch("app.services.audit_service.java_client.send_creator_data")
    def test_audit_single_creator_shop_rejection(self, mock_java, mock_staging_update, mock_embed):
        mock_embed.return_value = [0.0] * 768
        shop_creator = {
            "creator_id": "shop_1",
            "platform": "TIKTOK",
            "user_name": "kho_si_quan_ao_giare",
            "display_name": "Tổng Kho Sỉ Quần Áo Toàn Quốc",
            "avatar_url": "",
            "profile_url": "https://tiktok.com/@kho_si_quan_ao_giare",
            "followers": 3000,
            "category": "Fashion",
            "niche": "Sỉ lẻ",
            "contact": "Zalo sỉ: 0988888888",
            "content_summary": "Sỉ lẻ quần áo hot trend giá xưởng, tuyển đại lý toàn quốc ship cod",
            "observed_at": "2026-01-01T00:00:00Z",
            "data_confidence": 0.3,
            "posts": [
                {
                    "platform_post_id": "p2",
                    "post_url": "https://tiktok.com/@kho_si/video/2",
                    "caption": "Xả kho hàng sỉ lẻ giá tận gốc chỉ 49k ai lấy ib zalo",
                    "views": 500,
                    "likes": 5,
                    "comments": 1,
                    "shares": 0,
                    "posted_at": "2026-01-01T00:00:00Z",
                }
            ],
        }

        result = self.service.audit_single_creator(shop_creator, dry_run=False, auto_quarantine=True)

        self.assertEqual(result["status"], "JUNK_SHOP_CATALOG")
        self.assertTrue(result["isJunkOrShop"])
        self.assertFalse(result["syncedToJava"])
        mock_java.assert_not_called()
        mock_staging_update.assert_called_once()

    @patch("app.services.audit_service.java_client.send_creator_data")
    def test_three_ordinary_posts_require_review_without_shop_or_spam_label(self, send):
        creator = {"id": "creator-1", "platform": "TIKTOK", "username": "personal",
                   "followers": 200000, "contentSummary": "Generic generated creator description",
                   "posts": [{"platformPostId": str(i), "caption": f"My daily life {i}"} for i in range(3)]}
        result = self.service.audit_single_creator(creator, auto_quarantine=True)
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertFalse(result["isJunkOrShop"])
        self.assertEqual(result["actionTaken"], "QUEUED_FOR_MANAGER_REVIEW")
        saved = self.staging.upsert_candidate.call_args.args[0]
        self.assertEqual(saved.decision.value, "NEEDS_MANAGER_REVIEW")
        self.assertEqual(len(result["posts"]), 3)
        self.assertEqual(len(saved.evidence_summary["audit_posts"]), 3)
        send.assert_not_called()

    def test_audit_preserves_manager_decision(self):
        from app.ai.rules.rule_config import Decision
        self.staging.get_candidate.return_value = SimpleNamespace(decision=Decision.MANAGER_APPROVED)
        creator = {"platform": "TIKTOK", "username": "approved", "followers": 200000,
                   "posts": [{"platformPostId": "1", "caption": "A personal post"}]}
        result = self.service.audit_single_creator(creator)
        self.assertEqual(result["actionTaken"], "PRESERVED_MANAGER_DECISION")
        self.staging.upsert_candidate.assert_not_called()
        self.staging.update_status_and_decision.assert_not_called()

    def test_saved_audit_sample_is_read_without_fetch_or_recrawl(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.json"
            item = {"creatorId": "id", "username": "creator", "platform": "TIKTOK", "posts": []}
            path.write_text(json.dumps([{"runId": "run", "items": [item]}]), encoding="utf-8")
            with patch("app.services.audit_service.AUDIT_REPORT_PATH", str(path)), \
                 patch.object(self.service, "fetch_creators_to_audit") as fetch:
                result = self.service.get_audit_evidence("id", "TIKTOK", "creator", "run")
            self.assertEqual(result["evidenceSource"], "AUDIT_SNAPSHOT")
            fetch.assert_not_called()

    def test_legacy_report_evidence_is_marked_as_current_database(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.services.audit_service.AUDIT_REPORT_PATH", str(Path(directory) / "missing.json")), \
                 patch.object(self.service, "fetch_creators_to_audit", return_value=[{
                     "id": "id", "username": "creator", "platform": "TIKTOK", "posts": []}]):
                result = self.service.get_audit_evidence("id", "TIKTOK", "creator", "old-run")
        self.assertEqual(result["evidenceSource"], "CURRENT_DATABASE")

    def test_audit_evidence_api_reads_saved_data_without_recrawling(self):
        with patch("app.api.routers.quality_router.audit_service.get_audit_evidence", return_value={
                "creatorId": "id", "posts": [], "evidenceSource": "AUDIT_SNAPSHOT"}) as read:
            response = self.client.get("/api/v1/quality/audit-evidence", params={
                "creator_id": "id", "platform": "TIKTOK", "username": "creator", "run_id": "run"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["evidenceSource"], "AUDIT_SNAPSHOT")
        read.assert_called_once_with("id", "TIKTOK", "creator", "run")

    def test_audit_does_not_make_old_observations_fresh(self):
        creator = {"platform": "TIKTOK", "username": "old_profile", "followers": 20000,
                   "observedAt": "2020-01-01T00:00:00Z",
                   "posts": [{"platformPostId": "1", "caption": "Personal post"}]}
        with patch("app.services.audit_service.compute_data_confidence", return_value=0.5) as compute:
            self.service.audit_single_creator(creator, dry_run=True)
        self.assertEqual(compute.call_args.kwargs["observed_at"], creator["observedAt"])

    def test_batch_review_cases_are_not_counted_as_valid_creators_or_junk(self):
        with patch.object(self.service, "fetch_creators_to_audit", return_value=[{}]), \
             patch.object(self.service, "audit_single_creator", return_value={
                 "status": "NEEDS_REVIEW", "creatorType": "UNKNOWN", "isJunkOrShop": False,
                 "newConfidence": 0.8}):
            summary = self.service.audit_batch(dry_run=True)
        self.assertEqual(summary["needsReviewCount"], 1)
        self.assertEqual(summary["junkOrShopCount"], 0)
        self.assertEqual(summary["validCreators"], 0)

    @patch("app.services.audit_service.AuditService.fetch_creators_to_audit")
    @patch("app.services.audit_service.AuditService.audit_single_creator")
    def test_audit_batch_dry_run(self, mock_audit_single, mock_fetch):
        mock_fetch.return_value = [
            {"creator_id": "1", "user_name": "user1"},
            {"creator_id": "2", "user_name": "user2"},
        ]
        mock_audit_single.side_effect = [
            {
                "creatorId": "1",
                "username": "user1",
                "status": "VALID_KOC",
                "creatorType": "KOC",
                "isJunk": False,
                "isJunkOrShop": False,
                "newConfidence": 0.85,
            },
            {
                "creatorId": "2",
                "username": "user2",
                "status": "JUNK_SHOP_CATALOG",
                "creatorType": "NONE",
                "isJunk": True,
                "isJunkOrShop": True,
                "newConfidence": 0.20,
            },
        ]

        summary = self.service.audit_batch(limit=2, dry_run=True)

        self.assertEqual(summary["totalAudited"], 2)
        self.assertEqual(summary["validCreators"], 1)
        self.assertEqual(summary["kocCount"], 1)
        self.assertEqual(summary["junkOrShopCount"], 1)
        self.assertEqual(summary["cleanRate"], 50.0)
        self.assertEqual(summary["dryRun"], True)

    def test_api_batch_reclassify_and_report_endpoints(self):
        # Test GET /api/v1/quality/audit-report
        res_report = self.client.get("/api/v1/quality/audit-report", headers={"X-Service-Key": "normalization-test-key"})
        self.assertEqual(res_report.status_code, 200)

        # Test POST /api/v1/quality/batch-reclassify with mock audit_batch
        with patch("app.api.routers.quality_router.audit_service.audit_batch") as mock_batch:
            mock_batch.return_value = {
                "runId": "audit-test",
                "totalAudited": 10,
                "validCreators": 8,
                "kocCount": 7,
                "kolCount": 1,
                "junkOrShopCount": 2,
                "cleanRate": 80.0,
                "dryRun": True,
            }
            payload = {
                "limit": 10,
                "offset": 0,
                "dry_run": True,
                "auto_quarantine": True,
                "mode": "RECLASSIFY",
            }
            res_post = self.client.post("/api/v1/quality/batch-reclassify", json=payload,
                                        headers={"X-Service-Key": "normalization-test-key"})
            self.assertEqual(res_post.status_code, 200)
            data = res_post.json()
            self.assertEqual(data["runId"], "audit-test")
            self.assertEqual(data["cleanRate"], 80.0)
            mock_batch.assert_called_once_with(
                limit=10,
                offset=0,
                platform=None,
                category=None,
                dry_run=True,
                auto_quarantine=True,
                recheck_taxonomy=True,
            )

    def test_postgres_audit_cannot_reingest_stored_rows_or_overwrite_staging(self):
        creator = {"id": "creator", "platform": "TIKTOK", "username": "public_review",
            "source": "POSTGRESQL", "creatorType": "KOC", "followers": 25000,
            "observedAt": "2020-01-01T00:00:00Z", "category": "BEAUTY", "niche": "Gia dụng",
            "posts": [{"platformPostId": str(i), "caption": f"Review máy hút bụi {i}, ưu điểm sạch, nhược điểm ồn"} for i in range(5)]}
        with patch("app.services.audit_service.java_client.send_creator_data") as send:
            result = self.service.audit_single_creator(creator, dry_run=False)
        send.assert_not_called()
        self.staging.upsert_candidate.assert_not_called()
        self.assertFalse(result["syncedToJava"])
        self.assertEqual(result["normalizedCategory"], "HOME")
        self.assertEqual(result["oldType"], "KOC")
        self.assertEqual(result["status"], "PROPOSED_RECLASSIFICATION")
        self.assertTrue(result["requiresHumanReview"])

    def test_postgres_failure_never_returns_staging_as_database_evidence(self):
        with patch.object(self.service, "_get_pg_connection", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "PostgreSQL audit connection unavailable"):
                self.service.fetch_creators_to_audit()
        self.staging.list_candidates.assert_not_called()

    def test_dry_run_without_posts_never_recrawls_missing_evidence(self):
        with patch("app.services.audit_service.fetch_tiktok_preflight") as fetch:
            self.service.audit_single_creator({"platform": "TIKTOK", "username": "missing", "posts": []}, dry_run=True)
        fetch.assert_not_called()

    def test_taxonomy_recheck_uses_public_quotes_instead_of_stored_wrong_labels(self):
        from app.core.models import Post
        posts = [Post(platformPostId="one", postUrl="", caption="Review mì trộn ngon ở quán đầu ngõ"),
                 Post(platformPostId="two", postUrl="", caption="Món bún bò này có nước dùng rất ngon")]
        answer = {"category": "FOOD", "niche": "Thực phẩm", "confidence": 0.95,
                  "evidence": [{"postId": p.platformPostId, "quote": p.caption} for p in posts]}
        with patch("app.ai.analyzer.get_llm_enrichment", return_value=answer) as model:
            result = self.service.propose_taxonomy_from_evidence(posts)
        self.assertEqual((result["category"], result["niche"]), ("FOOD", "FOOD"))
        self.assertTrue(result["requiresHumanReview"])
        self.assertIn(posts[0].caption, model.call_args.args[0])

    def test_taxonomy_recheck_normalizes_separator_variants_of_category_aliases(self):
        from app.core.models import Post
        posts = [Post(platformPostId="one", postUrl="", caption="Chia sẻ đồ dùng cho mẹ và bé"),
                 Post(platformPostId="two", postUrl="", caption="Kinh nghiệm chăm sóc trẻ sơ sinh")]
        answer = {"category": "MOTHER_BABY", "niche": "MẸ_VÀ_BÉ", "confidence": 0.95,
                  "evidence": [{"postId": p.platformPostId, "quote": p.caption} for p in posts]}
        with patch("app.ai.analyzer.get_llm_enrichment", return_value=answer):
            result = self.service.propose_taxonomy_from_evidence(posts)
        self.assertEqual((result["category"], result["niche"]), ("MOTHER_BABY", "MOTHER_BABY"))

    def test_hallucinated_quotes_and_contradictory_categories_cannot_become_proposals(self):
        from app.core.models import Post
        posts = [Post(platformPostId="one", postUrl="", caption="Review mì trộn ngon ở quán đầu ngõ"),
                 Post(platformPostId="two", postUrl="", caption="Món bún bò này có nước dùng rất ngon")]
        for answer in [
            {"category": "FOOD", "niche": "FOOD", "confidence": 0.99,
             "evidence": [{"postId": "one", "quote": "A fabricated quote about food"}]},
            {"category": "BEAUTY", "niche": "Thực phẩm", "confidence": 0.99, "evidence": []},
            {"category": "FOOD", "niche": "FOOD", "confidence": float("nan"), "evidence": []},
        ]:
            with patch("app.ai.analyzer.get_llm_enrichment", return_value=answer):
                self.assertNotEqual(self.service.propose_taxonomy_from_evidence(posts)["status"], "PROPOSED")


if __name__ == "__main__":
    unittest.main()
