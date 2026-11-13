import json
import os
from typing import Any, Optional


RESULTS_FILE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "crawled_results.jsonl",
)


def get_crawled_facebook_identifiers() -> set[str]:
    """Snapshot of successfully collected profiles for discovery exclusion."""
    from app.crawlers.facebook_crawler import clean_facebook_username

    identifiers = set()
    if not os.path.exists(RESULTS_FILE_PATH):
        return identifiers
    with open(RESULTS_FILE_PATH, encoding="utf-8") as source:
        for line in source:
            try:
                creator = json.loads(line).get("creator")
            except (json.JSONDecodeError, AttributeError):
                continue
            if not isinstance(creator, dict) or str(creator.get("platform", "")).upper() != "FACEBOOK":
                continue
            for key in ("username", "externalId", "profileUrl"):
                value = creator.get(key)
                if value:
                    identifier = clean_facebook_username(str(value)).lstrip("@").casefold()
                    if identifier:
                        identifiers.add(identifier)
    return identifiers


def _normalize_item(raw: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Chuẩn hóa dữ liệu đọc từ JSONL để an toàn khi trả về client."""
    if not isinstance(raw, dict):
        return None

    creator = raw.get("creator")
    if not creator or not isinstance(creator, dict):
        return None

    return {
        "platform": creator.get("platform", "TIKTOK"),
        "username": creator.get("username", ""),
        "displayName": creator.get("displayName", ""),
        "profileUrl": creator.get("profileUrl", ""),
        "avatarUrl": creator.get("avatarUrl"),
        "followers": creator.get("followers", 0),
        "bio": creator.get("bio", ""),
        "publicEmail": creator.get("publicEmail"),
        "publicPhone": creator.get("publicPhone"),
        "bioCrossLinks": creator.get("bioCrossLinks", []),
        "recentPosts": raw.get("recentPosts", []),
        "aiEnrichment": raw.get("aiEnrichment", {}),
        "source": raw.get("source", ""),
        "crawlRunId": raw.get("crawlRunId", ""),
        "observedAt": raw.get("observedAt", ""),
        "collectedAt": raw.get("collectedAt", ""),
    }


def list_crawled_results(
    platform: Optional[str] = None,
    username: Optional[str] = None,
    creator_type: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """
    Đọc và lọc các kết quả cào thành công từ data/crawled_results.jsonl (mới nhất lên đầu).
    """
    if not os.path.exists(RESULTS_FILE_PATH):
        return [], 0

    records: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    clean_user = username.removeprefix("@").strip().lower() if username else None
    plat_filter = platform.strip().upper() if platform else None
    type_filter = creator_type.strip().upper() if creator_type else None

    # Đọc từ file (đọc tất cả các dòng, đảo ngược để lấy bản ghi mới nhất)
    try:
        with open(RESULTS_FILE_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return [], 0

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        item = _normalize_item(data)
        if not item:
            continue

        item_user = item["username"].lower()
        item_plat = item["platform"].upper()
        unique_key = f"{item_plat}:{item_user}"

        # Tránh trùng lặp profile nếu cào nhiều lần, giữ lần cào mới nhất
        if unique_key in seen_keys:
            continue

        # Áp dụng bộ lọc
        if plat_filter and item_plat != plat_filter:
            continue
        if clean_user and clean_user not in item_user:
            continue
        if type_filter:
            ai_data = item.get("aiEnrichment") or {}
            item_type = str(ai_data.get("creatorType", "")).upper()
            if item_type != type_filter:
                continue

        seen_keys.add(unique_key)
        records.append(item)

    total = len(records)
    paged = records[offset : offset + limit]
    return paged, total


def get_crawled_result_by_username(
    username: str, platform: Optional[str] = None
) -> Optional[dict[str, Any]]:
    """Tìm bản ghi cào chi tiết nhất của một creator theo username."""
    if not os.path.exists(RESULTS_FILE_PATH):
        return None

    clean_user = username.removeprefix("@").strip().lower()
    plat_filter = platform.strip().upper() if platform else None

    try:
        with open(RESULTS_FILE_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return None

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        item = _normalize_item(data)
        if not item:
            continue

        if item["username"].lower() == clean_user:
            if plat_filter is None or item["platform"].upper() == plat_filter:
                return item

    return None
