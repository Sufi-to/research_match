import copy
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import re
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("silver", ROOT / "notebooks/05_build_silver.py")
silver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(silver)
WHEN = datetime(2026, 9, 5)
ORCID = "https://orcid.org/0000-0002-1825-0097"
URL = "https://www.zu.ac.ae/CB/faculty_and_staff/_profiles/Example_Person.aspx"
AID = "https://openalex.org/A1"
GID = "https://openalex.org/G1"


def raw(entity, data, record_key):
    return {"openalex_" + entity + "_id": data["id"], "raw_json": json.dumps(data),
            "record_key": record_key, "fetched_at": WHEN, "source_updated_at": WHEN}


def fixture():
    data = {table: [] for table in silver.BRONZE_KEYS}
    data["zu_faculty_discovered_profiles"] = [
        {"profile_url": URL, "displayed_name": "Example_Person", "college_code": "CB",
         "personnel_section": "faculty", "record_key": "directory1", "discovered_at": WHEN},
        {"profile_url": URL, "displayed_name": "Example Person", "college_code": "CB",
         "personnel_section": "administrators", "record_key": "directory2", "discovered_at": WHEN}]
    data["zu_faculty_pages_raw"] = [{"page_url": URL, "page_type": "profile", "record_key": "profile1",
        "fetched_at": WHEN, "raw_html": '<nav>Do not include navigation</nav><div class="facutly-detail">'
        '<h6>Bio</h6><p>Researcher biography.</p><h6>Research Interests</h6><p>Machine learning.</p>'
        '</div><a href="' + ORCID + '">ORCID</a>'}]
    data["openalex_authors_raw"] = [raw("author", {"id": AID, "display_name": "Example Person", "orcid": ORCID,
                                                "works_count": 2, "last_known_institutions": [{"id": silver.ZU_ID}]}, "author1")]
    topic = {"id": "https://openalex.org/T1", "display_name": "Machine learning", "score": 0.8,
             "field": {"display_name": "Computer Science"}}
    work = {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1234/ABC", "title": "Learning systems",
            "publication_year": 2024, "type": "article", "abstract_inverted_index": None,
            "authorships": [{"author": {"id": AID, "display_name": "Example Person", "orcid": ORCID},
                            "institutions": [{"id": silver.ZU_ID}], "is_corresponding": True}],
            "topics": [topic], "primary_topic": topic, "awards": [{"id": GID}, {"id": "https://openalex.org/G404"}]}
    retracted = copy.deepcopy(work)
    retracted.update(id="https://openalex.org/W2", doi=None, is_retracted=True, awards=[])
    data["openalex_works_raw"] = [raw("work", work, "work1"), raw("work", retracted, "work2")]
    xml = '''<record xmlns="http://www.openarchives.org/OAI/2.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
    <header><identifier>oai:1</identifier></header><metadata><dc:dc>
    <dc:title>Learning systems</dc:title><dc:creator>Person, Example</dc:creator>
    <dc:identifier>info:doi/10.1234/ABC</dc:identifier><dc:identifier>https://doi.org/10.1234/abc</dc:identifier>
    <dc:description>Repository description.</dc:description><dc:date>2024-01-01</dc:date>
    <dc:subject>Machine learning</dc:subject></dc:dc></metadata></record>'''
    data["zu_scholars_oai_raw"] = [{"oai_identifier": "oai:1", "metadata_prefix": "oai_dc",
                                  "raw_record_xml": xml, "is_deleted": False, "record_key": "oai1", "fetched_at": WHEN}]
    data["openalex_funders_raw"] = [raw("funder", {"id": "https://openalex.org/F1", "display_name": "Example Funder", "country_code": "AE"}, "funder1")]
    data["openalex_awards_raw"] = [raw("award", {"id": GID, "display_name": "Example Award", "funder": {"id": "https://openalex.org/F1"},
                                              "amount": 100.0, "currency": "AED", "investigators": [{"name": "Example Person"}]}, "award1")]
    return data


def graph(data=None, decisions=(), build_id="test"):
    data = fixture() if data is None else data
    return silver.build_graph(lambda table: iter(data[table]), decisions, build_id)


def decision(candidate, action="accept", evidence_hash=None, decision_id="decision1"):
    return {"decision_id": decision_id, "candidate_id": candidate["candidate_id"],
            "evidence_hash": evidence_hash or candidate["evidence_hash"], "decision": action,
            "rationale": "Checked profile ORCID and publication evidence", "reviewed_by": "reviewer", "reviewed_at": WHEN}


class NormalizationTests(unittest.TestCase):
    def test_doi_forms_and_non_doi_urls(self):
        for value in ["INFO:DOI/10.1234/ABC", "https://doi.org/10.1234/ABC", "https://10.1234/ABC", "doi:10.1234/ABC"]:
            self.assertEqual(silver.normalize_doi(value), "10.1234/abc")
        self.assertIsNone(silver.normalize_doi("https://zuscholars.zu.ac.ae/works/1"))

    def test_orcid_checksum_and_name_aliases(self):
        self.assertEqual(silver.normalize_orcid(ORCID), ORCID)
        self.assertIsNone(silver.normalize_orcid(ORCID[:-1] + "8"))
        self.assertEqual(silver.normalized_name("Feras Al‐Obeidat"), silver.normalized_name("Feras_Al Obeidat"))

    def test_abstract_order_invalid_positions_and_gaps(self):
        self.assertEqual(silver.reconstruct_abstract({"systems": [1], "Learning": [0]}), ("Learning systems", None))
        for index in [{"a": [0], "b": [0]}, {"a": [-1]}, {"a": [100000]}, {"a": [True]}]:
            self.assertEqual(silver.reconstruct_abstract(index)[1], "invalid_abstract_index")
        self.assertEqual(silver.reconstruct_abstract({"a": [1]})[1], "incomplete_abstract_index")

    def test_profile_scope_and_missing_layout(self):
        text = fixture()["zu_faculty_pages_raw"][0]["raw_html"]
        bio, research, orcids, parsed = silver.profile_fields(text)
        self.assertEqual(bio, "Researcher biography.")
        self.assertEqual(research, "Machine learning.")
        self.assertEqual(orcids, [ORCID])
        self.assertTrue(parsed)
        self.assertFalse(silver.profile_fields("<nav>Research interests</nav>")[3])

    def test_tabbed_cti_profile_sections(self):
        html = '<div class="facutly-detail"><h4>Professor</h4><div class="tab-content">' \
               '<div id="summary_bio"><div> </div><p>CTI biography</p></div>' \
               '<div id="qualification">Education only</div>' \
               '<div id="research_prof_act"><p>AI research</p></div></div></div>'
        bio, research, _, _ = silver.profile_fields(html)
        self.assertEqual(bio, "CTI biography")
        self.assertEqual(research, "AI research")


class GraphTests(unittest.TestCase):
    def test_directory_roles_preserved_without_duplicate_researcher(self):
        g = graph()
        self.assertEqual(len(g.rows("researchers")), 1)
        self.assertEqual(g.rows("researchers")[0]["personnel_sections"], ["administrators", "faculty"])
        self.assertEqual(len(g.rows("researcher_aliases")), 2)

    def test_no_automatic_identity_or_award_attribution(self):
        g = graph()
        self.assertEqual(g.rows("entity_resolution_review")[0]["match_method"], "profile_orcid")
        self.assertEqual(g.rows("entity_resolution_review")[0]["status"], "pending")
        for table in ("researcher_works", "researcher_topic_scores", "coauthor_edges", "researcher_awards"):
            self.assertEqual(g.rows(table), [])

    def test_accepted_identity_retraction_and_scoring(self):
        candidate = graph().rows("entity_resolution_review")[0]
        g = graph(decisions=[decision(candidate)])
        self.assertEqual(len(g.rows("researcher_works")), 2)
        self.assertEqual(sum(r["eligible_for_expertise"] for r in g.rows("researcher_works")), 1)
        score = g.rows("researcher_topic_scores")[0]
        self.assertEqual(score["supporting_work_count"], 1)
        self.assertAlmostEqual(score["score"], 0.8 * 0.5 ** (2 / 5))
        self.assertEqual(g.rows("researcher_awards"), [])

    def test_rejected_and_stale_decisions_do_not_create_links(self):
        candidate = graph().rows("entity_resolution_review")[0]
        for d in [decision(candidate, "reject"), decision(candidate, evidence_hash="stale")]:
            self.assertEqual(graph(decisions=[d]).rows("researcher_works"), [])
        g = graph(decisions=[decision(candidate, evidence_hash="stale")])
        self.assertIn("stale_or_missing_candidate", {r["issue_code"] for r in g.rows("quality_issues")})

    def test_orcid_conflict_blocks_acceptance(self):
        data = fixture()
        author = json.loads(data["openalex_authors_raw"][0]["raw_json"])
        author["orcid"] = "https://orcid.org/0000-0001-5109-3700"
        data["openalex_authors_raw"][0]["raw_json"] = json.dumps(author)
        candidate = graph(data).rows("entity_resolution_review")[0]
        self.assertIn("orcid_conflict", candidate["quality_flags"])
        with self.assertRaisesRegex(ValueError, "conflicting ORCID"):
            graph(data, [decision(candidate)])

    def test_same_author_cannot_be_approved_for_two_profiles(self):
        data = fixture()
        directory = copy.deepcopy(data["zu_faculty_discovered_profiles"][0])
        directory.update(profile_url=URL.replace("CB", "CTI"), record_key="directory3")
        data["zu_faculty_discovered_profiles"].append(directory)
        profile = copy.deepcopy(data["zu_faculty_pages_raw"][0])
        profile.update(page_url=directory["profile_url"], record_key="profile2")
        data["zu_faculty_pages_raw"].append(profile)
        candidates = graph(data).rows("entity_resolution_review")
        with self.assertRaisesRegex(ValueError, "multiple pilot researchers"):
            graph(data, [decision(c, decision_id=str(i)) for i, c in enumerate(candidates)])

    def test_split_author_ids_same_orcid_are_reviewable(self):
        data = fixture()
        extra = json.loads(data["openalex_authors_raw"][0]["raw_json"])
        extra["id"] = "https://openalex.org/A2"
        data["openalex_authors_raw"].append(raw("author", extra, "author2"))
        candidates = graph(data).rows("entity_resolution_review")
        self.assertEqual(len(candidates), 2)
        self.assertTrue(all("shared_orcid_multiple_author_ids" in c["quality_flags"] for c in candidates))
        g = graph(data, [decision(c, decision_id=str(i)) for i, c in enumerate(candidates)])
        self.assertEqual(len(g.rows("researcher_works")), 2)

    def test_oai_unique_doi_enrichment_and_raw_creator_preservation(self):
        g = graph()
        work = next(w for w in g.rows("works") if w["work_id"] == "oa:W1")
        self.assertEqual(work["abstract_source"], "oai_description")
        self.assertEqual(work["abstract"], "Repository description.")
        self.assertEqual(work["oai_subjects"], ["Machine learning"])
        self.assertEqual(len(g.rows("works")), 2)
        self.assertEqual([r["raw_author_name"] for r in g.rows("work_authorships") if r["source_system"] == "zu_scholars"], ["Person, Example"])

    def test_matching_titles_without_doi_do_not_merge(self):
        data = fixture()
        data["zu_scholars_oai_raw"][0]["raw_record_xml"] = data["zu_scholars_oai_raw"][0]["raw_record_xml"].replace("10.1234/ABC", "missing").replace("10.1234/abc", "missing")
        g = graph(data)
        self.assertEqual(len(g.rows("works")), 3)
        self.assertFalse(next(w for w in g.rows("works") if w["work_id"].startswith("oai:"))["eligible_for_expertise"])

    def test_ambiguous_doi_does_not_merge_or_feed_expertise(self):
        data = fixture()
        other = json.loads(data["openalex_works_raw"][0]["raw_json"])
        other["id"] = "https://openalex.org/W3"
        data["openalex_works_raw"].append(raw("work", other, "work3"))
        g = graph(data)
        self.assertEqual(len(g.rows("works")), 4)
        self.assertTrue(all(not w["eligible_for_expertise"] for w in g.rows("works")))

    def test_oai_tombstone_not_used_for_enrichment(self):
        data = fixture()
        data["zu_scholars_oai_raw"][0]["is_deleted"] = True
        g = graph(data)
        self.assertIsNone(next(w for w in g.rows("works") if w["work_id"] == "oa:W1")["abstract"])
        self.assertIn("oai_deleted", {r["issue_code"] for r in g.rows("quality_issues")})

    def test_missing_awards_preserved(self):
        links = graph().rows("work_awards")
        self.assertEqual({r["resolution_status"] for r in links}, {"resolved", "unresolved"})

    def test_empty_funding_tables_do_not_block_research_graph(self):
        data = fixture()
        data["openalex_awards_raw"] = []
        data["openalex_funders_raw"] = []
        reader, _ = silver.bounded_read(lambda t: iter(data[t]))
        g = silver.build_graph(reader)
        self.assertEqual(len(g.rows("works")), 2)
        self.assertTrue(all(r["resolution_status"] == "unresolved" for r in g.rows("work_awards")))

    def test_coauthor_edges_only_use_verified_nonretracted_shared_works(self):
        data = fixture()
        other_orcid = "https://orcid.org/0000-0001-5109-3700"
        other_url = URL.replace("Example_Person", "Other_Person")
        entry = copy.deepcopy(data["zu_faculty_discovered_profiles"][0])
        entry.update(profile_url=other_url, displayed_name="Other Person", record_key="other_directory")
        data["zu_faculty_discovered_profiles"].append(entry)
        profile = copy.deepcopy(data["zu_faculty_pages_raw"][0])
        profile.update(page_url=other_url, record_key="other_profile", raw_html=profile["raw_html"].replace(ORCID, other_orcid))
        data["zu_faculty_pages_raw"].append(profile)
        data["openalex_authors_raw"].append(raw("author", {"id": "https://openalex.org/A2", "display_name": "Other Person", "orcid": other_orcid}, "other_author"))
        for work in data["openalex_works_raw"]:
            payload = json.loads(work["raw_json"])
            payload["authorships"].append({"author": {"id": "https://openalex.org/A2", "display_name": "Other Person", "orcid": other_orcid}})
            work["raw_json"] = json.dumps(payload)
        candidates = graph(data).rows("entity_resolution_review")
        g = graph(data, [decision(c, decision_id=str(i)) for i, c in enumerate(candidates)])
        self.assertEqual(len(g.rows("coauthor_edges")), 1)
        self.assertEqual(g.rows("coauthor_edges")[0]["work_ids"], ["oa:W1"])
        self.assertEqual(g.rows("coauthor_edges")[0]["shared_work_count"], 1)
        self.assertTrue(all(s["supporting_work_count"] == 1 for s in g.rows("researcher_topic_scores")))

    def test_later_rejection_removes_previous_accepted_links_on_rebuild(self):
        c = graph().rows("entity_resolution_review")[0]
        accepted = decision(c)
        rejected = decision(c, "reject", decision_id="decision2")
        rejected["reviewed_at"] = datetime(2026, 9, 6)
        self.assertTrue(graph(decisions=[accepted]).rows("researcher_works"))
        self.assertEqual(graph(decisions=[accepted, rejected]).rows("researcher_works"), [])

    def test_repeated_transforms_have_stable_business_keys(self):
        a, b = graph(build_id="a"), graph(build_id="b")
        for table in silver.CONTRACTS:
            strip = lambda rows: sorted([{k: v for k, v in r.items() if k != "build_id"} for r in rows], key=lambda r: r["row_key"])
            self.assertEqual(strip(a.rows(table)), strip(b.rows(table)), table)


class ContractAndRuntimeTests(unittest.TestCase):
    def test_write_buffers_are_row_and_byte_bounded(self):
        rows = [{"value": "x" * 15} for _ in range(5)]
        self.assertEqual([len(c) for c in silver.dataframe_chunks(rows, max_rows=2)], [2, 2, 1])
        self.assertTrue(all(len(json.dumps(c[0]).encode()) <= 40 for c in silver.dataframe_chunks(rows, max_bytes=40)))
        with self.assertRaisesRegex(ValueError, "write buffer"):
            list(silver.dataframe_chunks(rows, max_bytes=1))

    def test_ddl_contracts_and_current_views(self):
        ddl = (ROOT / "sql/04_create_silver_tables.sql").read_text()
        for table in [*silver.CONTRACTS, *silver.CONTROL_CONTRACTS]:
            body = re.search(r"CREATE TABLE IF NOT EXISTS " + table + r" \((.*?)\) USING DELTA", ddl, re.S)[1]
            fields = []
            for line in body.splitlines():
                m = re.match(r"\s*(\w+)\s+(ARRAY<STRING>|STRING|TIMESTAMP|BIGINT|INT|BOOLEAN|DOUBLE)(\s+NOT NULL)?", line)
                if m:
                    fields.append((m[1], m[2].lower(), not bool(m[3])))
            self.assertEqual(silver.fields(table), fields)
        self.assertIn("WHERE status = 'succeeded'", ddl)
        for table in silver.CONTRACTS:
            self.assertIn("CREATE VIEW IF NOT EXISTS " + table + "_current", ddl)

    def test_input_duplicate_and_size_guards(self):
        data = fixture()
        data["openalex_authors_raw"] *= 2
        reader, _ = silver.bounded_read(lambda t: iter(data[t]))
        with self.assertRaisesRegex(ValueError, "multiple latest"):
            list(reader("openalex_authors_raw"))
        with patch.object(silver, "MAX_INPUT_BYTES", 1):
            reader, _ = silver.bounded_read(lambda t: iter(fixture()[t]))
            with self.assertRaisesRegex(ValueError, "input limit"):
                list(reader("openalex_authors_raw"))

    @patch.object(silver, "spark_schema", return_value="schema")
    def test_failed_write_never_publishes_success(self, _):
        spark = Mock()
        spark.sql.return_value.first.return_value = {"n": 999, "keys": 999}
        g = graph()
        run = {"build_id": "test", "status": "started"}
        with self.assertRaisesRegex(ValueError, "Persisted row count"):
            silver.write_build(spark, "workspace", "silver", g, run)
        self.assertEqual(run["status"], "failed")
        self.assertIn("MERGE INTO", spark.sql.call_args.args[0])
        self.assertNotIn("DELETE", spark.sql.call_args.args[0])
        spark.catalog.dropTempView.assert_called_once()


if __name__ == "__main__":
    unittest.main()
