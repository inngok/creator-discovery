"""Run against a disposable PostgreSQL database via NORMALIZATION_TEST_DSN, never the app DSN."""
from datetime import UTC, datetime, timedelta
import json
import os
import tempfile
import unittest
import uuid

import psycopg2
from psycopg2 import sql
from app.services.db_normalization_service import DbNormalizationService


@unittest.skipUnless(os.environ.get("NORMALIZATION_TEST_DSN"), "Set NORMALIZATION_TEST_DSN to a disposable PostgreSQL database")
class DbNormalizationPostgresTest(unittest.TestCase):
    def setUp(self):
        self.schema = "normalization_test_" + uuid.uuid4().hex
        root = psycopg2.connect(os.environ["NORMALIZATION_TEST_DSN"])
        with root, root.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
            cur.execute(sql.SQL("CREATE SCHEMA {};").format(sql.Identifier(self.schema)))
        root.close()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.service = DbNormalizationService(self.connection, self.directory.name)
        self.creator = str(uuid.uuid4()); self.metric = str(uuid.uuid4())
        self.observed = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)
        self.seed()

    def connection(self, *, readonly=False):
        connection = psycopg2.connect(os.environ["NORMALIZATION_TEST_DSN"], options=f"-c search_path={self.schema},public")
        connection.set_session(readonly=readonly, isolation_level="REPEATABLE READ" if readonly else "READ COMMITTED")
        return connection

    def execute(self, query, values=None, *, fetch=False):
        connection = self.connection()
        try:
            with connection, connection.cursor() as cur:
                cur.execute(query, values)
                return cur.fetchall() if fetch else None
        finally:
            connection.close()

    def seed(self):
        self.execute('''CREATE TABLE creator(id uuid PRIMARY KEY,platform text,user_name text,display_name text,
            discovery_hidden boolean DEFAULT false,master_id uuid,source_type text DEFAULT 'PUBLIC',embedding vector(768));
            CREATE TABLE public_creator_metric(id uuid PRIMARY KEY,creator_id uuid REFERENCES creator(id),
            category text,niche text,creator_type text,followers bigint,avg_views numeric(19,2),avg_likes numeric(19,2),
            avg_comments numeric(19,2),avg_shares numeric(19,2),engagement_rate numeric(7,4),content_summary text,
            data_confidence numeric(5,4),freshness_status text,collected_at timestamp,observed_at timestamp,crawl_run_id text,
            quality_reviewed_at timestamp,quality_reviewed_by uuid);
            CREATE TABLE post(id uuid PRIMARY KEY,creator_id uuid REFERENCES creator(id),platform_post_id text,caption text,
            views bigint,likes bigint,comments bigint,shares bigint,posted_at text);
            CREATE TABLE audit_log(id uuid PRIMARY KEY,user_id uuid,action text,entity_type text,entity_id uuid,
            old_value jsonb,new_value jsonb,description text,created_at timestamp);''')
        self.execute("INSERT INTO creator(id,platform,user_name,display_name,embedding) VALUES (%s,'TIKTOK','legacy','Legacy',%s::vector)",
                     (self.creator, str([0.05] * 768)))
        self.execute('''INSERT INTO public_creator_metric(id,creator_id,category,niche,creator_type,followers,
            avg_likes,avg_comments,avg_shares,engagement_rate,content_summary,data_confidence,freshness_status,collected_at,observed_at)
            VALUES (%s,%s,'BEAUTY','Gia dụng','KOC',25000,100,20,5,0.5,'Old fabricated beauty summary',0.9,'FRESH',%s,%s)''',
            (self.metric, self.creator, self.observed, self.observed))
        for i in range(3):
            self.execute("INSERT INTO post(id,creator_id,platform_post_id,caption,posted_at) VALUES (%s,%s,%s,%s,%s)",
                (str(uuid.uuid4()), self.creator, str(i), f"Review máy hút bụi dùng thật tại nhà {i}", self.observed.isoformat()))

    def test_preview_apply_idempotency_and_complete_before_after_audit(self):
        plan = self.service.preview()
        self.assertTrue(plan["dryRun"])
        self.assertEqual(self.execute("SELECT category FROM public_creator_metric", fetch=True)[0][0], "BEAUTY")
        result = self.service.apply(plan["runId"])
        self.assertEqual(result["appliedCount"], 1)
        category, niche, rate, collected, observed, fresh = self.execute(
            "SELECT category,niche,engagement_rate,collected_at,observed_at,freshness_status FROM public_creator_metric", fetch=True)[0]
        self.assertEqual((category, niche, str(rate), fresh), ("HOME", "HOME", "0.0050", "STALE"))
        self.assertEqual((collected, observed), (self.observed, self.observed))
        self.assertEqual(self.execute("SELECT count(*) FROM post", fetch=True)[0][0], 3)
        self.assertIsNone(self.execute("SELECT embedding FROM creator", fetch=True)[0][0])
        old, new = self.execute("SELECT old_value,new_value FROM audit_log", fetch=True)[0]
        self.assertEqual(old["category"], "BEAUTY")
        self.assertEqual(new["changes"]["category"], "HOME")
        self.assertEqual(self.service.apply(plan["runId"])["alreadyAppliedCount"], 1)
        self.assertEqual(self.execute("SELECT count(*) FROM audit_log", fetch=True)[0][0], 1)
        self.assertEqual(self.service.preview()["proposedChanges"], 0)

    def test_recrawl_or_manager_approval_after_preview_conflicts(self):
        plan = self.service.preview()
        self.execute("UPDATE public_creator_metric SET quality_reviewed_at=now(),quality_reviewed_by=%s", (str(uuid.uuid4()),))
        result = self.service.apply(plan["runId"])
        self.assertEqual(result["conflictCount"], 1)
        self.assertEqual(self.execute("SELECT category FROM public_creator_metric", fetch=True)[0][0], "BEAUTY")
        self.assertEqual(self.execute("SELECT count(*) FROM audit_log", fetch=True)[0][0], 0)

    def test_post_evidence_changed_after_preview_is_not_overwritten(self):
        plan = self.service.preview()
        self.execute("UPDATE post SET caption='A manager corrected this evidence'")
        self.assertEqual(self.service.apply(plan["runId"])["conflictCount"], 1)

    def test_multiple_snapshots_only_the_latest_is_repaired(self):
        older = str(uuid.uuid4())
        self.execute("INSERT INTO public_creator_metric(id,creator_id,category,niche,creator_type,collected_at) VALUES (%s,%s,'FOOD','FOOD','KOC',%s)",
                     (older, self.creator, self.observed - timedelta(days=1)))
        plan = self.service.preview()
        self.assertEqual(plan["items"][0]["metricId"], self.metric)
        self.service.apply(plan["runId"])
        self.assertEqual(self.execute("SELECT category FROM public_creator_metric WHERE id=%s", (older,), fetch=True)[0][0], "FOOD")

    def test_a_newer_snapshot_after_preview_is_rejected(self):
        plan = self.service.preview()
        self.execute("INSERT INTO public_creator_metric(id,creator_id,category,niche,creator_type,collected_at) VALUES (%s,%s,'FOOD','FOOD','KOC',now())",
                     (str(uuid.uuid4()), self.creator))
        self.assertEqual(self.service.apply(plan["runId"])["conflictCount"], 1)

    def test_audit_failure_rolls_back_metric_and_embedding_together(self):
        plan = self.service.preview()
        self.execute("ALTER TABLE audit_log ADD CONSTRAINT fail_audit CHECK (false)")
        result = self.service.apply(plan["runId"])
        self.assertEqual(result["appliedCount"], 0)
        self.assertEqual(self.execute("SELECT category FROM public_creator_metric", fetch=True)[0][0], "BEAUTY")
        self.assertIsNotNone(self.execute("SELECT embedding FROM creator", fetch=True)[0][0])

    def test_tampered_plan_cannot_write_arbitrary_data(self):
        plan = self.service.preview()
        plan["items"][0]["changes"]["category"] = "FOOD"
        self.service._save(plan)
        self.assertEqual(self.service.apply(plan["runId"])["conflictCount"], 1)

    def test_previously_reviewed_labels_remain_untouched(self):
        self.execute("UPDATE public_creator_metric SET quality_reviewed_at=now(),quality_reviewed_by=%s", (str(uuid.uuid4()),))
        plan = self.service.preview()
        for field in ("category", "niche", "creatorType"):
            self.assertNotIn(field, plan["items"][0]["changes"])
        self.service.apply(plan["runId"])
        self.assertEqual(self.execute("SELECT category,niche FROM public_creator_metric", fetch=True)[0], ("BEAUTY", "Gia dụng"))

    def test_category_alias_filter_matches_multi_niche_legacy_records(self):
        self.execute("UPDATE public_creator_metric SET category='BEAUTY',niche='Thực phẩm, Gia dụng'")
        plan = self.service.preview(category="Ẩm thực & Nhà hàng")
        self.assertEqual(plan["totalAudited"], 1)
        self.assertEqual(plan["items"][0]["changes"]["niche"], "FOOD, HOME")

    def test_v19_database_without_visibility_column_can_be_previewed_without_schema_changes(self):
        self.execute("ALTER TABLE creator DROP COLUMN discovery_hidden")
        plan = self.service.preview()
        self.assertIsNone(plan["items"][0]["before"]["discoveryHidden"])
        self.assertEqual(self.service.apply(plan["runId"])["appliedCount"], 1)
        self.assertEqual(self.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() "
                                     "AND table_name='creator' AND column_name='discovery_hidden'", fetch=True)[0][0], 0)

    def test_visibility_schema_change_after_preview_requires_a_new_plan(self):
        self.execute("ALTER TABLE creator DROP COLUMN discovery_hidden")
        plan = self.service.preview()
        self.execute("ALTER TABLE creator ADD COLUMN discovery_hidden boolean DEFAULT false")
        self.assertEqual(self.service.apply(plan["runId"])["conflictCount"], 1)
