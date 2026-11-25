import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import crawl_router
from app.core.candidate_staging import CandidateStagingRecord, staging_repo
from app.services.preflight_service import PreflightService

app = FastAPI()
app.include_router(crawl_router.router)
client = TestClient(app)

MOCK_TIKTOK_AUTHOR = {
    "author_id": "6631682124809076737",
    "id_type": "TIKTOK_USER_ID",
    "observed_at": "2026-11-04T12:00:00+00:00",
    "unique_id": "domixi",
    "display_name": "Độ Mixi",
    "sec_uid": "MS4wLjABAAAA...",
    "avatar_url": "https://p16-sign-va.tiktokcdn.com/avatar.jpg",
    "followers": 6600000,
    "following": 150,
    "heart_count": 80000000,
    "video_count": 250,
    "signature": "Kênh chính thức của Độ Mixi",
    "bio": "Kênh chính thức của Độ Mixi",
    "bio_link": "",
    "is_verified": True,
    "profile_url": "https://www.tiktok.com/@domixi",
}


def test_resolve_author_id_matching():
    """Kiểm tra đối soát thành công khi Author ID trùng khớp 100% với expected_external_id."""
    with patch("app.services.preflight_service.fetch_tiktok_author_info", return_value=MOCK_TIKTOK_AUTHOR):
        res = PreflightService.resolve_author_id(
            platform="TIKTOK",
            username="domixi",
            expected_external_id="6631682124809076737",
        )
        assert res["status"] == "success"
        assert res["author_id"] == "6631682124809076737"
        assert res["is_matched"] is True
        assert "trùng khớp 100%" in res["message"]


def test_resolve_author_id_mismatch():
    """Kiểm tra khi Author ID không khớp với expected_external_id."""
    with patch("app.services.preflight_service.fetch_tiktok_author_info", return_value=MOCK_TIKTOK_AUTHOR):
        res = PreflightService.resolve_author_id(
            platform="TIKTOK",
            username="domixi",
            expected_external_id="9999999999999999999",
        )
        assert res["status"] == "success"
        assert res["is_matched"] is False
        assert "không khớp" in res["message"]


def test_resolve_author_id_and_update_staging_db():
    """
    Kiểm tra tình huống KOC đổi username:
    - Lúc đầu trong Staging DB là 'mixigaming' với external_id = '6631682124809076737'.
    - KOC đổi thành 'domixi'.
    - Khi resolve 'domixi', hệ thống nhận diện kênh cũ và tự cập nhật username thành 'domixi'.
    """
    staging_repo.upsert_candidate(
        CandidateStagingRecord(
            platform="TIKTOK",
            username="mixigaming",
            external_id="6631682124809076737",
            display_name="Độ Phùng Cũ",
        )
    )

    with patch("app.services.preflight_service.fetch_tiktok_author_info", return_value=MOCK_TIKTOK_AUTHOR):
        res = PreflightService.resolve_author_id(
            platform="TIKTOK",
            username="domixi",
            expected_external_id="6631682124809076737",
            auto_update_staging=True,
        )
        assert res["status"] == "success"
        assert res["is_matched"] is True
        assert res["previous_username"] == "mixigaming"

        # Kiểm tra xem DB Staging đã được update username mới chưa
        updated_cand = staging_repo.get_candidate_by_external_id("TIKTOK", "6631682124809076737")
        assert updated_cand is not None
        assert updated_cand.username == "domixi"


def test_api_post_tiktok_resolve_author():
    """Kiểm tra gọi API POST /api/v1/crawl/tiktok/resolve-author."""
    with patch("app.services.preflight_service.fetch_tiktok_author_info", return_value=MOCK_TIKTOK_AUTHOR):
        response = client.post(
            "/api/v1/crawl/tiktok/resolve-author",
            json={
                "username": "@domixi",
                "expected_external_id": "6631682124809076737",
                "auto_update_staging": True,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["author_id"] == "6631682124809076737"
        assert data["is_matched"] is True
        assert data["creator_info"]["display_name"] == "Độ Mixi"


def test_api_post_tiktok_resolve_author_requires_username():
    with patch("app.services.preflight_service.fetch_tiktok_author_info") as fetch:
        response = client.post(
            "/api/v1/crawl/tiktok/resolve-author",
            json={"username": "@", "auto_update_staging": False},
        )
    assert response.status_code == 400
    fetch.assert_not_called()


if __name__ == "__main__":
    print("Running test_resolve_author_id_matching...")
    test_resolve_author_id_matching()
    print("Running test_resolve_author_id_mismatch...")
    test_resolve_author_id_mismatch()
    print("Running test_resolve_author_id_and_update_staging_db...")
    test_resolve_author_id_and_update_staging_db()
    print("Running test_api_post_tiktok_resolve_author...")
    test_api_post_tiktok_resolve_author()
    print("Running test_api_post_tiktok_resolve_author_requires_username...")
    test_api_post_tiktok_resolve_author_requires_username()
    print("\n=============================================")
    print("SUCCESS: ALL 5 UNIT TESTS PASSED WITH 100%!")
    print("=============================================")


def test_resolver_without_staging_update_preserves_existing_username():
    author = dict(MOCK_TIKTOK_AUTHOR, author_id="7631682124809076737")
    staging_repo.upsert_candidate(
        CandidateStagingRecord(
            platform="TIKTOK",
            external_id=author["author_id"],
            username="mixigaming",
            display_name="Original display name",
        )
    )
    with patch("app.services.preflight_service.fetch_tiktok_author_info", return_value=author):
        response = client.post(
            "/api/v1/crawl/tiktok/resolve-author",
            json={"username": "domixi", "auto_update_staging": False},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["id_type"] == "TIKTOK_USER_ID"
    assert data["observed_at"] == author["observed_at"]
    assert data["previous_username"] == "mixigaming"
    after = staging_repo.get_candidate_by_external_id("TIKTOK", author["author_id"])
    assert after is not None
    assert after.username == "mixigaming"
    assert after.display_name == "Original display name"


def test_invalid_identity_returns_not_found():
    for invalid in (
        {"unique_id": ""},
        {"author_id": "secUid-value"},
        {"id_type": ""},
        {"observed_at": ""},
    ):
        info = dict(MOCK_TIKTOK_AUTHOR, **invalid)
        with patch("app.services.preflight_service.fetch_tiktok_author_info", return_value=info):
            response = client.post(
                "/api/v1/crawl/tiktok/resolve-author",
                json={"username": "domixi", "auto_update_staging": False},
            )
        assert response.status_code == 404


def test_fetcher_reads_actual_ssr_identity_and_observation_time():
    from app.crawlers.preflight_fetcher import fetch_tiktok_author_info
    raw = {"__DEFAULT_SCOPE__": {"webapp.user-detail": {"userInfo": {
        "user": {"id": "123", "uniqueId": "actual_name"}, "stats": {}}}}}
    before = datetime.now(UTC)
    with patch("app.crawlers.preflight_fetcher.fetch_tiktok_ssr_raw_data", return_value=raw):
        result = fetch_tiktok_author_info("input_name")
    assert result["unique_id"] == "actual_name"
    assert result["author_id"] == "123"
    assert result["id_type"] == "TIKTOK_USER_ID"
    observed_at = datetime.fromisoformat(result["observed_at"])
    assert observed_at.utcoffset() == timedelta(0)
    assert before <= observed_at <= datetime.now(UTC)


def test_fetcher_rejects_missing_or_invalid_identity_without_fallback():
    from app.crawlers.preflight_fetcher import fetch_tiktok_author_info
    for user in [{"id": "123"}, {"uniqueId": "domixi"},
                 {"id": "123", "uniqueId": 123},
                 {"id": "0", "uniqueId": "domixi"},
                 {"id": "secUid-value", "uniqueId": "domixi"},
                 {"id": "1" * 21, "uniqueId": "domixi"}]:
        raw = {"__DEFAULT_SCOPE__": {"webapp.user-detail": {"userInfo": {"user": user}}}}
        with patch("app.crawlers.preflight_fetcher.fetch_tiktok_ssr_raw_data", return_value=raw):
            assert fetch_tiktok_author_info("domixi") is None


def test_each_resolution_fetches_provider_again():
    with patch("app.services.preflight_service.fetch_tiktok_author_info", return_value=MOCK_TIKTOK_AUTHOR) as fetch:
        for _ in range(2):
            result = PreflightService.resolve_author_id(username="domixi", auto_update_staging=False)
            assert result["status"] == "success"
        assert fetch.call_count == 2
