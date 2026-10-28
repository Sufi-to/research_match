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

ROOT = Path(__file__).resolve().parents[1]


class IdentityBatchTests(unittest.TestCase):
    SQL = (ROOT / "sql/07_review_identity_batch_02.sql").read_text()
    PREFIX = "rm_batch02"
    NAMES = {"Ahmed Shuhaiber", "Andrew Marrington", "Dima Rabadi", "Fatma Outay", "Graham Manville"}

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
        adapted = re.sub(r"--[^\n]*", "", self.SQL)
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
        self.targets = self.db.execute("SELECT * FROM " + self.PREFIX + "_targets").fetchall()
        for i, (_, cid, aid, orcid, evidence) in enumerate(self.targets):
            self.db.execute("INSERT INTO entity_resolution_review_current VALUES (?,?,?,?,?,?,?)",
                            (cid, str(i), aid, evidence, "profile_orcid", "pending", "[]"))
            self.db.execute("INSERT INTO researchers_current VALUES (?,?,?,?)",
                            (str(i), "profile:" + str(i), json.dumps([orcid]), "[]"))
            self.db.execute("INSERT INTO authors_current VALUES (?,?)", (aid, orcid))

    def enable(self):
        self.db.executescript("DROP VIEW {0}_permission; CREATE TEMP VIEW {0}_permission AS "
                              "SELECT true AS approve_reviewed_batch;".format(self.PREFIX))

    def count(self):
        return self.db.execute("SELECT COUNT(*) FROM identity_decisions").fetchone()[0]

    def add_decision(self, action, evidence=None):
        _, cid, _, _, expected = self.targets[0]
        self.db.execute("INSERT INTO identity_decisions VALUES (?,?,?,?,?,?,?)",
                        ("existing", cid, evidence or expected, action, "Reviewed", "User", "2026-09-09"))

    def assert_blocked(self):
        self.enable()
        before = self.count()
        self.assertEqual(self.db.execute("SELECT all_checks_pass FROM " + self.PREFIX + "_gate").fetchone()[0], 0)
        self.db.execute(self.insert)
        self.assertEqual(self.count(), before)

    def test_exact_whitelist_and_hashes(self):
        self.assertEqual({t[0] for t in self.targets}, self.NAMES)
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
        self.assertEqual(self.count(), len(self.targets))
        self.db.execute(self.insert)
        self.assertEqual(self.count(), len(self.targets))
        self.db.execute("UPDATE entity_resolution_review_current SET status='verified'")
        self.db.execute(self.insert)
        self.assertEqual(self.count(), len(self.targets))

    def test_existing_accept_is_not_duplicated(self):
        self.add_decision("accept")
        self.enable()
        self.db.execute(self.insert)
        self.assertEqual(self.count(), len(self.targets))

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


class IdentityBatch03Tests(IdentityBatchTests):
    SQL = (ROOT / "sql/15_review_identity_batch_03.sql").read_text()
    PREFIX = "rm_batch03"
    NAMES = {"Ahmad Wazan", "Alena Connolly", "Asad Khattak", "Babar Shah", "Basel Almourad", "Fatma Taher"}

    def test_no_overlap_with_batch_02(self):
        earlier = IdentityBatchTests.SQL
        for _, cid, aid, _, _ in self.targets:
            self.assertNotIn(cid, earlier)
            self.assertNotIn(aid, earlier)


class IdentityBatch04Tests(IdentityBatch03Tests):
    """Split ORCIDs: several author records per researcher, accepted together."""
    SQL = (ROOT / "sql/16_review_identity_batch_04.sql").read_text()
    PREFIX = "rm_batch04"
    NAMES = {"Ahed Abugabah", "Ahmed Seffah", "Belal AlSinglawi", "Edmund Evangelista", "Farkhund Iqbal"}
    VERIFIED_SIBLING = "https://openalex.org/A5068661949"  # Ahed Abugabah's main record

    def setUp(self):
        super().setUp()
        # Regroup the fixture: records sharing an ORCID belong to one researcher.
        people = {}
        for _, cid, _, orcid, _ in self.targets:
            rid = people.setdefault(orcid, str(len(people)))
            self.db.execute("UPDATE entity_resolution_review_current SET researcher_id=?, "
                            "quality_flags='[\"shared_orcid_multiple_author_ids\"]' WHERE candidate_id=?", (rid, cid))
        self.db.execute("DELETE FROM researchers_current")
        for orcid, rid in people.items():
            self.db.execute("INSERT INTO researchers_current VALUES (?,?,?,?)",
                            (rid, "profile:" + rid, json.dumps([orcid]), "[]"))
        ahed = next(t[3] for t in self.targets if t[0] == "Ahed Abugabah")
        self.db.execute("INSERT INTO authors_current VALUES (?,?)", (self.VERIFIED_SIBLING, ahed))
        self.db.execute("INSERT INTO entity_resolution_review_current VALUES (?,?,?,?,?,?,?)",
                        ("main", people[ahed], self.VERIFIED_SIBLING, "h", "profile_orcid", "verified",
                         '["shared_orcid_multiple_author_ids"]'))

    def test_every_split_record_is_listed(self):
        by_orcid = {}
        for name, _, aid, orcid, _ in self.targets:
            by_orcid.setdefault(orcid, set()).add(aid)
            self.assertEqual({t[0] for t in self.targets if t[3] == orcid}, {name})
        self.assertEqual(len(self.targets), 11)
        self.assertTrue(all(len(aids) >= 2 for aids in by_orcid.values()))

    def test_no_overlap_with_batch_03(self):
        for _, cid, aid, _, _ in self.targets:
            self.assertNotIn(cid, IdentityBatch03Tests.SQL)
            self.assertNotIn(aid, IdentityBatch03Tests.SQL)

    def test_unflagged_candidate_blocks_entire_batch(self):
        self.db.execute("UPDATE entity_resolution_review_current SET quality_flags='[]' WHERE candidate_id=?",
                        (self.targets[0][1],))
        self.assert_blocked()

    def test_sibling_verified_for_another_researcher_blocks_batch(self):
        self.db.execute("UPDATE entity_resolution_review_current SET researcher_id='other' WHERE candidate_id='main'")
        self.assert_blocked()

    def test_records_of_one_orcid_split_across_researchers_block_batch(self):
        seffah = [t[1] for t in self.targets if t[0] == "Ahmed Seffah"]
        self.db.execute("UPDATE entity_resolution_review_current SET researcher_id='elsewhere' WHERE candidate_id=?",
                        (seffah[0],))
        self.db.execute("INSERT INTO researchers_current SELECT 'elsewhere', 'p', profile_orcids, quality_flags "
                        "FROM researchers_current WHERE researcher_id=("
                        "SELECT researcher_id FROM entity_resolution_review_current WHERE candidate_id=?)", (seffah[1],))
        self.assert_blocked()


if __name__ == "__main__":
    unittest.main()
