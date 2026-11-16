import json
import os
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from typing import Any
from pydantic import BaseModel, Field

from app.ai.rules.rule_config import Decision, ProcessingStatus, RULE_VERSION
from app.core.logger import setup_logger

logger = setup_logger("candidate_staging")

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
if not os.path.exists(DB_DIR):
    os.makedirs(DB_DIR, exist_ok=True)

SQLITE_STAGING_PATH = os.path.join(DB_DIR, "candidate_staging.db")


class CandidateStagingRecord(BaseModel):
    """Bản ghi Staging của Candidate phục vụ phân loại và điều phối Deep Crawl."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    platform: str = "TIKTOK"
    external_id: str = ""
    username: str
    display_name: str = ""
    profile_url: str = ""
    processing_status: ProcessingStatus = ProcessingStatus.DISCOVERED
    decision: Decision = Decision.SKIP_INSUFFICIENT
    primary_type: str = "UNKNOWN"
    reason_codes: list[str] = Field(default_factory=list)
    matched_signals: list[str] = Field(default_factory=list)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    rule_version: str = RULE_VERSION
    retry_count: int = 0
    last_checked_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class CandidateStagingRepository:
    """
    Quản lý lưu trữ Candidate Staging.
    - Hỗ trợ SQLite ở chế độ WAL (Write-Ahead Logging) cho concurrency an toàn.
    - Sẵn sàng tích hợp PostgreSQL qua biến môi trường DATABASE_URL.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_sqlite()
        return cls._instance

    from contextlib import contextmanager

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(SQLITE_STAGING_PATH, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        try:
            yield conn
        finally:
            conn.close()

    def _init_sqlite(self):
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candidate_staging (
                    id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    external_id TEXT,
                    username TEXT NOT NULL,
                    display_name TEXT,
                    profile_url TEXT,
                    processing_status TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    primary_type TEXT NOT NULL,
                    reason_codes TEXT,
                    matched_signals TEXT,
                    evidence_summary TEXT,
                    rule_version TEXT,
                    retry_count INTEGER DEFAULT 0,
                    last_checked_at TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    UNIQUE(platform, username)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_stg_plat_user ON candidate_staging(platform, username);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_stg_decision ON candidate_staging(decision);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_stg_status ON candidate_staging(processing_status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_stg_plat_extid ON candidate_staging(platform, external_id);")
            conn.commit()

    def upsert_candidate(self, record: CandidateStagingRecord) -> CandidateStagingRecord:
        now_str = datetime.now(UTC).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO candidate_staging (
                    id, platform, external_id, username, display_name, profile_url,
                    processing_status, decision, primary_type, reason_codes,
                    matched_signals, evidence_summary, rule_version, retry_count,
                    last_checked_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, username) DO UPDATE SET
                    external_id = COALESCE(excluded.external_id, candidate_staging.external_id),
                    display_name = COALESCE(excluded.display_name, candidate_staging.display_name),
                    profile_url = COALESCE(excluded.profile_url, candidate_staging.profile_url),
                    processing_status = excluded.processing_status,
                    decision = excluded.decision,
                    primary_type = excluded.primary_type,
                    reason_codes = excluded.reason_codes,
                    matched_signals = excluded.matched_signals,
                    evidence_summary = excluded.evidence_summary,
                    rule_version = excluded.rule_version,
                    retry_count = excluded.retry_count,
                    last_checked_at = excluded.last_checked_at,
                    updated_at = ?
            """, (
                record.id,
                record.platform,
                record.external_id,
                record.username,
                record.display_name,
                record.profile_url,
                record.processing_status.value if hasattr(record.processing_status, "value") else str(record.processing_status),
                record.decision.value if hasattr(record.decision, "value") else str(record.decision),
                record.primary_type,
                json.dumps(record.reason_codes, ensure_ascii=False),
                json.dumps(record.matched_signals, ensure_ascii=False),
                json.dumps(record.evidence_summary, ensure_ascii=False),
                record.rule_version,
                record.retry_count,
                record.last_checked_at,
                record.created_at,
                now_str,
                now_str,
            ))
            conn.commit()
        return record

    def get_candidate(self, platform: str, username: str) -> CandidateStagingRecord | None:
        clean_user = username.removeprefix("@").strip()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM candidate_staging WHERE platform = ? AND (username = ? OR username = ?)",
                (platform, clean_user, f"@{clean_user}")
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_record(row)

    def update_status_and_decision(
        self,
        platform: str,
        username: str,
        status: ProcessingStatus,
        decision: Decision | None = None,
        reason_codes: list[str] | None = None,
        primary_type: str | None = None,
    ):
        clean_user = username.removeprefix("@").strip()
        now_str = datetime.now(UTC).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "UPDATE candidate_staging SET processing_status = ?, updated_at = ?"
            params: list[Any] = [status.value if hasattr(status, "value") else str(status), now_str]

            if decision is not None:
                query += ", decision = ?"
                params.append(decision.value if hasattr(decision, "value") else str(decision))
            if primary_type is not None:
                query += ", primary_type = ?"
                params.append(primary_type)
            if reason_codes is not None:
                query += ", reason_codes = ?"
                params.append(json.dumps(reason_codes, ensure_ascii=False))

            query += " WHERE platform = ? AND (username = ? OR username = ?)"
            params.extend([platform, clean_user, f"@{clean_user}"])

            cursor.execute(query, params)
            conn.commit()

    def get_candidate_by_id(self, candidate_id: str) -> CandidateStagingRecord | None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM candidate_staging WHERE id = ?",
                (candidate_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_record(row)

    def count_by_decision(self, decision: Decision, platform: str | None = None) -> int:
        dec_val = decision.value if hasattr(decision, "value") else str(decision)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if platform:
                cursor.execute(
                    "SELECT COUNT(*) FROM candidate_staging WHERE decision = ? AND platform = ?",
                    (dec_val, platform.upper())
                )
            else:
                cursor.execute(
                    "SELECT COUNT(*) FROM candidate_staging WHERE decision = ?",
                    (dec_val,)
                )
            row = cursor.fetchone()
            return row[0] if row else 0

    def list_by_decision(
        self,
        decision: Decision,
        platform: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CandidateStagingRecord]:
        dec_val = decision.value if hasattr(decision, "value") else str(decision)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if platform:
                cursor.execute(
                    "SELECT * FROM candidate_staging WHERE decision = ? AND platform = ? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    (dec_val, platform.upper(), limit, offset)
                )
            else:
                cursor.execute(
                    "SELECT * FROM candidate_staging WHERE decision = ? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    (dec_val, limit, offset)
                )
            rows = cursor.fetchall()
            return [self._row_to_record(r) for r in rows]

    def get_stale_ingested_candidates(self, limit: int = 30) -> list[CandidateStagingRecord]:
        """Lấy danh sách các KOC đã INGESTED có thời gian cập nhật cũ nhất để cào làm mới (refresh)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM candidate_staging 
                WHERE processing_status = 'INGESTED'
                ORDER BY updated_at ASC 
                LIMIT ?
                """,
                (limit,)
            )
            rows = cursor.fetchall()
            return [self._row_to_record(r) for r in rows]

    def get_candidate_by_external_id(
        self, platform: str, external_id: str
    ) -> CandidateStagingRecord | None:
        """Tìm candidate theo platform và external_id (Author ID số cố định)."""
        clean_ext_id = str(external_id).strip()
        if not clean_ext_id:
            return None
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM candidate_staging WHERE platform = ? AND external_id = ? ORDER BY updated_at DESC LIMIT 1",
                (platform.upper(), clean_ext_id),
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_record(row)
            return None

    def update_candidate_username(
        self,
        platform: str,
        external_id: str,
        new_username: str,
        new_display_name: str | None = None,
    ) -> bool:
        """Cập nhật username (và display_name) mới khi KOC đổi tên trên kênh."""
        clean_user = new_username.removeprefix("@").strip()
        clean_ext_id = str(external_id).strip()
        if not clean_ext_id or not clean_user:
            return False

        target_record = self.get_candidate_by_external_id(platform, clean_ext_id)
        if not target_record:
            return False

        now_str = datetime.now(UTC).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 1. Xóa mọi bản ghi khác đang chiếm new_username trên cùng platform để tránh vi phạm UNIQUE
            cursor.execute(
                "DELETE FROM candidate_staging WHERE platform = ? AND (username = ? OR username = ?) AND id != ?",
                (platform.upper(), clean_user, f"@{clean_user}", target_record.id),
            )
            # 2. Xóa các bản ghi trùng lặp thừa có cùng external_id
            cursor.execute(
                "DELETE FROM candidate_staging WHERE platform = ? AND external_id = ? AND id != ?",
                (platform.upper(), clean_ext_id, target_record.id),
            )
            # 3. Cập nhật bản ghi chính xác theo khóa chính id
            new_profile_url = (
                f"https://www.tiktok.com/@{clean_user}"
                if platform.upper() == "TIKTOK"
                else f"https://www.facebook.com/{clean_user}"
            )
            if new_display_name:
                cursor.execute(
                    """
                    UPDATE candidate_staging
                    SET username = ?, display_name = ?, profile_url = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (clean_user, new_display_name, new_profile_url, now_str, target_record.id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE candidate_staging
                    SET username = ?, profile_url = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (clean_user, new_profile_url, now_str, target_record.id),
                )
            conn.commit()
            return cursor.rowcount > 0

    def get_summary_stats(self) -> dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM candidate_staging")
            total = cursor.fetchone()[0]

            cursor.execute("SELECT processing_status, COUNT(*) FROM candidate_staging GROUP BY processing_status")
            status_counts = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.execute("SELECT decision, COUNT(*) FROM candidate_staging GROUP BY decision")
            decision_counts = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.execute("SELECT platform, COUNT(*) FROM candidate_staging GROUP BY platform")
            platform_counts = {row[0]: row[1] for row in cursor.fetchall()}

            active_crawls = status_counts.get("PREFLIGHT_RUNNING", 0) + status_counts.get("DEEP_CRAWLING", 0)
            completed = status_counts.get("INGESTED", 0) + status_counts.get("PREFLIGHT_DONE", 0)
            failed = status_counts.get("FAILED", 0)

            return {
                "total_candidates": total,
                "active_crawls": active_crawls,
                "completed": completed,
                "failed": failed,
                "by_status": status_counts,
                "by_decision": decision_counts,
                "by_platform": platform_counts,
            }

    def list_candidates(
        self, limit: int = 50, offset: int = 0, platform: str | None = None
    ) -> tuple[list[CandidateStagingRecord], int]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if platform:
                cursor.execute("SELECT COUNT(*) FROM candidate_staging WHERE platform = ?", (platform.upper(),))
                total = cursor.fetchone()[0]
                cursor.execute(
                    "SELECT * FROM candidate_staging WHERE platform = ? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    (platform.upper(), limit, offset)
                )
            else:
                cursor.execute("SELECT COUNT(*) FROM candidate_staging")
                total = cursor.fetchone()[0]
                cursor.execute(
                    "SELECT * FROM candidate_staging ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    (limit, offset)
                )
            rows = cursor.fetchall()
            return [self._row_to_record(r) for r in rows], total

    def get_candidate_by_identifier(
        self, identifier: str, platform: str | None = None
    ) -> CandidateStagingRecord | None:
        clean_id = identifier.removeprefix("@").strip()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if platform:
                cursor.execute(
                    "SELECT * FROM candidate_staging WHERE platform = ? AND (username = ? OR username = ? OR external_id = ?) ORDER BY updated_at DESC LIMIT 1",
                    (platform.upper(), clean_id, f"@{clean_id}", clean_id)
                )
            else:
                cursor.execute(
                    "SELECT * FROM candidate_staging WHERE (username = ? OR username = ? OR external_id = ?) ORDER BY updated_at DESC LIMIT 1",
                    (clean_id, f"@{clean_id}", clean_id)
                )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_record(row)

    def _row_to_record(self, row: sqlite3.Row) -> CandidateStagingRecord:
        return CandidateStagingRecord(
            id=row["id"],
            platform=row["platform"],
            external_id=row["external_id"] or "",
            username=row["username"],
            display_name=row["display_name"] or "",
            profile_url=row["profile_url"] or "",
            processing_status=ProcessingStatus(row["processing_status"]),
            decision=Decision(row["decision"]),
            primary_type=row["primary_type"],
            reason_codes=json.loads(row["reason_codes"]) if row["reason_codes"] else [],
            matched_signals=json.loads(row["matched_signals"]) if row["matched_signals"] else [],
            evidence_summary=json.loads(row["evidence_summary"]) if row["evidence_summary"] else {},
            rule_version=row["rule_version"] or RULE_VERSION,
            retry_count=row["retry_count"] or 0,
            last_checked_at=row["last_checked_at"] or "",
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )


staging_repo = CandidateStagingRepository()
