import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_silver import fixture, graph as silver_graph, decision, silver, WHEN
from test_publication_versions import graph as version_graph

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gold", ROOT / "notebooks/10_build_gold.py")
gold = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gold)
SID = "3230bb0f-28c7-4830-90b5-5a21034794cb"


def source_data():
    data = fixture()
    candidate = silver_graph(data).rows("entity_resolution_review")[0]
    s = silver_graph(data, [decision(candidate)], build_id=SID)
    return {t: s.rows(t) for t in gold.SOURCE_COLUMNS}


def build(data=None, build_id="gold-test"):
    data = source_data() if data is None else data
    return gold.build_gold(lambda table: iter(data[table]), SID, build_id, 2026, 1)[0]


class GoldGraphTests(unittest.TestCase):
    def test_profile_evidence_counts_and_unknown_funding(self):
        g = build()
        p = g.rows("researcher_profiles")[0]
        self.assertEqual(p["linked_source_record_count"], 2)
        self.assertEqual(p["contribution_count"], 2)
        self.assertEqual(p["eligible_contribution_count"], 1)
        self.assertEqual(p["excluded_contribution_count"], 1)
        self.assertEqual(p["funding_status"], "not_established")
        self.assertIsNone(p["verified_award_count"])
        self.assertEqual(p["affiliation_status"], "directory_observed")
        self.assertEqual(len(g.rows("researcher_profile_publications")), 1)
        self.assertEqual(len(g.rows("researcher_profile_topics")), 1)
        self.assertEqual(len(g.rows("researcher_topic_evidence")), 1)
        self.assertEqual(g.rows("researcher_profile_topics")[0]["evidence_strength"], "single_contribution")

    def test_alias_not_double_counted_and_preprint_labeled(self):
        s = version_graph()
        data = {t: s.rows(t) for t in gold.SOURCE_COLUMNS}
        g = gold.build_gold(lambda t: iter(data[t]), s.build_id, expected_profiles=1)[0]
        self.assertEqual(g.rows("researcher_profiles")[0]["contribution_count"], 1)
        pub = g.rows("researcher_profile_publications")[0]
        self.assertEqual(pub["representative_work_id"], "oa:W7128031808")
        self.assertEqual(len(pub["source_work_ids"]), 2)
        self.assertTrue(pub["is_preprint"])
        self.assertIn("preprint; peer review not established", g.rows("researcher_search_document_history")[0]["search_text"])

    def test_repository_description_and_biography_not_in_search(self):
        data = source_data()
        data["researchers"][0]["biography"] = "UNVERIFIED_GRANT_CLAIM"
        work = next(w for w in data["works"] if w["eligible_for_expertise"])
        work["abstract"] = "REPOSITORY_DESCRIPTION_ONLY"
        work["abstract_source"] = "oai_description"
        g = build(data)
        doc = g.rows("researcher_search_document_history")[0]
        self.assertNotIn("REPOSITORY_DESCRIPTION_ONLY", doc["search_text"])
        self.assertNotIn("UNVERIFIED_GRANT_CLAIM", doc["search_text"])
        pub = g.rows("researcher_profile_publications")[0]
        self.assertIsNone(pub["abstract_excerpt"])
        self.assertEqual(pub["repository_description_excerpt"], "REPOSITORY_DESCRIPTION_ONLY")

    def test_openalex_abstract_in_search_is_labeled_and_bounded(self):
        data = source_data()
        work = next(w for w in data["works"] if w["eligible_for_expertise"])
        work["abstract"], work["abstract_source"] = "TEST_ABSTRACT " * 2000, "openalex"
        g = build(data)
        pub = g.rows("researcher_profile_publications")[0]
        self.assertEqual(len(pub["abstract_excerpt"]), 1500)
        doc = g.rows("researcher_search_document_history")[0]
        self.assertIn("OpenAlex abstract excerpt (source text):", doc["search_text"])
        self.assertLessEqual(len(doc["search_text"]), gold.MAX_SEARCH_CHARS)

    def test_placeholder_abstract_is_missing_not_search_evidence(self):
        data = source_data()
        work = next(w for w in data["works"] if w["eligible_for_expertise"])
        work["abstract"], work["abstract_source"] = "No abstract available.", "openalex"
        g = build(data)
        self.assertIsNone(g.rows("researcher_profile_publications")[0]["abstract_excerpt"])
        self.assertNotIn("No abstract available", g.rows("researcher_search_document_history")[0]["search_text"])
        self.assertEqual(g.rows("profile_quality_metrics")[0]["eligible_missing_abstract_count"], 1)

    def test_same_title_is_flagged_without_automatic_merge(self):
        s = version_graph(evidence=[])
        g = gold.build_gold(lambda t: iter(s.rows(t)), s.build_id, expected_profiles=1)[0]
        self.assertEqual(len(g.rows("researcher_profile_publications")), 2)
        self.assertEqual(g.rows("profile_quality_metrics")[0]["same_title_group_count"], 1)
        self.assertIn("same_title_records_need_version_review", g.rows("profile_quality_metrics")[0]["coverage_warnings"])

    def test_unsafe_retraction_or_alias_marked_eligible_fails(self):
        for key, value in (("is_retracted", True), ("quality_flags", ["concept_doi_alias"])):
            data = source_data()
            work = next(w for w in data["works"] if w["eligible_for_expertise"])
            work[key] = value
            with self.assertRaisesRegex(ValueError, "Unsafe contribution"):
                build(data)

    def test_pending_identity_cannot_publish(self):
        data = source_data()
        data["entity_resolution_review"][0]["status"] = "pending"
        with self.assertRaisesRegex(ValueError, "scope disagrees"):
            build(data)

    def test_expected_profile_count_guard(self):
        data = source_data()
        with self.assertRaisesRegex(ValueError, "Expected 7 verified"):
            gold.build_gold(lambda t: iter(data[t]), SID)

    def test_mixed_snapshot_and_duplicate_inputs_fail(self):
        data = source_data()
        data["works"][0]["build_id"] = "another-build"
        with self.assertRaisesRegex(ValueError, "Mixed Silver"):
            build(data)
        data = source_data()
        data["researcher_works"].append(copy.deepcopy(data["researcher_works"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate Silver row_key"):
            build(data)

    def test_duplicate_business_key_with_distinct_row_key_fails(self):
        data = source_data()
        duplicate = copy.deepcopy(data["researcher_contributions"][0])
        duplicate["row_key"] = "different-key"
        data["researcher_contributions"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "business key"):
            build(data)

    def test_wrong_topic_count_or_scoring_year_fails(self):
        for field, value in (("supporting_work_count", 99), ("as_of_year", 2025), ("score", float("nan"))):
            data = source_data()
            data["researcher_topic_scores"][0][field] = value
            with self.assertRaises(ValueError):
                build(data)

    def test_no_inferred_authorship_for_representative(self):
        data = source_data()
        data["researcher_works"][0]["candidate_ids"] = ["nonexistent"]
        with self.assertRaisesRegex(ValueError, "accepted identity"):
            build(data)

    def test_missing_member_and_missing_contribution_fail(self):
        data = source_data()
        data["researcher_contributions"][0]["source_work_ids"] = []
        with self.assertRaisesRegex(ValueError, "representative or members"):
            build(data)
        data = source_data()
        data["researcher_contributions"].pop()
        with self.assertRaisesRegex(ValueError, "coverage disagrees"):
            build(data)

    def test_new_award_attribution_requires_policy_review(self):
        data = source_data()
        data["researcher_awards"] = [{"build_id": SID, "row_key": "award", "researcher_id": "r", "award_id": "a", "verification_status": "verified"}]
        with self.assertRaisesRegex(ValueError, "funding policy"):
            build(data)

    def test_missing_doi_uses_openalex_evidence_url(self):
        data = source_data()
        next(w for w in data["works"] if w["eligible_for_expertise"])["doi"] = None
        self.assertEqual(build(data).rows("researcher_profile_publications")[0]["evidence_url"], "https://openalex.org/W1")

    def test_no_topics_still_has_publication_backed_search_document(self):
        data = source_data()
        data["work_topics"] = []
        data["researcher_topic_scores"] = []
        g = build(data)
        self.assertEqual(g.rows("researcher_profile_topics"), [])
        self.assertEqual(len(g.rows("researcher_search_document_history")), 1)

    def test_document_identity_and_text_stable_across_builds(self):
        a = build(build_id="one").rows("researcher_search_document_history")[0]
        b = build(build_id="two").rows("researcher_search_document_history")[0]
        for field in ("document_id", "search_text", "search_text_sha256", "publication_evidence_ids", "topic_ids"):
            self.assertEqual(a[field], b[field])
        self.assertNotEqual(a["build_id"], b["build_id"])

    def test_input_and_output_resource_guards(self):
        with patch.object(gold, "MAX_INPUT_ROWS", 1):
            with self.assertRaisesRegex(ValueError, "input exceeds"):
                build()
        with patch.object(gold, "MAX_OUTPUT_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "output exceeds"):
                build()

    def test_search_text_budget_does_not_leave_dangling_references(self):
        g = build()
        p = g.rows("researcher_profiles")[0]
        pubs = [copy.deepcopy(g.rows("researcher_profile_publications")[0]) for _ in range(30)]
        for i, pub in enumerate(pubs):
            pub.update(publication_evidence_id=str(i), title="Title " * 100, abstract_excerpt="Long abstract " * 100)
        doc = gold.make_search_document(p, pubs, [], [])
        self.assertLessEqual(len(doc["search_text"]), 6000)
        self.assertLessEqual(len(doc["publication_evidence_ids"]), 12)
        for pid in doc["publication_evidence_ids"]:
            self.assertIn("Publication [" + pid + "]", doc["search_text"])


class GoldContractsAndPublicationTests(unittest.TestCase):
    def test_ddl_contracts_match_and_search_has_change_feed(self):
        ddl = (ROOT / "sql/09_create_gold_tables.sql").read_text()
        for table in [*gold.CONTRACTS, *gold.CONTROL_CONTRACTS]:
            body = re.search(r"CREATE TABLE IF NOT EXISTS " + table + r" \((.*?)\) USING DELTA", ddl, re.S)[1]
            fields = []
            for line in body.splitlines():
                m = re.match(r"\s*(\w+)\s+(ARRAY<STRING>|STRING|TIMESTAMP|BIGINT|INT|BOOLEAN|DOUBLE)(\s+NOT NULL)?", line)
                if m:
                    fields.append((m[1], m[2].lower(), not bool(m[3])))
            self.assertEqual(gold.fields(table), fields)
        self.assertIn("'delta.enableChangeDataFeed' = 'true'", ddl)

    def test_current_view_tracks_serving_not_latest_validated_run(self):
        ddl = (ROOT / "sql/09_create_gold_tables.sql").read_text()
        view = re.search(r"CREATE VIEW IF NOT EXISTS current_build AS\n(.*?);", ddl, re.S)[1]
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE TABLE build_runs (build_id, document_count, status)")
            db.execute("CREATE TABLE researcher_search_documents (gold_build_id, document_id)")
            db.executemany("INSERT INTO build_runs VALUES (?,?,?)", [("old", 1, "validated"), ("new", 2, "validated")])
            db.execute("CREATE VIEW current_build AS " + view)
            self.assertEqual(db.execute("SELECT * FROM current_build").fetchall(), [])
            db.execute("INSERT INTO researcher_search_documents VALUES ('old','r1')")
            self.assertEqual(db.execute("SELECT build_id FROM current_build").fetchall(), [("old",)])
            db.execute("DELETE FROM researcher_search_documents")
            db.executemany("INSERT INTO researcher_search_documents VALUES (?,?)", [("new", "r1"), ("new", "r2")])
            self.assertEqual(db.execute("SELECT build_id FROM current_build").fetchall(), [("new",)])
            db.execute("UPDATE build_runs SET status='failed' WHERE build_id='new'")
            self.assertEqual(db.execute("SELECT * FROM current_build").fetchall(), [])

    @patch.object(gold, "spark_schema", return_value="schema")
    def test_partial_snapshot_write_never_reaches_serving_merge(self, _):
        spark = Mock()
        spark.sql.return_value.first.return_value = {"n": 999, "keys": 999}
        g = build()
        run = {"build_id": g.build_id, "status": "started"}
        with self.assertRaisesRegex(ValueError, "persisted row/key"):
            gold.write_build(spark, "workspace", "researchmatch_gold", g, run, Mock())
        self.assertEqual(run["status"], "failed")
        self.assertFalse(any("WHEN NOT MATCHED BY SOURCE" in call.args[0] for call in spark.sql.call_args_list))

    @patch.object(gold, "spark_schema", return_value="schema")
    def test_validated_snapshot_published_with_one_full_projection_merge(self, _):
        spark, source_check = Mock(), Mock()
        g = build()
        def sql(query):
            out = Mock()
            if query.startswith("SELECT COUNT"):
                table = re.search(r"researchmatch_gold`\.`(\w+)`", query)[1]
                out.first.return_value = {"n": len(g.tables[table]), "keys": len(g.tables[table])}
            return out
        spark.sql.side_effect = sql
        run = {"build_id": g.build_id, "status": "started"}
        gold.write_build(spark, "workspace", "researchmatch_gold", g, run, source_check)
        queries = [call.args[0] for call in spark.sql.call_args_list]
        merges = [q for q in queries if "WHEN NOT MATCHED BY SOURCE THEN DELETE" in q]
        self.assertEqual(len(merges), 1)
        self.assertIn("researcher_search_documents", merges[0])
        self.assertLess(next(i for i, q in enumerate(queries) if q.startswith("MERGE INTO") and "build_runs" in q), queries.index(merges[0]))
        self.assertEqual(run["status"], "validated")
        source_check.assert_called_once()

    @patch.object(gold, "spark_schema", return_value="schema")
    def test_source_change_after_staging_does_not_publish(self, _):
        g, spark = build(), Mock()
        def sql(query):
            out = Mock()
            if query.startswith("SELECT COUNT"):
                t = re.search(r"researchmatch_gold`\.`(\w+)`", query)[1]
                out.first.return_value = {"n": len(g.tables[t]), "keys": len(g.tables[t])}
            return out
        spark.sql.side_effect = sql
        run = {"build_id": g.build_id, "status": "started"}
        with self.assertRaisesRegex(ValueError, "source changed"):
            gold.write_build(spark, "workspace", "researchmatch_gold", g, run,
                             Mock(side_effect=ValueError("source changed")))
        self.assertEqual(run["status"], "failed")
        self.assertFalse(any("WHEN NOT MATCHED BY SOURCE" in c.args[0] for c in spark.sql.call_args_list))

    def test_serving_shape_has_no_snapshot_row_key(self):
        row = gold.serving_rows(build())[0]
        self.assertEqual(set(row), {n for n, _, _ in gold.fields("researcher_search_documents")})
        self.assertNotIn("row_key", row)

    def test_review_sql_relational_checks_against_fixture(self):
        # Dialect-adapt JSON EXPLODE; all validation joins/predicates are unchanged.
        g, data = build(), source_data()
        with sqlite3.connect(":memory:") as db:
            db.create_function("SIZE", 1, lambda v: len(json.loads(v)))
            db.create_function("ARRAY_CONTAINS", 2, lambda a, v: v in json.loads(a))
            db.create_function("SHA2", 2, lambda text, bits: hashlib.sha256(text.encode()).hexdigest())
            class CollectSet:
                def __init__(self): self.values = set()
                def step(self, value): self.values.add(value)
                def finalize(self): return json.dumps(sorted(self.values))
            db.create_aggregate("COLLECT_SET", 1, CollectSet)
            def create(name, columns, rows):
                db.execute("CREATE TABLE " + name + " (" + ",".join(columns) + ")")
                for row in rows:
                    values = [json.dumps(row[c]) if isinstance(row[c], list) else
                              row[c].isoformat() if hasattr(row[c], "isoformat") else row[c] for c in columns]
                    db.execute("INSERT INTO " + name + " VALUES (" + ",".join("?" for _ in columns) + ")", values)
            for table in gold.CONTRACTS:
                create(table + "_current", [n for n, _, _ in gold.fields(table)], g.rows(table))
            create("researcher_search_documents", [n for n, _, _ in gold.fields("researcher_search_documents")], gold.serving_rows(g))
            create("current_build", ["build_id", "silver_build_id", "transform_version", "status", "document_count", "counts_json"],
                   [{"build_id": g.build_id, "silver_build_id": SID, "transform_version": gold.TRANSFORM_VERSION,
                     "status": "validated", "document_count": 1, "counts_json": "{}"}])
            create("silver_current_build", ["build_id"], [{"build_id": SID}])
            for table in ("researchers", "works", "researcher_contributions"):
                create("silver_" + table, ["build_id", "row_key", *gold.SOURCE_COLUMNS[table].split()], data[table])
            sql = (ROOT / "sql/11_review_gold.sql").read_text()
            sql = re.sub(r"--[^\n]*", "", sql)
            sql = re.sub(r"USE (CATALOG|SCHEMA) \w+;", "", sql)
            sql = sql.replace("workspace.researchmatch_silver.", "silver_")
            sql = sql.replace("SELECT gold_build_id, researcher_id, EXPLODE(publication_evidence_ids) AS publication_evidence_id\n  FROM researcher_search_documents",
                              "SELECT d.gold_build_id, d.researcher_id, j.value AS publication_evidence_id FROM researcher_search_documents d, json_each(d.publication_evidence_ids) j")
            checked = False
            for statement in sql.split(";"):
                if not statement.strip() or statement.strip().startswith("SHOW TBLPROPERTIES"):
                    continue
                cur = db.execute(statement)
                result = cur.fetchall()
                if cur.description[0][0] == "check_name":
                    self.assertEqual(len(result), 7)
                    self.assertTrue(all(r[1] == 0 for r in result), result)
                    checked = True
            self.assertTrue(checked)


class Row(dict):
    def asDict(self, recursive=False):
        return dict(self)


class Frame:
    def __init__(self, rows):
        self.rows = [Row(r) for r in rows]

    def first(self):
        return self.rows[0] if self.rows else None

    def limit(self, n):
        return Frame(self.rows[:n])

    def repartition(self, n):
        return self

    def toLocalIterator(self):
        return iter(self.rows)


class GoldRuntimeTests(unittest.TestCase):
    def environment(self, mode="validate", stale=False):
        data, spark, dbutils = source_data(), Mock(), Mock()
        widgets = {"catalog": "workspace", "silver_schema": "researchmatch_silver", "gold_schema": "researchmatch_gold",
                   "mode": mode, "expected_verified_profiles": "1"}
        dbutils.widgets.get.side_effect = widgets.__getitem__
        spark.catalog.tableExists.return_value = True
        def table(name):
            target = name.split(".")[-1].strip("`")
            fields = [SimpleNamespace(name=n, dataType=SimpleNamespace(simpleString=lambda t=t: t))
                      for n, t, _ in gold.fields(target)]
            return SimpleNamespace(schema=SimpleNamespace(fields=fields))
        spark.table.side_effect = table
        def sql(query):
            if query.startswith("DESCRIBE DETAIL"):
                return Frame([{"format": "delta", "properties": {"delta.enableChangeDataFeed": "true"}}])
            if query.startswith("DESCRIBE HISTORY"):
                return Frame([{"version": 2 if stale and "identity_decisions" in query else 1}])
            if "researchmatch_silver`.`current_build" in query:
                return Frame([{"build_id": SID, "transform_version": gold.SILVER_VERSION, "as_of_year": 2026,
                               "source_versions_json": '{"identity_decisions":1}'}])
            if "researchmatch_gold`.`current_build" in query:
                return Frame([])
            if query.startswith("SELECT COUNT(*) AS n"):
                return Frame([{"n": 0}])
            if "VERSION AS OF" in query:
                name = re.search(r"researchmatch_silver`\.`(\w+)`", query)[1]
                return Frame(data[name])
            raise AssertionError("Unexpected query: " + query)
        spark.sql.side_effect = sql
        return spark, dbutils

    def test_default_validation_writes_nothing(self):
        spark, dbutils = self.environment()
        summary = gold.run_notebook(spark, dbutils)
        self.assertEqual(summary["search_documents"], 1)
        spark.createDataFrame.assert_not_called()
        self.assertFalse(any(call.args[0].startswith("MERGE") for call in spark.sql.call_args_list))

    def test_changed_identity_decision_version_blocks_before_writes(self):
        spark, dbutils = self.environment(stale=True)
        with self.assertRaisesRegex(ValueError, "decisions changed"):
            gold.run_notebook(spark, dbutils)
        spark.createDataFrame.assert_not_called()

    def test_identical_published_build_is_no_write_skip(self):
        spark, dbutils = self.environment(mode="build")
        original = spark.sql.side_effect
        fingerprint = gold.key(gold.TRANSFORM_VERSION, "workspace", "researchmatch_silver", "researchmatch_gold",
                               SID, {t: 1 for t in gold.SOURCE_COLUMNS}, 2026, 1,
                               gold.MAX_SEARCH_CHARS, gold.MAX_SEARCH_TOPICS, gold.MAX_SEARCH_PUBLICATIONS)
        def sql(query):
            if "researchmatch_gold`.`current_build" in query:
                return Frame([{"build_id": "published", "input_fingerprint": fingerprint}])
            if query.startswith("SELECT COUNT(*) AS n"):
                return Frame([{"n": 1}])
            return original(query)
        spark.sql.side_effect = sql
        result = gold.run_notebook(spark, dbutils)
        self.assertTrue(result["skipped"])
        spark.createDataFrame.assert_not_called()


if __name__ == "__main__":
    unittest.main()
