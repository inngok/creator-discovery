"""Public-source excerpts for search; generated prose is not evidence."""
import re

from app.ai.fallback_labels import resolve_fallback_labels

INSUFFICIENT_SUMMARY = "Chưa đủ nội dung công khai để tóm tắt."
PRODUCT_GROUPS = frozenset({
    "Mỹ phẩm", "Trang phục", "Sức khỏe và Làm đẹp", "Gia dụng",
    "Thực phẩm", "Điện tử", "Mẹ và Bé",
})


def resolve_product_groups(items, sources):
    """Keep at most three distinct product groups with evidence for each group."""
    if not isinstance(items, list):
        return "UNKNOWN"
    labels = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        if label in PRODUCT_GROUPS and label not in labels and has_label_evidence(
            {"labelEvidence": item.get("evidence")}, sources
        ):
            labels.append(label)
        if len(labels) == 3:
            break
    return ",".join(labels) if labels else "UNKNOWN"


def _clean_source(value: str) -> str:
    text = re.sub(r"https?://\S+|www\.\S+|[\w.+-]+@[\w.-]+\.\w+", " ", value or "")
    text = re.sub(r"(?<!\w)\+?\d[\d .()-]{7,}\d(?!\w)", " ", text)
    return re.sub(r"\s+", " ", text).strip(" |,;:-")


def build_content_summary(bio: str, captions: list[str]) -> str:
    excerpts = []
    for source in [*captions, bio]:
        excerpt = _clean_source(source)
        if len(excerpt) >= 12 and excerpt not in excerpts:
            excerpts.append(excerpt[:220])
        if len(excerpts) == 3:
            break
    return " | ".join(excerpts)[:600] if excerpts else INSUFFICIENT_SUMMARY


def has_label_evidence(ai_data, sources):
    quotes = ai_data.get("labelEvidence")
    return isinstance(quotes, list) and bool(quotes) and all(
        isinstance(quote, str) and len(quote.strip()) >= 12
        and _clean_source(quote) == quote.strip()
        and any(quote in source for source in sources if source)
        for quote in quotes
    )


def resolve_grounded_fallback_labels(category, niche, ai_data, sources):
    if "nicheLabels" in ai_data:
        if has_label_evidence(ai_data, sources):
            category, _ = resolve_fallback_labels(category, niche, ai_data)
        return category, resolve_product_groups(ai_data["nicheLabels"], sources)
    if not has_label_evidence(ai_data, sources):
        return category, niche
    return resolve_fallback_labels(category, niche, ai_data)
