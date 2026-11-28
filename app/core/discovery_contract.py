"""Runtime rules from the backend-owned contract; sync using crawler scripts/sync_discovery_contract.py."""
import json
import unicodedata
from pathlib import Path

CONTRACT = json.loads((Path(__file__).resolve().parents[1] / "contracts/creator-discovery-v1.json").read_text(encoding="utf-8"))
VERSION = CONTRACT["version"]
MIN_FOLLOWERS = CONTRACT["minFollowers"]
MAX_RECENT_POSTS = CONTRACT["maxRecentPosts"]
EMBEDDING_DIMENSIONS = CONTRACT["embeddingDimensions"]


def _key(value):
    return " ".join(unicodedata.normalize("NFC", str(value or "")).strip().lower().split())


LABELS = {
    _key(alias): label
    for label in [*CONTRACT["categories"], *CONTRACT["niches"]]
    for alias in [label["code"], *label["aliases"]]
}
CATEGORY_CODES = frozenset(label["code"] for label in CONTRACT["categories"])


def canonical_label(value):
    if _key(value) in {"", "unknown", "general", "null", "none", "n/a"}:
        return "UNKNOWN"
    return LABELS.get(_key(value), {}).get("code", str(value).strip())


def parent_category(value):
    label = LABELS.get(_key(value))
    return label.get("category", label["code"]) if label else None


def normalize_classification(category, niche):
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
    label = LABELS.get(_key(category))
    if label and label.get("category"):
        # A requested subtopic must not turn into seeds for its entire parent category.
        return list(label["aliases"])
    parent = parent_category(category)
    return next((list(label["searchTerms"]) for label in CONTRACT["categories"] if label["code"] == parent), [category])
