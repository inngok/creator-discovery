"""Runtime rules from the backend-owned contract; dynamically refreshed from backend with local JSON fallback."""
import json
import os
import time
import unicodedata
from pathlib import Path

import httpx

_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "contracts/creator-discovery-v1.json"
CONTRACT = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
VERSION = CONTRACT["version"]
MIN_FOLLOWERS = CONTRACT["minFollowers"]
MAX_RECENT_POSTS = CONTRACT["maxRecentPosts"]
EMBEDDING_DIMENSIONS = CONTRACT["embeddingDimensions"]

_CACHE_TTL_SECONDS = 300
_last_refresh_ts = 0.0


def _key(value):
    return " ".join(unicodedata.normalize("NFC", str(value or "")).strip().lower().split())


def _build_labels(contract):
    return {
        _key(alias): label
        for label in [*contract.get("categories", []), *contract.get("niches", [])]
        for alias in [label["code"], *label.get("aliases", [])]
    }


LABELS = _build_labels(CONTRACT)
CATEGORY_CODES = frozenset(label["code"] for label in CONTRACT["categories"])


def _contract_endpoint() -> str | None:
    ingest_url = os.environ.get("JAVA_BACKEND_URL", "").strip()
    if not ingest_url:
        return None
    base = ingest_url.split("/creators/ingest")[0].rstrip("/")
    return f"{base}/taxonomies/creator-discovery-contract"


def refresh_contract(force: bool = False) -> bool:
    """Refresh contract from backend taxonomy endpoint if TTL expired; falls back to local JSON."""
    global CONTRACT, VERSION, MIN_FOLLOWERS, MAX_RECENT_POSTS, EMBEDDING_DIMENSIONS, LABELS, CATEGORY_CODES, _last_refresh_ts
    now = time.monotonic()
    if not force and (now - _last_refresh_ts) < _CACHE_TTL_SECONDS:
        return False
    _last_refresh_ts = now
    endpoint = _contract_endpoint()
    if not endpoint:
        return False
    try:
        resp = httpx.get(endpoint, timeout=2.0)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and isinstance(data.get("categories"), list):
                CONTRACT = data
                VERSION = CONTRACT.get("version", VERSION)
                MIN_FOLLOWERS = CONTRACT.get("minFollowers", MIN_FOLLOWERS)
                MAX_RECENT_POSTS = CONTRACT.get("maxRecentPosts", MAX_RECENT_POSTS)
                EMBEDDING_DIMENSIONS = CONTRACT.get("embeddingDimensions", EMBEDDING_DIMENSIONS)
                LABELS = _build_labels(CONTRACT)
                CATEGORY_CODES = frozenset(label["code"] for label in CONTRACT["categories"])
                return True
    except Exception:
        pass
    return False


def canonical_label(value):
    refresh_contract()
    if _key(value) in {"", "unknown", "general", "null", "none", "n/a"}:
        return "UNKNOWN"
    return LABELS.get(_key(value), {}).get("code", str(value).strip())


def parent_category(value):
    refresh_contract()
    label = LABELS.get(_key(value))
    return label.get("category", label["code"]) if label else None


def normalize_classification(category, niche):
    refresh_contract()
    parts = [niche] if _key(niche) in LABELS else str(niche or "").split(",")
    parts = list(dict.fromkeys(canonical_label(part) for part in parts if canonical_label(part) != "UNKNOWN"))
    parents = {parent_category(part) for part in parts} - {None}
    category = parent_category(category) or canonical_label(category)
    if len(parents) == 1:
        category = next(iter(parents))
    elif len(parents) > 1 and category not in parents:
        category = "UNKNOWN"
    return category, ", ".join(parts) or "UNKNOWN"


def search_terms(category):
    refresh_contract()
    label = LABELS.get(_key(category))
    if label and label.get("category"):
        # A requested subtopic must not turn into seeds for its entire parent category.
        return list(label["aliases"])
    parent = parent_category(category)
    return next((list(label["searchTerms"]) for label in CONTRACT["categories"] if label["code"] == parent), [category])
