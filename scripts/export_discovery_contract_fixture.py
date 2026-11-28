"""Export deterministic JavaMapper output for backend ingestion-to-discovery integration tests."""
import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.models import AIEnrichment, CreatorInfo, IngestPayload, Platform, Post
from app.mappers.java_mapper import JavaMapper


def fixtures():
    cases = []
    for identity, category, niche, expected, caption in [
        ("food", "Ẩm thực & Nhà hàng", "Thực phẩm", "FOOD", "Review mì trộn và quán ăn ngon"),
        ("home", "BEAUTY", "Gia dụng", "HOME", "Review máy hút bụi và đồ gia dụng"),
        ("pets", "LIFESTYLE", "Thú cưng", "PETS", "Review thức ăn và đồ chơi thú cưng"),
        ("tech", "TECHNOLOGY", "Điện tử", "TECH", "Review điện thoại và công nghệ"),
    ]:
        payload = IngestPayload(
            creator=CreatorInfo(platform=Platform.TIKTOK, externalId=f"contract-{identity}",
                username=f"contract_{identity}", displayName=f"Contract {identity}", followers=25000,
                profileUrl=f"https://www.tiktok.com/@contract_{identity}",
                avatarUrl="https://example.test/avatar.png", bio=caption),
            aiEnrichment=AIEnrichment(category=category, niche=niche, creatorType="koc", contentSummary=caption),
            source="PUBLIC", crawlRunId=f"contract-run-{identity}",
            observedAt=datetime(2026, 11, 27, tzinfo=UTC),
            recentPosts=[Post(platformPostId=f"{identity}-{i}", postUrl=f"https://example.test/post/{identity}-{i}",
                caption=caption, postedAt=f"2026-11-{26-i:02d}T00:00:00Z",
                views=10000, likes=100 if i < 3 else 9000, comments=20, shares=5,
                coverUrl="https://example.test/thumbnail.png") for i in range(4)],
        )
        with patch("app.mappers.java_mapper.embedding_service.get_search_embedding", return_value=[0.05] * 768), \
             patch("app.mappers.java_mapper.compute_data_confidence", return_value=0.9):
            cases.append({"expectedCategory": expected, "payload": JavaMapper.from_ingest_payload(payload)})
    return cases


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(fixtures(), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if args.output.read_text(encoding="utf-8") != content:
            raise SystemExit("JavaMapper fixture drift: re-export and run both contract suites")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    print("Crawler-to-discovery fixtures verified" if args.check else "Crawler-to-discovery fixtures exported")
