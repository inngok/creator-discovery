import os
from datetime import UTC, datetime, timedelta
import redis

from app.ai.rules.rule_config import Decision, ProcessingStatus
from app.core.candidate_staging import staging_repo
from app.core.logger import setup_logger

logger = setup_logger("deduplicator")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# In-memory fallback if Redis is unreachable (for local testing/offline mode)
_IN_MEMORY_LOCKS: dict[str, datetime] = {}


def get_redis_client() -> redis.Redis | None:
    try:
        r = redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=2.0)
        r.ping()
        return r
    except Exception as e:
        logger.debug(f"Redis is not available, using in-memory lock: {e}")
        return None


def acquire_candidate_lock(platform: str, username: str, ttl_seconds: int = 300) -> bool:
    """
    Khóa phân tán nguyên tử (Atomic Lock) chống 2 worker xử lý cùng 1 username.
    Sử dụng lệnh Redis 'SET key val NX EX ttl'.
    """
    clean_user = username.removeprefix("@").strip().lower()
    lock_key = f"candidate:lock:{platform.upper()}:{clean_user}"
    r = get_redis_client()

    if r:
        try:
            acquired = r.set(lock_key, "locked", nx=True, ex=ttl_seconds)
            return bool(acquired)
        except Exception as e:
            logger.warning(f"Error acquiring Redis lock for {lock_key}: {e}")

    # Fallback in-memory lock
    now = datetime.now(UTC)
    if lock_key in _IN_MEMORY_LOCKS:
        if _IN_MEMORY_LOCKS[lock_key] > now:
            return False  # Vẫn đang bị khóa
    _IN_MEMORY_LOCKS[lock_key] = now + timedelta(seconds=ttl_seconds)
    return True


def release_candidate_lock(platform: str, username: str):
    """Giải phóng khóa phân tán sau khi xử lý xong."""
    clean_user = username.removeprefix("@").strip().lower()
    lock_key = f"candidate:lock:{platform.upper()}:{clean_user}"
    r = get_redis_client()

    if r:
        try:
            r.delete(lock_key)
        except Exception as e:
            logger.warning(f"Error deleting Redis lock for {lock_key}: {e}")

    _IN_MEMORY_LOCKS.pop(lock_key, None)


def should_enqueue_preflight(platform: str, username: str, cooldown_days: int = 7) -> tuple[bool, str]:
    """
    Kiểm tra xem Candidate có đủ điều kiện đưa vào hàng đợi Preflight không:
    - Nếu đã có Decision AUTO_REJECT gần đây (< 7 ngày) -> Bỏ qua.
    - Nếu đã được INGESTED thành công gần đây -> Bỏ qua.
    - Nếu đang chạy PREFLIGHT_RUNNING hoặc DEEP_CRAWLING -> Bỏ qua.
    """
    record = staging_repo.get_candidate(platform, username)
    if not record:
        return True, "NEW_CANDIDATE"

    # Kiểm tra trạng thái đang chạy
    if record.processing_status in [ProcessingStatus.PREFLIGHT_RUNNING, ProcessingStatus.DEEP_CRAWLING]:
        return False, f"CURRENTLY_PROCESSING_{record.processing_status.value}"

    # Kiểm tra thời gian cooldown cho tài khoản bị Reject
    try:
        last_dt = datetime.fromisoformat(record.last_checked_at.replace("Z", "+00:00"))
        if datetime.now(UTC) - last_dt < timedelta(days=cooldown_days):
            if record.decision == Decision.AUTO_REJECT:
                return False, "RECENTLY_AUTO_REJECTED"
            if record.processing_status == ProcessingStatus.INGESTED:
                return False, "ALREADY_INGESTED"
    except Exception:
        pass

    return True, "ELIGIBLE_FOR_RECHECK"
