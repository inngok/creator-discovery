"""Read the operator's saved Instagram session; never modify it while crawling."""
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _instagram_host(host):
    return isinstance(host, str) and (host.lstrip(".") == "instagram.com"
                                    or host.lstrip(".").endswith(".instagram.com"))


def load_instagram_session():
    if os.getenv("INSTAGRAM_USE_SAVED_SESSION", "true").lower() not in {"1", "true", "yes"}:
        return None
    path = Path(os.getenv("INSTAGRAM_SESSION_PATH") or DATA_DIR / "playwright_ig_state.json")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        now = time.time()
        cookies = [c for c in state.get("cookies", [])
                   if _instagram_host(c.get("domain"))
                   and (c.get("expires", -1) <= 0 or c["expires"] > now)]
        if not {"sessionid", "ds_user_id"}.issubset({c.get("name") for c in cookies}):
            return None
        origins = [o for o in state.get("origins", [])
                   if _instagram_host(urlsplit(o.get("origin", "")).hostname)]
        return {"cookies": cookies, "origins": origins}
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def create_instagram_context(playwright):
    from app.crawlers.public_browser import create_browser_context
    return create_browser_context(playwright, storage_state=load_instagram_session())
