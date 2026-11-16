import unittest
from unittest.mock import patch
import pandas as pd
from app.services.category_discovery_service import CategoryDiscoveryService


class ManagedCrawlCategoriesTest(unittest.TestCase):
    def setUp(self):
        with patch.object(CategoryDiscoveryService, "_load_directory"):
            self.service = CategoryDiscoveryService()
        self.service._cache_df = pd.DataFrame({
            "ID Chuy\u00ean gia": ["garden_creator", "beauty_creator", "other_creator"],
            "Ph\u00e2n lo\u1ea1i Nh\u00e0 s\u00e1ng t\u1ea1o": [
                "N\u00f4ng nghi\u1ec7p", "M\u1ef9 ph\u1ea9m", "Gaming"],
        })

    def usernames(self, category):
        return [row["username"] for row in self.service.get_candidates_by_category(category)]

    def test_manager_added_name_works_without_code_mapping(self):
        self.assertEqual(self.usernames("N\u00f4ng nghi\u1ec7p"), ["garden_creator"])

    def test_managed_seed_name_retains_legacy_synonyms(self):
        self.assertEqual(self.usernames("L\u00e0m \u0111\u1eb9p & M\u1ef9 ph\u1ea9m"), ["beauty_creator"])

    def test_legacy_code_still_works(self):
        self.assertEqual(self.usernames("BEAUTY"), ["beauty_creator"])

    def test_unknown_name_does_not_select_unrelated_creators(self):
        self.assertEqual(self.usernames("Astronomy"), [])

    def test_compound_name_matches_its_terms(self):
        self.assertEqual(self.usernames("Gaming & Astronomy"), ["other_creator"])

    def test_regex_characters_are_literal(self):
        self.assertEqual(self.usernames(".*"), [])

    def test_hunt_keywords_are_used_when_resolving_candidates(self):
        results = self.service.get_candidates_by_category("Custom field", keywords=["Gaming"])
        self.assertEqual([row["username"] for row in results], ["other_creator"])
