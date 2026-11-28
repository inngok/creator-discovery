import unittest
from unittest.mock import Mock, patch
import numpy as np

from app.ai import analyzer
from app.core.discovery_contract import CATEGORY_CODES


class CategoryModelScopeTest(unittest.TestCase):
    def test_incomplete_model_without_domain_evidence_cannot_force_food_into_beauty(self):
        model = Mock()
        with patch.object(analyzer, "cat_model", model), \
             patch.object(analyzer, "cat_classes", ["BEAUTY", "FASHION"]), \
             patch.object(analyzer, "CATEGORY_PROTOTYPES", {}), \
             patch.object(analyzer.embedding_service, "get_embedding") as embed:
            result = analyzer._predict_category("Review bún bò và quán ăn ngon")
        self.assertEqual(result.label, "UNKNOWN")
        self.assertTrue(result.needs_review)
        self.assertEqual(result.engine, "PARTIAL_TAXONOMY")
        embed.assert_not_called()
        model.predict_proba.assert_not_called()

    def test_partial_model_can_accept_a_category_with_matching_semantic_evidence(self):
        model = Mock()
        model.predict_proba.return_value = np.array([[0.95, 0.05]])
        with patch.object(analyzer, "cat_model", model), \
             patch.object(analyzer, "cat_classes", ["BEAUTY", "FASHION"]), \
             patch.object(analyzer, "CATEGORY_PROTOTYPES", {"BEAUTY": "Skincare and makeup reviews"}), \
             patch.object(analyzer.embedding_service, "get_embedding", return_value=np.array([[1., 0.]])):
            result = analyzer._predict_category("Review kem chống nắng")
        self.assertEqual(result.label, "BEAUTY")
        self.assertFalse(result.needs_review)

    def test_a_prototype_for_another_category_is_not_evidence_for_the_predicted_label(self):
        model = Mock()
        model.predict_proba.return_value = np.array([[0.05, 0.95]])
        with patch.object(analyzer, "cat_model", model), \
             patch.object(analyzer, "cat_classes", ["BEAUTY", "FASHION"]), \
             patch.object(analyzer, "CATEGORY_PROTOTYPES", {"BEAUTY": "Skincare reviews"}), \
             patch.object(analyzer.embedding_service, "get_embedding", return_value=np.array([[1., 0.]])):
            self.assertEqual(analyzer._predict_category("Review món ăn").label, "UNKNOWN")

    def test_high_binary_probability_does_not_override_a_semantic_mismatch(self):
        model = Mock()
        model.predict_proba.return_value = np.array([[0.99, 0.01]])
        with patch.object(analyzer, "cat_model", model), \
             patch.object(analyzer, "cat_classes", ["BEAUTY", "FASHION"]), \
             patch.object(analyzer, "CATEGORY_PROTOTYPES", {"BEAUTY": "Skincare reviews"}), \
             patch.object(analyzer.embedding_service, "get_embedding", side_effect=[np.array([[0., 1.]]), np.array([[1., 0.]])]):
            self.assertEqual(analyzer._predict_category("Review món ăn").label, "UNKNOWN")

    def test_complete_category_model_can_classify_food_without_the_binary_model_guard(self):
        labels = sorted(CATEGORY_CODES)
        probabilities = [0.01 / (len(labels) - 1)] * len(labels)
        probabilities[labels.index("FOOD")] = 0.99
        model = Mock()
        model.predict_proba.return_value = np.array([probabilities])
        with patch.object(analyzer, "cat_model", model), patch.object(analyzer, "cat_classes", labels), \
             patch.object(analyzer, "CATEGORY_PROTOTYPES", {}), \
             patch.object(analyzer.embedding_service, "get_embedding", return_value=np.array([[1., 0.]])):
            self.assertEqual(analyzer._predict_category("Review bún bò").label, "FOOD")
