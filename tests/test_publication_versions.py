import copy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import unittest

from test_silver import decision, fixture, raw, silver, ORCID, URL, AID


def pair_fixture():
    data = fixture()
    rule = silver.REVIEWED_WORK_VERSIONS[0]
    work = json.loads(data["openalex_works_raw"][0]["raw_json"])
    data["openalex_works_raw"] = []
    data["zu_scholars_oai_raw"] = []
    for role in ("concept", "version"):
        item = copy.deepcopy(work)
        item.update(id=rule[role + "_work_id"].replace("oa:", "https://openalex.org/"),
                    doi=rule[role]["doi"], title=rule["expected_title"], type="preprint",
                    is_retracted=False)
        data["openalex_works_raw"].append(raw("work", item, role + "-source"))
    return data


def graph(data=None, accept=True, evidence=None):
    data = pair_fixture() if data is None else data
    read = lambda table: iter(data[table])
    pending = silver.build_graph(read, version_evidence=evidence)
    decisions = [decision(c, decision_id=c["candidate_id"])
                 for c in pending.rows("entity_resolution_review")] if accept else []
    return silver.build_graph(read, decisions, version_evidence=evidence)


def mutate_work(data, index, **changes):
    row = data["openalex_works_raw"][index]
    content = json.loads(row["raw_json"])
    content.update(changes)
    row["raw_json"] = json.dumps(content)


class PublicationVersionTests(unittest.TestCase):
    def test_records_preserved_contribution_and_score_counted_once(self):
        g = graph()
        self.assertEqual(len(g.rows("works")), 2)
        self.assertEqual(len(g.rows("work_sources")), 2)
        self.assertEqual(len(g.rows("work_authorships")), 2)
        self.assertEqual(len(g.rows("researcher_works")), 2)
        self.assertEqual(len(g.rows("work_contributions")), 2)
        rows = g.rows("researcher_contributions")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["publication_type"], "preprint")
        self.assertTrue(rows[0]["eligible_for_expertise"])
        self.assertEqual(len(rows[0]["source_work_ids"]), 2)
        score = g.rows("researcher_topic_scores")[0]
        self.assertEqual(score["supporting_work_count"], 1)
        self.assertAlmostEqual(score["score"], 0.8 * 0.5 ** (2 / 5))
        works = {w["work_id"]: w for w in g.rows("works")}
        alias = works[silver.REVIEWED_WORK_VERSIONS[0]["concept_work_id"]]
        self.assertFalse(alias["eligible_for_expertise"])
        self.assertIn("concept_doi_alias", alias["quality_flags"])
        self.assertFalse(alias["is_retracted"])

    def test_evidence_provenance_and_digest(self):
        r = graph().rows("work_version_relationships")[0]
        self.assertEqual(r["evidence_sha256"], hashlib.sha256(r["evidence_json"].encode()).hexdigest())
        self.assertEqual(len(r["source_urls"]), 2)
        self.assertEqual(json.loads(r["evidence_json"]), silver.REVIEWED_WORK_VERSIONS[0])

    def test_same_title_different_doi_without_review_is_not_grouped(self):
        g = graph(evidence=[])
        self.assertEqual(len(g.rows("researcher_contributions")), 2)
        self.assertEqual(g.rows("work_version_relationships"), [])
        self.assertEqual(g.rows("researcher_topic_scores")[0]["supporting_work_count"], 2)

    def test_invalid_reciprocal_evidence_fails(self):
        evidence = copy.deepcopy(silver.REVIEWED_WORK_VERSIONS)
        evidence[0]["version"]["relatedIdentifiers"] = []
        with self.assertRaisesRegex(ValueError, "reciprocal"):
            graph(evidence=evidence)

    def test_overlapping_rules_fail(self):
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            graph(evidence=silver.REVIEWED_WORK_VERSIONS * 2)

    def test_missing_member_fails_closed(self):
        data = pair_fixture()
        data["openalex_works_raw"].pop()
        with self.assertRaisesRegex(ValueError, "missing or ambiguous"):
            graph(data)

    def test_additional_record_same_doi_fails_closed(self):
        data = pair_fixture()
        item = json.loads(data["openalex_works_raw"][0]["raw_json"])
        item["id"] = "https://openalex.org/W999"
        data["openalex_works_raw"].append(raw("work", item, "extra-source"))
        with self.assertRaisesRegex(ValueError, "missing or ambiguous"):
            graph(data)

    def test_both_dois_changing_cannot_silently_disable_the_rule(self):
        data = pair_fixture()
        mutate_work(data, 0, doi="10.1234/changed-concept")
        mutate_work(data, 1, doi="10.1234/changed-version")
        with self.assertRaisesRegex(ValueError, "missing or ambiguous"):
            graph(data)

    def test_changed_title_or_type_needs_review(self):
        for change in ({"title": "A different study"}, {"type": "article"}):
            data = pair_fixture()
            mutate_work(data, 1, **change)
            with self.assertRaisesRegex(ValueError, "title/type changed"):
                graph(data)

    def test_retraction_on_either_member_cannot_be_bypassed(self):
        for index in (0, 1):
            data = pair_fixture()
            mutate_work(data, index, is_retracted=True)
            g = graph(data)
            self.assertFalse(g.rows("researcher_contributions")[0]["eligible_for_expertise"])
            self.assertEqual(g.rows("researcher_topic_scores"), [])
            self.assertFalse(any(r["eligible_for_expertise"] for r in g.rows("researcher_works")))

    def test_no_authorship_transfer_from_concept_to_version(self):
        data = pair_fixture()
        mutate_work(data, 1, authorships=[{"author": {"id": "https://openalex.org/A999", "display_name": "Other Person"}}])
        g = graph(data)
        self.assertEqual(len(g.rows("researcher_works")), 1)
        self.assertFalse(g.rows("researcher_contributions")[0]["eligible_for_expertise"])
        self.assertEqual(g.rows("researcher_topic_scores"), [])

    def test_work_orcid_conflict_on_representative_still_blocks_link(self):
        data = pair_fixture()
        mutate_work(data, 1, authorships=[{"author": {"id": AID, "display_name": "Example Person",
                                                     "orcid": "https://orcid.org/0000-0001-5109-3700"}}])
        g = graph(data)
        self.assertFalse(g.rows("researcher_contributions")[0]["eligible_for_expertise"])
        self.assertEqual(g.rows("researcher_topic_scores"), [])

    def test_unapproved_identity_still_has_no_contributions(self):
        g = graph(accept=False)
        self.assertEqual(g.rows("researcher_contributions"), [])
        self.assertEqual(g.rows("researcher_topic_scores"), [])

    def test_concept_topics_not_transferred_to_version(self):
        data = pair_fixture()
        mutate_work(data, 1, topics=[], primary_topic=None)
        self.assertEqual(graph(data).rows("researcher_topic_scores"), [])

    def test_contribution_keys_stable_and_curated_evidence_changes_fingerprint_input(self):
        a, b = graph(), graph()
        self.assertEqual(a.rows("researcher_contributions"), b.rows("researcher_contributions"))
        source = silver.__file__
        self.assertIn("key(TRANSFORM_VERSION, REVIEWED_WORK_VERSIONS,", Path(source).read_text())
        self.assertNotEqual(silver.key(silver.TRANSFORM_VERSION, silver.REVIEWED_WORK_VERSIONS),
                            silver.key(silver.TRANSFORM_VERSION, []))

    def test_shared_concept_and_version_create_one_coauthor_edge(self):
        data = pair_fixture()
        directory = copy.deepcopy(data["zu_faculty_discovered_profiles"][0])
        url2 = URL.replace("Example_Person", "Second_Person")
        directory.update(profile_url=url2, displayed_name="Second Person", record_key="dir2")
        data["zu_faculty_discovered_profiles"].append(directory)
        profile = copy.deepcopy(data["zu_faculty_pages_raw"][0])
        orcid2 = "https://orcid.org/0000-0001-5109-3700"
        profile.update(page_url=url2, record_key="page2", raw_html=profile["raw_html"].replace(ORCID, orcid2))
        data["zu_faculty_pages_raw"].append(profile)
        data["openalex_authors_raw"].append(raw("author", {"id": "https://openalex.org/A2", "display_name": "Second Person", "orcid": orcid2}, "author2"))
        for row in data["openalex_works_raw"]:
            work = json.loads(row["raw_json"])
            work["authorships"].append({"author": {"id": "https://openalex.org/A2", "display_name": "Second Person", "orcid": orcid2}})
            row["raw_json"] = json.dumps(work)
        g = graph(data)
        self.assertEqual(len(g.rows("researcher_works")), 4)
        self.assertEqual(len(g.rows("researcher_contributions")), 2)
        self.assertEqual(len(g.rows("coauthor_edges")), 1)
        self.assertEqual(g.rows("coauthor_edges")[0]["shared_work_count"], 1)

    def test_readonly_sql_checks_execute_against_fixture_graph(self):
        # Portable relational checks only; this is not a Databricks runtime test.
        g = graph()
        with sqlite3.connect(":memory:") as db:
            for table in silver.CONTRACTS:
                columns = [field[0] for field in silver.fields(table)]
                db.execute("CREATE TABLE " + table + "_current (" + ",".join(columns) + ")")
                for row in g.rows(table):
                    values = [json.dumps(row[c]) if isinstance(row[c], list) else
                              row[c].isoformat() if hasattr(row[c], "isoformat") else row[c] for c in columns]
                    db.execute("INSERT INTO " + table + "_current VALUES (" + ",".join("?" for _ in values) + ")", values)
            db.execute("CREATE TABLE current_build (build_id, transform_version, completed_at)")
            db.execute("INSERT INTO current_build VALUES ('test', ?, '2026-09-10')", (silver.TRANSFORM_VERSION,))
            sql = (Path(silver.__file__).resolve().parents[1] / "sql/08_check_publication_versions.sql").read_text()
            sql = re.sub(r"--[^\n]*", "", sql)
            sql = re.sub(r"USE (CATALOG|SCHEMA) \w+;", "", sql)
            saw_checks = False
            for statement in sql.split(";"):
                if not statement.strip():
                    continue
                cursor = db.execute(statement)
                results = cursor.fetchall()
                if cursor.description[0][0] == "check_name":
                    saw_checks = True
                    self.assertEqual(len(results), 3)
                    self.assertTrue(all(row[1] == 0 for row in results))
            self.assertTrue(saw_checks)


if __name__ == "__main__":
    unittest.main()
