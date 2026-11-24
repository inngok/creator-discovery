"""Reuse an operator's existing TikTok session without modifying its saved file."""
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _tiktok_host(host):
    return isinstance(host, str) and (host.lstrip(".") == "tiktok.com"
                                    or host.lstrip(".").endswith(".tiktok.com"))


def load_tiktok_session():
    if os.getenv("TIKTOK_USE_SAVED_SESSION", "true").lower() not in {"1", "true", "yes"}:
        return None
    path = Path(os.getenv("TIKTOK_SESSION_PATH") or DATA_DIR / "playwright_state.json")
    if not path.exists() and "TIKTOK_SESSION_PATH" not in os.environ:
        path = DATA_DIR / "tiktok_cookies.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        state = {"cookies": raw, "origins": []} if isinstance(raw, list) else raw
        now = time.time()
        cookies = [c for c in state.get("cookies", [])
                   if _tiktok_host(c.get("domain"))
                   and (c.get("expires", -1) <= 0 or c["expires"] > now)]
        if not cookies:
            return None
        origins = [origin for origin in state.get("origins", [])
                   if _tiktok_host(urlsplit(origin.get("origin", "")).hostname)]
        return {"cookies": cookies, "origins": origins}
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def create_tiktok_context(playwright):
    from app.crawlers.public_browser import create_browser_context
    return create_browser_context(playwright, storage_state=load_tiktok_session())
