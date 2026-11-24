import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.crawlers import tiktok_session as session
from app.services.collection_service import session_status


class TikTokSessionTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.path = Path(self.root.name) / "playwright_state.json"
        self.env = patch.dict(os.environ, {"TIKTOK_USE_SAVED_SESSION": "true",
                                          "TIKTOK_SESSION_PATH": str(self.path)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def cookie(self, name, domain=".tiktok.com", expires=-1):
        return {"name": name, "value": "private-test-value", "domain": domain,
                "path": "/", "expires": expires, "httpOnly": True,
                "secure": True, "sameSite": "Lax"}

    def save(self):
        self.path.write_text(json.dumps({"cookies": [self.cookie("sessionid"),
            self.cookie("expired", expires=time.time() - 100),
            self.cookie("other", domain=".example.test"),
            self.cookie("lookalike", domain="tiktok.com.evil.test")],
            "origins": [{"origin": "https://www.tiktok.com", "localStorage": []},
                        {"origin": "https://example.test", "localStorage": []}]}), encoding="utf-8")

    def test_saved_session_filters_expired_and_other_domains_without_overwriting_file(self):
        self.save()
        original = self.path.read_bytes()
        state = session.load_tiktok_session()
        self.assertEqual([c["name"] for c in state["cookies"]], ["sessionid"])
        self.assertEqual(len(state["origins"]), 1)
        self.assertEqual(self.path.read_bytes(), original)

    def test_missing_corrupt_or_expired_session_uses_public_access(self):
        self.assertIsNone(session.load_tiktok_session())
        self.path.write_text("invalid json", encoding="utf-8")
        self.assertIsNone(session.load_tiktok_session())
        self.path.write_text(json.dumps({"cookies": [self.cookie("expired", expires=1)]}))
        self.assertIsNone(session.load_tiktok_session())

    def test_public_mode_does_not_read_saved_session(self):
        self.save()
        with patch.dict(os.environ, {"TIKTOK_USE_SAVED_SESSION": "false"}), patch.object(
                Path, "read_text", side_effect=AssertionError("Unexpected session read")):
            self.assertIsNone(session.load_tiktok_session())
            self.assertEqual(session_status("TIKTOK")["mode"], "PUBLIC_ONLY")

    def test_tiktok_profile_and_discovery_share_the_saved_session_loader(self):
        from app.crawlers.tiktok_crawler import _init_playwright_context
        from app.crawlers.tiktok_discovery import _init_discovery_context
        self.save()
        context = MagicMock()
        with patch("app.crawlers.public_browser.create_browser_context", return_value=context) as create:
            self.assertEqual(_init_playwright_context(MagicMock()), (context, None))
            self.assertEqual(create.call_args.kwargs["storage_state"]["cookies"][0]["name"], "sessionid")
            _init_discovery_context(MagicMock())
            self.assertEqual(create.call_args.kwargs["storage_state"]["cookies"][0]["name"], "sessionid")
        self.assertEqual(create.call_count, 2)

    def test_status_reports_session_presence_without_claiming_live_access(self):
        self.save()
        status = session_status("TIKTOK")
        self.assertEqual(status["mode"], "SAVED_SESSION")
        self.assertTrue(status["uses_saved_session"])
        self.assertFalse(status["remotely_verified"])
        self.assertNotIn("private-test-value", str(status))
        self.assertEqual(session_status("FACEBOOK")["mode"], "PUBLIC_ONLY")
        self.assertEqual(session_status("INSTAGRAM")["mode"], "PUBLIC_ONLY")

    def test_legacy_cookie_export_is_supported(self):
        self.path.write_text(json.dumps([self.cookie("sessionid")]))
        self.assertEqual(session.load_tiktok_session()["cookies"][0]["name"], "sessionid")
