import re
from urllib.parse import parse_qs, urlsplit


def normalize_profile_identifier(platform: str, raw: str) -> str:
    platform = platform.strip().upper()
    domains = {"TIKTOK": "tiktok.com", "FACEBOOK": "facebook.com", "INSTAGRAM": "instagram.com"}
    if platform not in domains:
        raise ValueError("Supported platforms: TIKTOK, FACEBOOK, INSTAGRAM")
    value = raw.strip()
    if "://" in value or "/" in value or value.lower().startswith(domains[platform]):
        parsed = urlsplit(value if "://" in value else "https://" + value)
        host = (parsed.hostname or "").lower()
        domain = domains[platform]
        if host != domain and not host.endswith("." + domain):
            raise ValueError("Profile URL does not match the selected platform")
        parts = parsed.path.strip("/").split("/")
        value = parts[0]
        if platform == "FACEBOOK" and value == "profile.php":
            value = parse_qs(parsed.query).get("id", [""])[0]
        elif len(parts) > 1:
            raise ValueError("Provide a profile URL, not a post or search URL")
    value = value.lstrip("@")
    reserved = {"search", "explore", "accounts", "login", "reel", "reels", "p", "watch", "share", "video", "videos"}
    if value.casefold() in reserved or not re.fullmatch(r"[A-Za-z0-9._]{1,100}", value):
        raise ValueError("Invalid profile username or URL")
    return value
