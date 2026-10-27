import ast
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_ai_search import documents, scan_payload

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("matching", ROOT / "notebooks/13_match_researchers.py")
matching = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matching)


def fixture():
    docs = documents()
    titles = ["Blockchain security", "Digital forensic malware detection", "Brain tumor medical image detection",
              "Organizational employee technology adoption", "Autonomous vehicle traffic transportation planning",
              "Career employability higher education", "Reflective teaching higher education"]
    data = {t: [] for t in matching.EVIDENCE_COLUMNS}
    for i, (d, title) in enumerate(zip(docs, titles)):
        d.update(eligible_contribution_count=1, eligible_preprint_count=0)
        base = {"build_id": "gold", "silver_build_id": "silver", "row_key": str(i), "researcher_id": d["researcher_id"]}
        p = {**base, "publication_evidence_id": f"p{i}", "contribution_id": f"c{i}", "representative_work_id": f"oa:W{i}",
             "title": title, "abstract_excerpt": "", "publication_year": 2025, "publication_type": "article",
             "is_preprint": False, "evidence_url": f"https://doi.org/10.1/{i}", "quality_flags": []}
        t = {**base, "topic_id": f"t{i}", "topic_name": title, "supporting_contribution_count": 1}
        e = {**base, "topic_id": f"t{i}", "publication_evidence_id": f"p{i}", "contribution_id": f"c{i}",
             "representative_work_id": f"oa:W{i}"}
        for name, row in zip(data, (p, t, e)):
            data[name].append(row)
    snap = {"documents": docs, "gold_build_id": "gold", "silver_build_id": "silver"}
    return docs, data, snap


def evaluate(query, mutate=None, required="", top_k=3):
    docs, data, snap = fixture()
    if mutate:
        mutate(docs, data)
    ev = matching.prepare_evidence(docs, data, "gold", "silver")
    rows = [{**{k: d[k] for k in matching.RESULT_COLUMNS}, "score": 1.0} for d in reversed(docs)]
    return matching.match_candidates(matching.query_plan(query, required), rows, ev, snap, top_k)


class MatchingPolicyTests(unittest.TestCase):
    def test_specific_evidence_overrides_retrieval_order(self):
        result = evaluate("Blockchain information security")
        self.assertEqual(result["status"], "candidates_for_review")
        self.assertEqual(result["candidates"][0]["researcher_id"], "zu:0")
        self.assertEqual(result["candidates"][0]["retrieval_rank"], 7)
        self.assertEqual(result["candidates"][0]["evidence"][0]["url"], "https://doi.org/10.1/0")

    def test_out_of_scope_high_retrieval_scores_abstain(self):
        result = evaluate("Exoplanet atmospheric spectroscopy")
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["candidates"], [])

    def test_broad_method_needs_refinement(self):
        for query in ("Machine learning", "AI and ML", "Higher education", "Learning and data analysis"):
            with self.subTest(query=query):
                self.assertEqual(evaluate(query)["status"], "needs_refinement")

    def test_unknown_application_not_rescued_by_generic_method(self):
        self.assertEqual(evaluate("Exoplanet detection using machine learning")["status"], "insufficient_evidence")

    def test_negation_needs_refinement_not_ignored(self):
        for query in ("Malware without forensics", "Exclude blockchain security", "Not brain tumor detection"):
            self.assertEqual(evaluate(query)["status"], "needs_refinement")

    def test_common_inflections_and_aliases(self):
        self.assertEqual(matching.evidence_terms("AI brain tumours and medical images"),
                         matching.evidence_terms("Artificial intelligence brain tumors and medical image"))
        self.assertEqual(matching.evidence_terms("digital forensics"), matching.evidence_terms("digital forensic"))

    def test_abstract_only_passing_mention_does_not_qualify(self):
        def mutate(docs, data):
            data["researcher_profile_publications"][0]["abstract_excerpt"] = "Exoplanet spectroscopy is unrelated to this study."
        self.assertEqual(evaluate("Exoplanet spectroscopy", mutate)["status"], "insufficient_evidence")

    def test_linked_topic_can_support_without_title_words(self):
        def mutate(docs, data):
            data["researcher_profile_publications"][0]["title"] = "A technical case study"
        result = evaluate("Blockchain security", mutate)
        ev = result["candidates"][0]["evidence"][0]
        self.assertEqual(ev["matched_in_title"], [])
        self.assertEqual(ev["matched_topics"][0]["topic_id"], "t0")
        self.assertEqual(ev["topic_only_anchor_terms"], ["blockchain"])

    def test_specific_title_precedes_broad_paper_with_passing_abstract_terms(self):
        def mutate(docs, data):
            pubs = data["researcher_profile_publications"]
            pubs[2]["title"] = "Brain tumor detection"
            data["researcher_profile_topics"][2].update(topic_name="Brain tumor detection", supporting_contribution_count=2)
            p = copy.deepcopy(pubs[2])
            p.update(row_key="extra", publication_evidence_id="extra", contribution_id="extra",
                     title="Breast cancer diagnosis", abstract_excerpt="Brain tumor medical image detection", publication_year=2026)
            pubs.append(p)
            e = copy.deepcopy(data["researcher_topic_evidence"][2])
            e.update(row_key="extra", publication_evidence_id="extra", contribution_id="extra")
            data["researcher_topic_evidence"].append(e)
            docs[2]["eligible_contribution_count"] = 2
        result = evaluate("Brain tumor medical image detection", mutate)
        self.assertEqual(result["candidates"][0]["evidence"][0]["title"], "Brain tumor detection")

    def test_separate_papers_cannot_manufacture_joint_evidence(self):
        def mutate(docs, data):
            p = copy.deepcopy(data["researcher_profile_publications"][1])
            p.update(row_key="extra", researcher_id="zu:0", publication_evidence_id="extra", contribution_id="extra")
            data["researcher_profile_publications"].append(p)
            docs[0]["eligible_contribution_count"] = 2
        self.assertEqual(evaluate("Blockchain malware", mutate)["status"], "insufficient_evidence")

    def test_required_terms_enforced_on_same_contribution(self):
        def mutate(docs, data):
            data["researcher_profile_publications"][0]["abstract_excerpt"] = "Career reflective perspectives"
        result = evaluate("Blockchain career reflective employability", mutate, required="employability")
        self.assertEqual(result["status"], "insufficient_evidence")

    def test_required_terms_must_be_in_topic(self):
        with self.assertRaisesRegex(ValueError, "already present"):
            evaluate("Blockchain security", required="malware")

    def test_partial_query_coverage_explicit(self):
        def mutate(docs, data):
            data["researcher_profile_publications"][0]["abstract_excerpt"] = "Reflective career"
        result = evaluate("Blockchain reflective career employability", mutate)
        first = result["candidates"][0]
        self.assertEqual(first["evidence_scope"], "partial_query_coverage")
        self.assertIn("employability", first["best_evidence_missing_terms"])

    def test_preprint_type_and_unknown_funding_preserved(self):
        def mutate(docs, data):
            data["researcher_profile_publications"][0].update(is_preprint=True, publication_type="preprint")
            docs[0]["eligible_preprint_count"] = 1
        row = evaluate("Blockchain security", mutate)["candidates"][0]
        self.assertTrue(row["preprint_evidence_present"])
        self.assertEqual(row["funding_status"], "not_established")
        self.assertEqual(row["qualifying_contribution_count"], 1)

    def test_retractions_aliases_duplicate_groups_fail(self):
        for flag in ("retracted", "concept_doi_alias"):
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                evaluate("Blockchain security", lambda docs, data: data["researcher_profile_publications"][0]["quality_flags"].append(flag))
        def duplicate(docs, data):
            p = copy.deepcopy(data["researcher_profile_publications"][0])
            p.update(row_key="different", publication_evidence_id="different")
            data["researcher_profile_publications"].append(p)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            evaluate("Blockchain security", duplicate)

    def test_snapshot_or_cross_person_link_fails(self):
        for field, value in (("silver_build_id", "stale"), ("researcher_id", "foreign")):
            with self.assertRaises(ValueError):
                evaluate("Blockchain security", lambda docs, data: data["researcher_profile_publications"][0].update({field: value}))
        with self.assertRaisesRegex(ValueError, "Orphan"):
            evaluate("Blockchain security", lambda docs, data: data["researcher_topic_evidence"][0].update(researcher_id="zu:1"))

    def test_incomplete_evidence_counts_rejected(self):
        with self.assertRaisesRegex(ValueError, "counts disagree"):
            evaluate("Blockchain security", lambda docs, data: docs[0].update(eligible_contribution_count=2))
        with self.assertRaisesRegex(ValueError, "support counts"):
            evaluate("Blockchain security", lambda docs, data: data["researcher_profile_topics"][0].update(supporting_contribution_count=2))

    def test_stale_candidate_not_accepted_even_for_negative_query(self):
        docs, data, snap = fixture()
        ev = matching.prepare_evidence(docs, data, "gold", "silver")
        rows = [{k: d[k] for k in matching.RESULT_COLUMNS} for d in docs]
        rows[0]["gold_build_id"] = "old"
        with self.assertRaisesRegex(ValueError, "stale"):
            matching.match_candidates(matching.query_plan("Astronomy spectroscopy"), rows, ev, snap)

    def test_raw_scores_do_not_change_evidence_ranking(self):
        docs, data, snap = fixture()
        ev = matching.prepare_evidence(docs, data, "gold", "silver")
        rows = [{k: d[k] for k in matching.RESULT_COLUMNS} for d in docs]
        one = matching.match_candidates(matching.query_plan("Malware forensic"), rows, ev, snap)
        for r in rows:
            r["score"] = 1000000
        two = matching.match_candidates(matching.query_plan("Malware forensic"), rows, ev, snap)
        self.assertEqual(one, two)

    def test_empty_retrieval_does_not_invent_candidates(self):
        docs, data, snap = fixture()
        ev = matching.prepare_evidence(docs, data, "gold", "silver")
        result = matching.match_candidates(matching.query_plan("Blockchain security"), [], ev, snap)
        self.assertEqual(result["status"], "insufficient_evidence")

    def test_query_bounds(self):
        for query in ("", "a", "x" * 501):
            with self.assertRaises(ValueError):
                matching.query_plan(query)

    def test_portable_read_only_helpers_match_notebook_12(self):
        source = ast.parse((ROOT / "notebooks/12_setup_and_test_ai_search.py").read_text())
        target = ast.parse((ROOT / "notebooks/13_match_researchers.py").read_text())
        old = {n.name: n for n in source.body if isinstance(n, ast.FunctionDef)}
        new = {n.name: n for n in target.body if isinstance(n, ast.FunctionDef)}
        # Notebook 13 stays frozen at the seven-profile pilot; 12 now takes the count from Gold.
        for name in old.keys() & new.keys() - {"run_notebook", "validate_documents", "source_snapshot"}:
            self.assertEqual(ast.dump(old[name]), ast.dump(new[name]), name)
        self.assertNotIn("write", {n.attr for n in ast.walk(target) if isinstance(n, ast.Attribute)})


class MatchingIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.docs, data, self.snap = fixture()
        self.ev = matching.prepare_evidence(self.docs, data, "gold", "silver")
        self.cfg = matching.config(endpoint="vs_endpoint_1", model="databricks-gte-large-en")

    def payload(self):
        cols = matching.RESULT_COLUMNS + ["score"]
        return {"manifest": {"columns": [{"name": c} for c in cols]},
                "result": {"data_array": [[d.get(c, 1.0) for c in cols] for d in self.docs]}}

    def test_live_adapter_bounded_filtered_and_query_not_sql(self):
        with patch.object(matching, "api_call", return_value=self.payload()) as call:
            result = matching.run_match(Mock(), self.cfg, self.snap, self.ev, "Blockchain security")
        self.assertEqual(result["status"], "candidates_for_review")
        body = call.call_args.kwargs["body"]
        self.assertEqual(body["num_results"], matching.MAX_PROFILES)
        self.assertEqual(json.loads(body["filters_json"]), {"gold_build_id": "gold", "is_searchable": True})

    def test_negative_and_refinement_skip_inference(self):
        for query in ("Exoplanet spectroscopy", "Machine learning", "Malware without forensic"):
            with patch.object(matching, "api_call") as api:
                self.assertEqual(matching.run_match(Mock(), self.cfg, self.snap, self.ev, query)["inference_queries"], 0)
                api.assert_not_called()

    def test_query_failure_not_suppressed_as_no_match(self):
        with patch.object(matching, "api_call", side_effect=RuntimeError("quota")), self.assertRaisesRegex(RuntimeError, "quota"):
            matching.run_match(Mock(), self.cfg, self.snap, self.ev, "Blockchain security")

    def test_malformed_extra_stale_response_fails(self):
        payload = self.payload()
        payload["result"]["data_array"][0][3] = "old"
        with patch.object(matching, "api_call", return_value=payload), self.assertRaises(ValueError):
            matching.retrieve_candidates(Mock(), self.cfg, self.snap, "Blockchain", "HYBRID")
        payload = self.payload()
        payload["next_page_token"] = "unexpected"
        with patch.object(matching, "api_call", return_value=payload), self.assertRaises(ValueError):
            matching.retrieve_candidates(Mock(), self.cfg, self.snap, "Blockchain", "HYBRID")

    def run_mode(self, mode="validate", changed=False):
        values = {"mode": mode, "retrieval_type": "HYBRID", "top_k": "3", "catalog": "workspace",
                  "silver_schema": "researchmatch_silver", "gold_schema": "researchmatch_gold",
                  "endpoint_name": "vs_endpoint_1", "embedding_model": "databricks-gte-large-en",
                  "research_topic": "Blockchain security", "required_terms": ""}
        widgets = SimpleNamespace(dropdown=Mock(), text=Mock(), get=values.__getitem__)
        spark = Mock()
        spark.sql.return_value.first.return_value = {"version": 2 if changed else 1}
        index = matching.index_spec(self.cfg, "databricks-gte-large-en")
        index["status"] = {"ready": True}
        del index["delta_sync_index_spec"]["columns_to_sync"]
        def api(w, method, path, **kwargs):
            if path.endswith("/vs_endpoint_1"):
                self.assertEqual(method, "GET")
                return {"endpoint_type": "STANDARD", "endpoint_status": {"state": "ONLINE"}}
            if path.endswith("/scan"):
                return scan_payload(self.docs)
            if path.endswith("/query"):
                return self.payload()
            self.assertEqual(method, "GET")
            return index
        with patch.object(matching, "source_snapshot", return_value=self.snap), \
                patch.object(matching, "load_evidence", return_value=(self.ev, {"researcher_profile_publications": 1})), \
                patch.object(matching, "api_call", side_effect=api) as calls, patch("builtins.print") as printed:
            result = matching.run_notebook(spark, SimpleNamespace(widgets=widgets), Mock())
            return result, calls, printed

    def test_default_validate_has_no_inference_no_resource_writes(self):
        result, calls, _ = self.run_mode()
        self.assertEqual(result["status"], "validation_passed")
        self.assertEqual([c.args[2].rsplit("/", 1)[-1] for c in calls.call_args_list][-1], "scan")
        self.assertEqual(len(calls.call_args_list), 3)

    def test_full_match_reuses_existing_index(self):
        result, calls, _ = self.run_mode("match")
        self.assertEqual(result["candidates"][0]["researcher_id"], "zu:0")
        self.assertEqual(len(calls.call_args_list), 4)

    def test_evaluation_is_compact_and_labels_pending(self):
        result, _, _ = self.run_mode("evaluate")
        self.assertEqual(len(result["checks"]), 12)
        self.assertEqual(result["review_status"], "human_labels_pending")
        self.assertNotIn("evidence", result["checks"][0])

    def test_evidence_version_change_blocks_result(self):
        with self.assertRaisesRegex(ValueError, "results withheld"):
            self.run_mode("match", changed=True)


if __name__ == "__main__":
    unittest.main()
