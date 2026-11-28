import math
import unittest
from unittest.mock import patch

from app.core.discovery_contract import CONTRACT, canonical_label, normalize_classification, search_terms
from app.ai.rules.rule_config import MIN_FOLLOWERS_THRESHOLD
from app.clients.java_client import JavaClient
from scripts.export_discovery_contract_fixture import fixtures


class DiscoveryContractTest(unittest.TestCase):
    def test_all_aliases_have_the_same_meaning_as_their_canonical_code(self):
        for label in [*CONTRACT["categories"], *CONTRACT["niches"]]:
            for alias in [label["code"], *label["aliases"]]:
                with self.subTest(alias=alias):
                    self.assertEqual(canonical_label(alias), label["code"])
                    self.assertEqual(normalize_classification(alias, alias),
                                     (label.get("category", label["code"]), label["code"]))

    def test_public_observed_niche_wins_over_inconsistent_coarse_category(self):
        self.assertEqual(normalize_classification("BEAUTY", "Gia dụng"), ("HOME", "HOME"))
        self.assertEqual(normalize_classification("BEAUTY", "Thực phẩm, Gia dụng"), ("UNKNOWN", "FOOD, HOME"))
        self.assertEqual(normalize_classification("FOOD", "Thực phẩm, Gia dụng"), ("FOOD", "FOOD, HOME"))
        self.assertEqual(normalize_classification("FOOD", "UNKNOWN"), ("FOOD", "UNKNOWN"))

    def test_collection_uses_contract_floor_and_does_not_mix_unrelated_search_seeds(self):
        self.assertEqual(MIN_FOLLOWERS_THRESHOLD, CONTRACT["minFollowers"])
        self.assertEqual(search_terms("TECHNOLOGY"), search_terms("Công nghệ & Điện thoại / PC"))
        self.assertNotIn("Nhà cửa, Nội thất & Thiết bị gia dụng", search_terms("LIFESTYLE"))
        self.assertNotIn("Game", search_terms("ENTERTAINMENT"))
        self.assertIn("Chăm sóc da", search_terms("SKINCARE"))
        self.assertNotIn("Làm đẹp", search_terms("SKINCARE"))

    def test_real_mapper_exports_consistent_snapshot_metrics_units_and_classification(self):
        for case in fixtures():
            payload = case["payload"]
            self.assertEqual(payload["category"], case["expectedCategory"])
            self.assertEqual(payload["creatorType"], "KOC")
            self.assertEqual(payload["contractVersion"], CONTRACT["version"])
            self.assertEqual(payload["engagementRateUnit"], "RATIO")
            self.assertEqual(len(payload["recentPosts"]), 3)
            self.assertEqual(payload["avgLikes"], 100)
            self.assertEqual(payload["engagementRate"], 0.005)
            self.assertTrue(payload["recentPosts"][0]["thumbnailUrl"])
            self.assertTrue(all(math.isfinite(v) for v in payload["embedding"]))

    def test_ingestion_idempotent_ack_is_success_but_rejection_is_not(self):
        client = JavaClient()
        for status, creator_id, expected in [("SUCCESS", "creator", True), ("SKIPPED", "creator", True),
                                              ("REJECTED", None, False), ("SUCCESS", None, False)]:
            with self.subTest(status=status, creator_id=creator_id), patch("app.clients.java_client.httpx.post") as post:
                post.return_value.status_code = 200
                post.return_value.json.return_value = {"success": True, "data": {"status": status, "creatorId": creator_id}}
                self.assertEqual(client.send_creator_data({}), expected)
