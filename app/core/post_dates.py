"""Publication evidence only: never substitute crawl time for a missing post date."""
from datetime import UTC, datetime
import re


def normalize_post_date(value: object, *, now: datetime | None = None) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, datetime):
            parsed = value
        else:
            text = str(value).strip()
            if re.fullmatch(r"\d{10}(?:\.\d+)?|\d{13}", text):
                epoch = float(text) / (1000 if len(text) == 13 else 1)
                parsed = datetime.fromtimestamp(epoch, UTC)
            elif re.match(r"^\d{4}-\d{2}-\d{2}(?:$|[T ])", text):
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            else:
                return None
        parsed = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        if parsed > (now or datetime.now(UTC)):
            return None
        return parsed.isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def post_evidence_order(post) -> tuple[bool, float]:
    """Order dated posts newest first; retain undated evidence after them."""
    date = normalize_post_date(post.postedAt)
    return date is not None, datetime.fromisoformat(date.replace("Z", "+00:00")).timestamp() if date else 0
