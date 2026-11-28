import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from main import app


class DbNormalizationApiTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.environment = patch.dict(os.environ, {"DB_NORMALIZATION_API_KEY": "normalization-test-key"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.headers = {"X-Service-Key": "normalization-test-key"}

    def test_preview_requires_service_auth_before_connecting(self):
        with patch("app.api.routers.quality_router.db_normalization_service.preview") as preview:
            response = self.client.post("/api/v1/quality/db-normalization/preview", json={})
        self.assertEqual(response.status_code, 403)
        preview.assert_not_called()

    def test_preview_failure_is_reported_and_never_substitutes_staging(self):
        with patch("app.api.routers.quality_router.db_normalization_service.preview", side_effect=RuntimeError("Database unavailable")):
            response = self.client.post("/api/v1/quality/db-normalization/preview", json={}, headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertIn("SQLite", response.json()["detail"])

    def test_existing_batch_check_defaults_to_preview_not_reingestion(self):
        with patch("app.api.routers.quality_router.db_normalization_service.preview", return_value={"runId": "test", "dryRun": True}) as preview, \
             patch("app.api.routers.quality_router.db_normalization_service.apply") as apply:
            response = self.client.post("/api/v1/quality/batch-reclassify", json={}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["dryRun"])
        preview.assert_called_once()
        apply.assert_not_called()

    def test_invalid_plan_identifier_cannot_read_files_or_apply(self):
        with patch("app.api.routers.quality_router.db_normalization_service.apply") as apply:
            response = self.client.post("/api/v1/quality/db-normalization/apply", json={"plan_id": "../file"}, headers=self.headers)
        self.assertEqual(response.status_code, 422)
        apply.assert_not_called()

    def test_expired_or_wrong_target_plan_is_a_conflict(self):
        with patch("app.api.routers.quality_router.db_normalization_service.apply", side_effect=ValueError("Normalization plan expired")):
            response = self.client.post("/api/v1/quality/db-normalization/apply",
                json={"plan_id": "normalize-" + "a" * 32}, headers=self.headers)
        self.assertEqual(response.status_code, 409)
