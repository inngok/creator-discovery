from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
import tempfile
import unittest
import uuid
from unittest.mock import patch

from app.services.db_normalization_service import DbNormalizationService, assess_record, fingerprint
from app.core.postgres_connection import connect_postgres

NOW = datetime(2026, 11, 28, 0, 0, tzinfo=UTC)


def record(**overrides):
    row = {"creatorId": str(uuid.uuid4()), "metricId": str(uuid.uuid4()), "platform": "TIKTOK",
        "username": "creator", "category": "BEAUTY", "niche": "Gia dụng", "creatorType": " koc ",
        "followers": 25000, "avgLikes": Decimal("100"), "avgComments": Decimal("20"),
        "avgShares": Decimal("5"), "engagementRate": Decimal("0.5"), "dataConfidence": Decimal("0.9"),
        "freshnessStatus": "FRESH", "observedAt": datetime(2026, 10, 1),
        "collectedAt": datetime(2026, 10, 1), "embedding": "[0.1,0.2]",
        "contentSummary": "Generated beauty summary with no public evidence", "qualityReviewedAt": None,
        "qualityReviewedBy": None, "posts": [{"platformPostId": str(i), "caption": f"Review máy hút bụi thật tại nhà {i}",
            "postedAt": f"2026-10-0{i+1}T00:00:00Z"} for i in range(3)]}
    row.update(overrides)
    return row


class DbNormalizationTest(unittest.TestCase):
    def test_corrects_industry_and_er_from_stored_facts_without_faking_a_new_crawl(self):
        original = record()
        result = assess_record(original, now=NOW)
        self.assertEqual(result["changes"]["category"], "HOME")
        self.assertEqual(result["changes"]["niche"], "HOME")
        self.assertEqual(result["changes"]["creatorType"], "KOC")
        self.assertEqual(result["changes"]["engagementRate"], "0.0050")
        self.assertEqual(result["changes"]["freshnessStatus"], "STALE")
        self.assertTrue(result["clearEmbedding"])
        self.assertNotIn("observedAt", result["changes"])
        self.assertNotIn("collectedAt", result["changes"])
        self.assertNotIn("posts", result["changes"])
        self.assertEqual(original["engagementRate"], Decimal("0.5"))

    def test_manager_classification_is_preserved_while_measured_er_can_be_normalized(self):
        result = assess_record(record(qualityReviewedAt=NOW, qualityReviewedBy=str(uuid.uuid4())), now=NOW)
        for field in ("category", "niche", "creatorType", "dataConfidence"):
            self.assertNotIn(field, result["changes"])
        self.assertEqual(result["changes"]["engagementRate"], "0.0050")
        self.assertTrue(result["managerDecisionPreserved"])

    def test_never_guesses_percentage_units_or_zeroes_missing_counts(self):
        result = assess_record(record(avgComments=None, engagementRate=Decimal("120.5")), now=NOW)
        self.assertNotIn("engagementRate", result["changes"])
        self.assertIn("ER_UNIT_UNVERIFIABLE_WITHOUT_COUNTS", result["reviewReasons"])

    def test_missing_evidence_does_not_turn_old_summary_into_bio_or_invent_a_creator_type(self):
        result = assess_record(record(category=None, niche=None, creatorType=None, posts=[], observedAt=None), now=NOW)
        self.assertEqual(result["changes"]["category"], "UNKNOWN")
        self.assertEqual(result["changes"]["creatorType"], "UNKNOWN")
        self.assertNotIn("contentSummary", result["changes"])
        self.assertNotIn("freshnessStatus", result["changes"])
        self.assertIn("NO_PUBLIC_POST_EVIDENCE_RECRAWL_NEEDED", result["reviewReasons"])

    def test_non_finite_confidence_does_not_remain_eligible_by_accident(self):
        result = assess_record(record(dataConfidence=Decimal("NaN")), now=NOW)
        self.assertEqual(result["changes"]["dataConfidence"], "0")
        self.assertIn("INVALID_DATA_CONFIDENCE", result["reviewReasons"])

    def test_known_coarse_category_without_niche_still_requires_content_verification(self):
        result = assess_record(record(category="BEAUTY", niche="UNKNOWN"), now=NOW)
        self.assertIn("CLASSIFICATION_NEEDS_CONTENT_REVIEW", result["reviewReasons"])
        self.assertNotIn("category", result["changes"])

    def test_summary_uses_the_newest_three_dated_posts(self):
        posts = [{"platformPostId": str(i), "caption": f"Nội dung công khai có bằng chứng thứ {i}",
                  "postedAt": f"2026-10-0{i+1}T00:00:00Z"} for i in range(4)]
        result = assess_record(record(posts=posts), now=NOW)
        self.assertIn("thứ 3", result["changes"]["contentSummary"])
        self.assertNotIn("thứ 0", result["changes"]["contentSummary"])

    def test_repeated_normalization_proposes_no_extra_changes(self):
        original = record()
        first = assess_record(original, now=NOW)
        updated = {**original, **first["changes"], "embedding": None}
        second = assess_record(updated, now=NOW)
        self.assertFalse(second["changes"])
        self.assertFalse(second["clearEmbedding"])

    def test_evidence_and_review_changes_alter_the_concurrency_fingerprint(self):
        original = record()
        changed = {**original, "qualityReviewedAt": NOW}
        self.assertNotEqual(fingerprint(original), fingerprint(changed))
        changed = {**original, "posts": [{"caption": "Updated public content"}]}
        self.assertNotEqual(fingerprint(original), fingerprint(changed))

    def test_missing_snapshot_is_reported_without_fabricating_a_record(self):
        self.assertEqual(assess_record(record(metricId=None), now=NOW)["changes"], {})

    def test_missing_database_configuration_never_falls_back_to_remote_defaults_or_staging(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Configure NORMALIZATION_DATABASE_URL"):
                connect_postgres()

    def test_plan_path_and_contract_version_are_validated_before_connecting(self):
        with tempfile.TemporaryDirectory() as folder:
            service = DbNormalizationService(report_dir=folder)
            with self.assertRaises(ValueError):
                service.report("../another-file")
            plan_id = "normalize-" + uuid.uuid4().hex
            service._save({"runId": plan_id, "contractHash": "old", "contractVersion": "future"})
            with self.assertRaisesRegex(ValueError, "Contract changed"):
                service.apply(plan_id)
