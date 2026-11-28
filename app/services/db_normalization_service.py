"""Preview and repair stored data without recrawling, inventing freshness or running classification models."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
import tempfile
import os
import uuid

from app.core.discovery_contract import CONTRACT, LABELS, VERSION, canonical_label, normalize_classification
from app.core.post_dates import normalize_post_date
from app.core.postgres_connection import connect_postgres

REPORT_DIR = Path(__file__).resolve().parents[2] / "data/db_normalization"
CONTRACT_HASH = hashlib.sha256(json.dumps(CONTRACT, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
FIELDS = {"category": "category", "niche": "niche", "creatorType": "creator_type",
          "engagementRate": "engagement_rate", "contentSummary": "content_summary",
          "freshnessStatus": "freshness_status", "dataConfidence": "data_confidence"}
SELECT = '''SELECT c.id::text AS "creatorId", c.platform, c.user_name AS username,
    c.display_name AS "displayName", c.discovery_hidden AS "discoveryHidden",
    c.master_id::text AS "masterId", c.source_type AS "sourceType", c.embedding::text AS embedding,
    m.id::text AS "metricId", m.category, m.niche, m.creator_type AS "creatorType", m.followers,
    m.avg_views AS "avgViews", m.avg_likes AS "avgLikes", m.avg_comments AS "avgComments",
    m.avg_shares AS "avgShares", m.engagement_rate AS "engagementRate",
    m.content_summary AS "contentSummary", m.data_confidence AS "dataConfidence",
    m.freshness_status AS "freshnessStatus", m.collected_at AS "collectedAt",
    m.observed_at AS "observedAt", m.crawl_run_id AS "crawlRunId",
    m.quality_reviewed_at AS "qualityReviewedAt", m.quality_reviewed_by::text AS "qualityReviewedBy"
    FROM creator c LEFT JOIN public_creator_metric m ON m.id=(
        SELECT latest.id FROM public_creator_metric latest WHERE latest.creator_id=c.id
        ORDER BY latest.collected_at DESC, latest.id DESC LIMIT 1)'''


def serial(value):
    if isinstance(value, (datetime, Decimal, uuid.UUID)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    raise TypeError(type(value).__name__)


def plain(value):
    return json.loads(json.dumps(value, default=serial, ensure_ascii=False))


def fingerprint(row):
    return hashlib.sha256(json.dumps(plain(row), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def number(value):
    try:
        result = Decimal(str(value)) if value is not None else None
        return result if result is not None and result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def assess_record(row, *, now=None):
    """Only changes derivable from stored facts. Semantic correctness is never asserted here."""
    now = now or datetime.now(UTC)
    before = plain(row)
    changes, reasons, review = {}, [], []
    if not row.get("metricId"):
        return {"creatorId": row["creatorId"], "metricId": None, "before": before,
                "fingerprint": fingerprint(row), "changes": {}, "clearEmbedding": False,
                "reasons": [], "reviewReasons": ["NO_METRIC_SNAPSHOT"], "status": "REVIEW_REQUIRED"}
    reviewed = bool(row.get("qualityReviewedAt") or row.get("qualityReviewedBy"))

    def propose(field, value, reason):
        previous = row.get(field)
        equal = number(previous) == number(value) if field in {"engagementRate", "dataConfidence"} else previous == value
        if not equal:
            changes[field] = value
            reasons.append(reason)

    category, niche = normalize_classification(row.get("category"), row.get("niche"))
    if not reviewed:
        propose("category", category, "CANONICAL_CATEGORY_FROM_OBSERVED_NICHE")
        propose("niche", niche, "CANONICAL_NICHE_ALIASES")
        creator_type = str(row.get("creatorType") or "UNKNOWN").strip().upper()
        propose("creatorType", creator_type, "CANONICAL_CREATOR_TYPE")
        if creator_type not in {"KOL", "KOC", "BRAND", "ORGANIZATION", "SPAM", "USER", "REJECTED"}:
            review.append("CREATOR_TYPE_NEEDS_EVIDENCE")
        if category == "UNKNOWN" or niche == "UNKNOWN":
            review.append("CLASSIFICATION_NEEDS_CONTENT_REVIEW")
        confidence = number(row.get("dataConfidence"))
        if confidence is None and row.get("dataConfidence") is not None:
            propose("dataConfidence", "0", "INVALID_CONFIDENCE_REQUIRES_REVIEW")
            review.append("INVALID_DATA_CONFIDENCE")
        elif confidence is None:
            review.append("MISSING_DATA_CONFIDENCE")
        elif not Decimal("0") <= confidence <= Decimal("1"):
            propose("dataConfidence", "0", "INVALID_CONFIDENCE_REQUIRES_REVIEW")
            review.append("INVALID_DATA_CONFIDENCE")
        elif confidence < Decimal(str(CONTRACT["minDataConfidence"])):
            review.append("LOW_DATA_CONFIDENCE")

    counts = [number(row.get(field)) for field in ("avgLikes", "avgComments", "avgShares")]
    followers = number(row.get("followers"))
    if followers is not None and followers > 0 and all(value is not None and value >= 0 for value in counts):
        ratio = (sum(counts) / followers).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        if ratio <= Decimal("999.9999"):
            propose("engagementRate", str(ratio), "ER_RATIO_FROM_STORED_COUNTS")
        else:
            review.append("ER_OUTSIDE_STORAGE_RANGE")
    elif row.get("engagementRate") is not None:
        review.append("ER_UNIT_UNVERIFIABLE_WITHOUT_COUNTS")

    observed = normalize_post_date(row.get("observedAt"), now=now)
    if not observed:
        review.append("MISSING_OR_INVALID_OBSERVATION_TIME")
    elif row.get("freshnessStatus") == "FRESH" and now - datetime.fromisoformat(observed.replace("Z", "+00:00")) > timedelta(days=7):
        propose("freshnessStatus", "STALE", "OLD_OBSERVATION_REMAINS_OLD")
        review.append("OLD_OBSERVATION_RECRAWL_NEEDED")

    posts = row.get("posts") or []
    def order(post):
        date = normalize_post_date(post.get("postedAt"), now=now)
        return date is not None, date or "", post.get("platformPostId") or ""
    recent = sorted(posts, key=order, reverse=True)[:CONTRACT["maxRecentPosts"]]
    captions = [p.get("caption") or "" for p in recent if len((p.get("caption") or "").strip()) >= 12]
    if captions:
        from app.ai.grounded_enrichment import build_content_summary
        propose("contentSummary", build_content_summary("", captions), "SUMMARY_FROM_STORED_POST_EXCERPTS")
    else:
        review.append("NO_PUBLIC_POST_EVIDENCE_RECRAWL_NEEDED")
    clear_embedding = bool(row.get("embedding") and ("contentSummary" in changes or
        ("category" in changes and canonical_label(row.get("category")) != category)))
    if clear_embedding:
        reasons.append("STALE_EMBEDDING_INVALIDATED_USE_LEXICAL_RETRIEVAL")
    return {"creatorId": row["creatorId"], "metricId": row["metricId"],
            "username": row.get("username"), "platform": row.get("platform"),
            "before": before, "fingerprint": fingerprint(row), "changes": changes,
            "clearEmbedding": clear_embedding, "managerDecisionPreserved": reviewed,
            "reasons": reasons, "reviewReasons": review,
            "status": "REVIEW_REQUIRED" if review else "CHANGES_PROPOSED" if changes or clear_embedding else "UNCHANGED"}


class DbNormalizationService:
    def __init__(self, connection_factory=connect_postgres, report_dir=None):
        self.connection_factory = connection_factory
        self.report_dir = Path(report_dir) if report_dir else REPORT_DIR

    @staticmethod
    def _cursor(connection):
        from psycopg2.extras import RealDictCursor
        return connection.cursor(cursor_factory=RealDictCursor)

    @staticmethod
    def _target(connection):
        params = connection.get_dsn_parameters()
        return {key: params.get(key) for key in ("host", "port", "dbname")}

    def _posts(self, cursor, creator_id, *, lock=False):
        cursor.execute('''SELECT id::text AS id, platform_post_id AS "platformPostId", caption,
            views, likes, comments, shares, posted_at AS "postedAt" FROM post WHERE creator_id=%s
            ORDER BY id''' + (" FOR UPDATE" if lock else ""), (creator_id,))
        return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _select_sql(cursor):
        # V19 databases predate discovery visibility. Absence is unknown, not a new visibility decision.
        cursor.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() "
                       "AND table_name='creator' AND column_name='discovery_hidden') AS supported")
        return SELECT if cursor.fetchone()["supported"] else SELECT.replace(
            'c.discovery_hidden AS "discoveryHidden"', 'NULL::boolean AS "discoveryHidden"')

    def _path(self, plan_id):
        if not re.fullmatch(r"normalize-[0-9a-f]{32}", plan_id):
            raise ValueError("Invalid normalization plan ID")
        return self.report_dir / (plan_id + ".json")

    def _save(self, report):
        self.report_dir.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.report_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(report, output, ensure_ascii=False, indent=2, default=serial)
            os.replace(temporary, self._path(report["runId"]))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def report(self, plan_id):
        return json.loads(self._path(plan_id).read_text(encoding="utf-8"))

    def preview(self, *, limit=100, offset=0, platform=None, category=None, after_id=None, all_rows=False):
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("Invalid normalization batch range")
        if after_id:
            uuid.UUID(after_id)
        now = datetime.now(UTC)
        items = []
        connection = self.connection_factory(readonly=True)
        try:
            target = self._target(connection)
            with self._cursor(connection) as cursor:
                select_sql = self._select_sql(cursor)
                while True:
                    conditions, parameters = ["1=1"], []
                    if platform:
                        conditions.append("UPPER(c.platform)=%s"); parameters.append(platform.strip().upper())
                    if category:
                        code = canonical_label(category)
                        aliases = [key for key, label in LABELS.items() if label["code"] == code] or [category.strip().lower()]
                        conditions.append("(LOWER(BTRIM(m.category))=ANY(%s) OR LOWER(BTRIM(m.niche))=ANY(%s) "
                            "OR EXISTS (SELECT 1 FROM unnest(string_to_array(COALESCE(m.niche,''),',')) niche_part "
                            "WHERE LOWER(BTRIM(niche_part))=ANY(%s)))")
                        parameters.extend([aliases, aliases, aliases])
                    if after_id:
                        conditions.append("c.id>%s::uuid"); parameters.append(after_id)
                    parameters.extend([limit, offset])
                    cursor.execute(select_sql + " WHERE " + " AND ".join(conditions) + " ORDER BY c.id LIMIT %s OFFSET %s", parameters)
                    rows = [dict(row) for row in cursor.fetchall()]
                    for row in rows:
                        row["posts"] = self._posts(cursor, row["creatorId"])
                        items.append(assess_record(row, now=now))
                    if rows:
                        after_id = rows[-1]["creatorId"]
                    if not all_rows or len(rows) < limit:
                        break
                    offset = 0
            connection.rollback()
        finally:
            connection.close()
        changed = sum(bool(item["changes"] or item["clearEmbedding"]) for item in items)
        report = {"runId": "normalize-" + uuid.uuid4().hex, "contractVersion": VERSION,
                  "contractHash": CONTRACT_HASH, "target": target, "executedAt": now.isoformat(),
                  "expiresAt": (now + timedelta(hours=1)).isoformat(), "dryRun": True, "status": "PREVIEW",
                  "assessmentScope": "STORED_DATA_CONTRACT_NORMALIZATION_NOT_SEMANTIC_VERIFICATION",
                  "totalAudited": len(items), "proposedChanges": changed,
                  "needsReviewCount": sum(bool(item["reviewReasons"]) for item in items),
                  "nextAfterId": after_id if not all_rows and len(items) == limit else None,
                  "items": items}
        self._save(report)
        return report

    def apply(self, plan_id, *, actor_id=None):
        if actor_id:
            uuid.UUID(actor_id)
        report = self.report(plan_id)
        if report["contractHash"] != CONTRACT_HASH or report["contractVersion"] != VERSION:
            raise ValueError("Contract changed; preview normalization again")
        if datetime.fromisoformat(report["expiresAt"]) < datetime.now(UTC):
            raise ValueError("Normalization plan expired; preview again")
        connection = self.connection_factory(readonly=False)
        outcomes = []
        try:
            if self._target(connection) != report["target"]:
                raise ValueError("Database target differs from the normalization preview")
            for item in report["items"]:
                if not item["changes"] and not item["clearEmbedding"]:
                    outcomes.append({"creatorId": item["creatorId"], "status": "UNCHANGED"}); continue
                try:
                    with self._cursor(connection) as cursor:
                        cursor.execute("SELECT id FROM creator WHERE id=%s FOR UPDATE", (item["creatorId"],))
                        description = json.dumps({"planId": plan_id, "contractVersion": VERSION}, sort_keys=True)
                        cursor.execute("SELECT id FROM audit_log WHERE action='NORMALIZE_CREATOR_DATA' AND entity_id=%s AND description=%s",
                                       (item["metricId"], description))
                        if cursor.fetchone():
                            connection.rollback()
                            outcomes.append({"creatorId": item["creatorId"], "status": "ALREADY_APPLIED"}); continue
                        cursor.execute("SELECT id FROM public_creator_metric WHERE creator_id=%s ORDER BY collected_at DESC,id DESC LIMIT 1 FOR UPDATE",
                                       (item["creatorId"],))
                        cursor.fetchone()
                        cursor.execute(self._select_sql(cursor) + " WHERE c.id=%s", (item["creatorId"],))
                        current = cursor.fetchone()
                        if current is None:
                            raise ValueError("Creator no longer exists")
                        current = dict(current)
                        current["posts"] = self._posts(cursor, item["creatorId"], lock=True)
                        if fingerprint(current) != item["fingerprint"]:
                            raise ValueError("Snapshot, evidence or manager decision changed after preview")
                        recomputed = assess_record(current)
                        if recomputed["changes"] != item["changes"] or recomputed["clearEmbedding"] != item["clearEmbedding"]:
                            raise ValueError("Normalization proposal changed; preview again")
                        columns = list(item["changes"])
                        if any(field not in FIELDS for field in columns):
                            raise ValueError("Unsupported normalization field")
                        if columns:
                            assignments = ", ".join(FIELDS[field] + "=%s" for field in columns)
                            cursor.execute("UPDATE public_creator_metric SET " + assignments + " WHERE id=%s",
                                           [item["changes"][field] for field in columns] + [item["metricId"]])
                        if item["clearEmbedding"]:
                            cursor.execute("UPDATE creator SET embedding=NULL WHERE id=%s", (item["creatorId"],))
                        cursor.execute('''INSERT INTO audit_log(id,user_id,action,entity_type,entity_id,old_value,new_value,description,created_at)
                            VALUES (%s,%s,'NORMALIZE_CREATOR_DATA','PublicCreatorMetric',%s,%s::jsonb,%s::jsonb,%s,now())''',
                            (str(uuid.uuid4()), actor_id, item["metricId"], json.dumps(item["before"]),
                             json.dumps({"changes": item["changes"], "embeddingCleared": item["clearEmbedding"]}), description))
                    connection.commit()
                    outcomes.append({"creatorId": item["creatorId"], "status": "APPLIED"})
                except Exception as failure:
                    connection.rollback()
                    outcomes.append({"creatorId": item["creatorId"], "status": "CONFLICT_OR_ERROR", "reason": str(failure)})
        finally:
            connection.close()
        report["dryRun"] = False
        report["applyResults"] = outcomes
        report["appliedCount"] = sum(item["status"] == "APPLIED" for item in outcomes)
        report["conflictCount"] = sum(item["status"] == "CONFLICT_OR_ERROR" for item in outcomes)
        report["alreadyAppliedCount"] = sum(item["status"] == "ALREADY_APPLIED" for item in outcomes)
        report["status"] = ("PARTIAL" if report["appliedCount"] else "CONFLICT") if report["conflictCount"] else "APPLIED"
        self._save(report)
        return report


db_normalization_service = DbNormalizationService()
