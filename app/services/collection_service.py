"""Explicit live/manual/snapshot collection for the three supported platforms."""
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.core.models import IngestPayload

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
PLATFORMS = ("TIKTOK", "FACEBOOK", "INSTAGRAM")


def session_status(platform: str) -> dict:
    """Describe collection mode; remote readiness requires an actual live request."""
    if platform not in PLATFORMS:
        raise ValueError("Unsupported platform")
    if platform == "TIKTOK":
        from app.crawlers.tiktok_session import load_tiktok_session
        if load_tiktok_session() is not None:
            return {"platform": platform, "status": "SAVED_SESSION_UNVERIFIED",
                    "mode": "SAVED_SESSION", "requires_login": False,
                    "uses_saved_session": True, "remotely_verified": False}
    return {"platform": platform, "status": "PUBLIC_ACCESS_UNVERIFIED",
            "mode": "PUBLIC_ONLY", "requires_login": False,
            "uses_saved_session": False, "remotely_verified": False}


def _snapshot_path(platform: str, username: str) -> Path:
    import hashlib
    key = hashlib.sha256(f"{platform}:{username.lstrip('@').casefold()}".encode()).hexdigest()
    return DATA_DIR / "collection_snapshots" / f"{key}.json"


def save_snapshot(payload: IngestPayload) -> None:
    path = _snapshot_path(payload.creator.platform.value, payload.creator.username)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Each write is atomic so concurrent API requests cannot leave partial JSON.
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(payload.model_dump_json())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_snapshot(platform: str, username: str, max_age_hours: int) -> IngestPayload | None:
    try:
        payload = IngestPayload.model_validate_json(_snapshot_path(platform, username).read_text(encoding="utf-8"))
        age = (datetime.now(UTC) - payload.collectedAt.astimezone(UTC)).total_seconds()
        if payload.creator.platform.value == platform and payload.creator.username.lstrip('@').casefold() == username.lstrip('@').casefold() and 0 <= age <= max_age_hours * 3600:
            return payload
    except (OSError, ValueError):
        pass
    return None


async def collect_profile(platform: str, username: str, allow_cached: bool = False,
                          max_age_hours: int = 168) -> dict:
    from app.crawlers.facebook_crawler import crawl_facebook_profile
    from app.crawlers.instagram_crawler import crawl_instagram_profile
    from app.crawlers.tiktok_crawler import crawl_tiktok_profile
    from app.core.profile_identifiers import normalize_profile_identifier

    platform = platform.strip().upper()
    username = normalize_profile_identifier(platform, username)
    runner = {"TIKTOK": crawl_tiktok_profile, "FACEBOOK": crawl_facebook_profile,
              "INSTAGRAM": crawl_instagram_profile}[platform]
    try:
        payload = await runner(username)
        if payload is None:
            raise RuntimeError("Profile unavailable, login required, or source blocked")
        if platform in {"FACEBOOK", "INSTAGRAM"}:
            from app.core.vietnam_evidence import vietnam_market_evidence
            market_check = vietnam_market_evidence({"bio": payload.creator.bio},
                                                    [p.model_dump() for p in payload.recentPosts])
            if not market_check["matches"]:
                return {"status": "error", "is_live": True, "data": None,
                        "error": "VIETNAM_MARKET_NOT_VERIFIED", "market_check": market_check}
        if not payload.recentPosts:
            return {"status": "partial", "is_live": True,
                    "warning": "Profile loaded, but no posts were collected. The source may restrict anonymous access; retry later or try another public profile.",
                    "connection": session_status(platform), "data": payload.model_dump(mode="json")}
    except Exception:
        payload = load_snapshot(platform, username, max_age_hours) if allow_cached else None
        if payload is not None and platform in {"FACEBOOK", "INSTAGRAM"}:
            from app.core.vietnam_evidence import vietnam_market_evidence
            if not vietnam_market_evidence({"bio": payload.creator.bio},
                                            [p.model_dump() for p in payload.recentPosts])["matches"]:
                payload = None
        if payload is not None:
            return {"status": "cached", "is_live": False,
                    "warning": "Live collection failed. Returning a previously saved snapshot; check collectedAt.",
                    "data": payload.model_dump(mode="json")}
        return {"status": "error", "is_live": False, "data": None,
                "error": "LIVE_COLLECTION_FAILED", "connection": session_status(platform),
                "recovery": ["Retry later; the public source may temporarily restrict access", "Retry with a profile username instead of keyword discovery", "Retry with allow_cached=true if a snapshot exists"]}
    warning = None
    try:
        save_snapshot(payload)
    except OSError:
        warning = "Live data collected, but the local snapshot could not be saved."
    return {"status": "success", "is_live": True, "warning": warning,
            "data": payload.model_dump(mode="json")}
