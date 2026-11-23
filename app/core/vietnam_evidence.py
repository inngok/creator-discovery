"""Conservative Vietnam market evidence; never infer country from a person's name."""
import re
import unicodedata


def _plain(text):
    text = re.sub(r"https?://\S+|#[\w]+", " ", text or "")
    return " ".join("".join(c for c in unicodedata.normalize("NFD", text.casefold().replace("đ", "d"))
                            if unicodedata.category(c) != "Mn").split())


def _vietnamese(text):
    original = re.sub(r"https?://\S+|#[\w]+", " ", text or "")
    words = set(re.findall(r"[a-z]+", _plain(original)))
    common = {"minh", "cua", "voi", "duoc", "khong", "nhung", "mot", "nay", "cho", "cac", "ban", "cung", "trai", "nghiem", "chia", "se", "lam", "dep", "pham", "thuc", "nhan", "lien", "he", "nhat"}
    return bool(re.search(r"[ăâđêôơưĂÂĐÊÔƠƯ\u1ea0-\u1ef9]", original)) and len(words & common) >= 3


def vietnam_market_evidence(profile, posts):
    from app.core.location_resolver import resolve_location
    bio = profile.get("bio") or ""
    location = str(profile.get("location") or profile.get("country") or "")
    declared = str(profile.get("country_code") or "").upper()
    if declared and declared not in {"VN", "VNM", "VIETNAM", "VIET NAM"}:
        return {"matches": False, "market": "FOREIGN", "reasons": ["EXPLICIT_FOREIGN_COUNTRY"]}
    combined = _plain(location + " " + bio)
    foreign_base = re.search(r"\b(?:based in|living in|located in)\s+(?:usa|united states|uk|london|singapore|thailand|indonesia|malaysia|australia|canada|japan|korea)\b", combined)
    if foreign_base:
        return {"matches": False, "market": "FOREIGN", "reasons": ["EXPLICIT_FOREIGN_BASE"]}
    has_country = bool(re.search(r"\b(?:viet nam|vietnam)\b", _plain(location)))
    bio_country = bool(re.search(r"(?:^|[|;\n])\s*(?:📍\s*)?(?:viet nam|vietnam)\s*(?:$|[|;\n])", bio.casefold()))
    bio_country = bio_country or bool(re.search(r"\b(?:based in|living in|located in)\s+(?:viet nam|vietnam)\b|\bvietnam.based\b", _plain(bio)))
    city = resolve_location(bio=bio, extra_texts=[location], default="")
    # Do not read display name, username, search snippets or search terms as evidence.
    captions = {p.get("caption", "").strip() for p in posts if p.get("caption", "").strip()}
    vietnamese_posts = sum(_vietnamese(caption) for caption in captions)
    reasons = []
    if declared in {"VN", "VNM", "VIETNAM", "VIET NAM"}:
        reasons.append("DECLARED_VIETNAM")
    if has_country or bio_country or (city and (_vietnamese(bio) or vietnamese_posts >= 2)):
        reasons.append("PROFILE_VIETNAM_LOCATION")
    if _vietnamese(bio) and vietnamese_posts >= 2:
        reasons.append("VIETNAMESE_BIO_AND_REPEATED_CONTENT")
    return {"matches": bool(reasons), "market": "VN" if reasons else "UNKNOWN",
            "reasons": reasons or ["INSUFFICIENT_VIETNAM_EVIDENCE"], "vietnamese_posts": vietnamese_posts}
