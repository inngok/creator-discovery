import os
import unittest
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

        result = self.service.audit_single_creator(creator, dry_run=False, auto_quarantine=True)

        self.assertEqual(result["status"], "VALID_KOC")
        self.assertEqual(result["creatorType"], "KOC")
        self.assertFalse(result["isJunkOrShop"])
        self.assertGreater(result["newConfidence"], 0.6)
        self.assertTrue(result["hasEmbedding"])
        self.assertTrue(result["syncedToJava"])
        mock_java.assert_called_once()

    @patch("app.services.audit_service.embedding_service.get_embedding")
    @patch("app.services.audit_service.staging_repo.update_status_and_decision")
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
        res_report = self.client.get("/api/v1/quality/audit-report")
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
            }
            res_post = self.client.post("/api/v1/quality/batch-reclassify", json=payload)
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
            )


if __name__ == "__main__":
    unittest.main()
