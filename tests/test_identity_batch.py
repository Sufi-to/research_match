"""Offline relational guard tests; not a substitute for Databricks SQL execution.

Execute the production SQL in SQLite with only temporary-view syntax and the
timestamp spelling adapted. Register the four scalar functions used by Spark.
The actual joins, evidence predicates, whole-batch gate and INSERT are unchanged.
"""
import json
from pathlib import Path
import re
import sqlite3
import unittest
import uuid

SQL = (Path(__file__).resolve().parents[1] / "sql/07_review_identity_batch_02.sql").read_text()


class IdentityBatchTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.create_function("size", 1, lambda x: len(json.loads(x)) if x else None)
        self.db.create_function("array_contains", 2, lambda a, v: v in json.loads(a) if a else None)
        self.db.create_function("uuid", 0, lambda: str(uuid.uuid4()))
        self.db.create_function("current_user", 0, lambda: "test reviewer")
        self.db.executescript("""
          CREATE TABLE entity_resolution_review_current (
            candidate_id, researcher_id, openalex_author_id, evidence_hash,
            match_method, status, quality_flags);
          CREATE TABLE researchers_current (researcher_id, profile_url, profile_orcids, quality_flags);
          CREATE TABLE authors_current (openalex_author_id, orcid);
          CREATE TABLE work_authorships_current
            (openalex_author_id, author_orcid, work_id, institution_ids);
          CREATE TABLE works_current
            (work_id, title, doi, publication_year, eligible_for_expertise, is_retracted, quality_flags);
          CREATE TABLE quality_issues_current (entity_type, entity_id, issue_code);
          CREATE TABLE identity_decisions
            (decision_id, candidate_id, evidence_hash, decision, rationale, reviewed_by, reviewed_at);
        """)
        # Remove comments before splitting; semicolons in comments are not SQL.
        adapted = re.sub(r"--[^\n]*", "", SQL)
        adapted = re.sub(r"USE (CATALOG|SCHEMA) \w+;", "", adapted)
        adapted = adapted.replace("CREATE OR REPLACE TEMP VIEW", "CREATE TEMP VIEW")
        adapted = adapted.replace("current_timestamp()", "CURRENT_TIMESTAMP")
        statements, pending = [], ""
        for char in adapted:
            pending += char
            if char == ";" and sqlite3.complete_statement(pending):
                statements.append(pending.strip())
                pending = ""
        self.assertFalse(pending.strip())
        for statement in statements:
            if statement.startswith("CREATE TEMP VIEW"):
                self.db.execute(statement)
        self.insert = next(s for s in statements if s.startswith("INSERT INTO identity_decisions"))
        self.previews = [s for s in statements if s.startswith("SELECT")]
        self.targets = self.db.execute("SELECT * FROM rm_batch02_targets").fetchall()
        for i, (_, cid, aid, orcid, evidence) in enumerate(self.targets):
            self.db.execute("INSERT INTO entity_resolution_review_current VALUES (?,?,?,?,?,?,?)",
                            (cid, str(i), aid, evidence, "profile_orcid", "pending", "[]"))
            self.db.execute("INSERT INTO researchers_current VALUES (?,?,?,?)",
                            (str(i), "profile:" + str(i), json.dumps([orcid]), "[]"))
            self.db.execute("INSERT INTO authors_current VALUES (?,?)", (aid, orcid))

    def enable(self):
        self.db.executescript("""DROP VIEW rm_batch02_permission;
          CREATE TEMP VIEW rm_batch02_permission AS SELECT true AS approve_reviewed_batch;""")

    def count(self):
        return self.db.execute("SELECT COUNT(*) FROM identity_decisions").fetchone()[0]

    def add_decision(self, action, evidence=None):
        _, cid, _, _, expected = self.targets[0]
        self.db.execute("INSERT INTO identity_decisions VALUES (?,?,?,?,?,?,?)",
                        ("existing", cid, evidence or expected, action, "Reviewed", "User", "2026-09-09"))

    def assert_blocked(self):
        self.enable()
        before = self.count()
        self.assertEqual(self.db.execute("SELECT all_checks_pass FROM rm_batch02_gate").fetchone()[0], 0)
        self.db.execute(self.insert)
        self.assertEqual(self.count(), before)

    def test_exact_whitelist_and_hashes(self):
        self.assertEqual({t[0] for t in self.targets}, {
            "Ahmed Shuhaiber", "Andrew Marrington", "Dima Rabadi", "Fatma Outay", "Graham Manville"})
        for _, cid, aid, orcid, evidence in self.targets:
            self.assertRegex(cid, r"^[a-f0-9]{64}$")
            self.assertRegex(evidence, r"^[a-f0-9]{64}$")
            self.assertRegex(aid, r"^https://openalex.org/A\d+$")
            self.assertRegex(orcid, r"^https://orcid.org/[\d-]+$")

    def test_default_run_all_does_not_approve(self):
        for query in self.previews:
            self.db.execute(query).fetchall()
        self.db.execute(self.insert)
        self.assertEqual(self.count(), 0)

    def test_deliberate_accept_and_rerun_before_and_after_build(self):
        self.enable()
        self.db.execute(self.insert)
        self.assertEqual(self.count(), 5)
        self.db.execute(self.insert)
        self.assertEqual(self.count(), 5)
        self.db.execute("UPDATE entity_resolution_review_current SET status='verified'")
        self.db.execute(self.insert)
        self.assertEqual(self.count(), 5)

    def test_existing_accept_is_not_duplicated(self):
        self.add_decision("accept")
        self.enable()
        self.db.execute(self.insert)
        self.assertEqual(self.count(), 5)

    def test_changed_evidence_blocks_entire_batch(self):
        self.db.execute("UPDATE entity_resolution_review_current SET evidence_hash='changed' WHERE researcher_id='0'")
        self.assert_blocked()

    def test_missing_candidate_blocks_entire_batch(self):
        self.db.execute("DELETE FROM entity_resolution_review_current WHERE researcher_id='0'")
        self.assert_blocked()

    def test_duplicate_join_row_blocks_entire_batch(self):
        self.db.execute("INSERT INTO entity_resolution_review_current SELECT * FROM entity_resolution_review_current WHERE researcher_id='0'")
        self.assert_blocked()

    def test_unbuilt_rejection_is_not_overridden(self):
        self.add_decision("reject")
        self.assert_blocked()

    def test_stale_accept_is_not_overridden(self):
        self.add_decision("accept", "old-hash")
        self.assert_blocked()

    def test_cross_profile_author_blocks_entire_batch(self):
        self.db.execute("""INSERT INTO entity_resolution_review_current
          SELECT 'other', 'other', openalex_author_id, evidence_hash, match_method, status, quality_flags
          FROM entity_resolution_review_current WHERE researcher_id='0'""")
        self.assert_blocked()

    def test_directory_duplicate_issue_blocks_entire_batch(self):
        self.db.execute("INSERT INTO quality_issues_current VALUES ('researcher','0','other_directory_urls_same_name')")
        self.assert_blocked()

    def test_split_orcid_blocks_entire_batch(self):
        self.db.execute("INSERT INTO authors_current VALUES ('another-author',?)", (self.targets[0][3],))
        self.assert_blocked()

    def test_work_orcid_conflict_blocks_entire_batch(self):
        self.db.execute("INSERT INTO work_authorships_current VALUES (?,?,?,?)",
                        (self.targets[0][2], "different-orcid", "work", "[]"))
        self.assert_blocked()

    def test_missing_or_conflicting_profile_orcid_blocks_batch(self):
        self.db.execute("UPDATE researchers_current SET profile_orcids='[]' WHERE researcher_id='0'")
        self.assert_blocked()

    def test_quality_flag_blocks_entire_batch(self):
        self.db.execute("UPDATE entity_resolution_review_current SET quality_flags='[\"orcid_conflict\"]' WHERE researcher_id='0'")
        self.assert_blocked()


if __name__ == "__main__":
    unittest.main()
