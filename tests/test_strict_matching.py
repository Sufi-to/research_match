"""Strict policy regressions and reuse of the existing read-only adapter tests."""
import ast
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import test_matching as original

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("strict_matching", ROOT / "notebooks/14_match_researchers_strict.py")
strict = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strict)


def add_publication(docs, data, index, **changes):
    source = data["researcher_profile_publications"][index]
    p = copy.deepcopy(source)
    ident = "extra-" + str(len(data["researcher_profile_publications"]))
    p.update(row_key=ident, publication_evidence_id=ident, contribution_id=ident,
             representative_work_id="oa:" + ident)
    p.update(changes)
    data["researcher_profile_publications"].append(p)
    docs[index]["eligible_contribution_count"] += 1
    docs[index]["eligible_preprint_count"] += int(p["is_preprint"])
    return p


class StrictPolicyTests(unittest.TestCase):
    def setUp(self):
        swap = patch.object(original, "matching", strict)
        swap.start()
        self.addCleanup(swap.stop)

    def test_full_direct_evidence_has_no_missing_terms(self):
        r = original.evaluate("Blockchain security")
        self.assertEqual(r["status"], "candidates_for_review")
        self.assertEqual(r["partial_candidates"], [])
        self.assertEqual(r["candidates"][0]["evidence_scope"], "direct_query_terms")
        self.assertEqual(r["candidates"][0]["best_evidence_missing_terms"], [])

    def test_missing_broad_term_is_partial_not_complete(self):
        r = original.evaluate("Blockchain information security")
        self.assertEqual(r["status"], "partial_evidence_only")
        self.assertEqual(r["candidates"], [])
        self.assertEqual(r["partial_candidates"][0]["best_evidence_missing_terms"], ["information"])

    def test_topic_label_cannot_create_candidate_or_corpus_coverage(self):
        def change(docs, data):
            data["researcher_profile_publications"][0]["title"] = "A technical case study"
        r = original.evaluate("Blockchain security", change)
        self.assertEqual(r["status"], "insufficient_evidence")

    def test_breast_paper_does_not_become_brain_evidence(self):
        def change(docs, data):
            # Keep "brain" somewhere in the corpus so the per-paper gate is tested.
            data["researcher_profile_publications"][0]["abstract_excerpt"] = "Brain"
            data["researcher_profile_publications"][2].update(
                title="A deep learning framework for automated breast cancer diagnosis",
                abstract_excerpt="Image detection and tumor classification")
        r = original.evaluate("Brain tumor medical image detection", change)
        self.assertEqual(r["candidates"] + r["partial_candidates"], [])

    def test_missing_forensic_subject_not_filled_by_label(self):
        def change(docs, data):
            data["researcher_profile_publications"][0]["abstract_excerpt"] = "Forensic"
            data["researcher_profile_publications"][1].update(title="Fast and efficient malware detection", abstract_excerpt="")
        r = original.evaluate("Digital forensic malware detection", change)
        self.assertEqual(r["candidates"] + r["partial_candidates"], [])

    def test_speed_estimation_not_autonomous_prediction_evidence(self):
        def change(docs, data):
            data["researcher_profile_publications"][0]["abstract_excerpt"] = "Autonomous transportation"
            data["researcher_profile_publications"][4].update(
                title="Real Time Vehicle Speed Estimation", abstract_excerpt="Traffic vehicle detection")
        r = original.evaluate("Traffic prediction transportation planning autonomous vehicles", change)
        self.assertEqual(r["candidates"] + r["partial_candidates"], [])

    def test_topic_only_broad_terms_remain_missing(self):
        def change(docs, data):
            data["researcher_profile_publications"][4]["title"] = "Autonomous vehicle traffic transportation"
        r = original.evaluate("Autonomous vehicle traffic transportation planning", change)
        ev = r["partial_candidates"][0]["evidence"][0]
        self.assertEqual(ev["missing_query_terms"], ["planning"])
        self.assertEqual(ev["topic_only_query_terms"], ["planning"])
        self.assertIn("topic_context_not_used_for_scoring", ev)

    def test_required_term_needs_direct_evidence_not_topic(self):
        def change(docs, data):
            data["researcher_profile_publications"][0]["abstract_excerpt"] = "Planning"
            data["researcher_profile_publications"][4]["title"] = "Autonomous vehicle traffic transportation"
        r = original.evaluate("Autonomous vehicle traffic transportation planning", change, required="planning")
        self.assertEqual(r["candidates"] + r["partial_candidates"], [])

    def test_abstract_can_complete_title_subject_evidence(self):
        def change(docs, data):
            data["researcher_profile_publications"][1].update(
                title="Malware detection", abstract_excerpt="Digital forensic methods are evaluated.")
        r = original.evaluate("Digital forensic malware detection", change)
        self.assertEqual(r["candidates"][0]["evidence"][0]["matched_in_abstract"], ["digital", "forensic"])

    def test_no_cross_paper_completion(self):
        def change(docs, data):
            data["researcher_profile_publications"][1]["title"] = "Malware detection"
            add_publication(docs, data, 1, title="Digital forensic investigation")
        r = original.evaluate("Digital forensic malware detection", change)
        self.assertEqual(r["candidates"] + r["partial_candidates"], [])

    def test_same_title_discount_preserves_both_records(self):
        def change(docs, data):
            add_publication(docs, data, 1, publication_type="preprint", is_preprint=True)
        r = original.evaluate("Digital forensic malware detection", change)
        c = r["candidates"][0]
        self.assertEqual(c["qualifying_source_contribution_count"], 2)
        self.assertEqual(c["support_group_count_for_ranking"], 1)
        self.assertEqual(c["suspected_duplicate_records_discounted"], 1)
        self.assertEqual(len(c["evidence"]), 1)
        ev = c["evidence"][0]
        self.assertFalse(ev["is_preprint"])
        self.assertTrue(c["possible_versions_include_preprint"])
        self.assertEqual(len(ev["possible_versions"]), 2)
        self.assertTrue(any(x["is_preprint"] for x in ev["possible_versions"]))
        self.assertEqual(ev["version_relationship_status"], "unverified_same_title")

    def test_same_title_does_not_increase_score(self):
        before = original.evaluate("Digital forensic malware detection")["candidates"][0]
        after = original.evaluate("Digital forensic malware detection",
                                  lambda docs, data: add_publication(docs, data, 1))["candidates"][0]
        self.assertEqual(before["relevance_score_heuristic"], after["relevance_score_heuristic"])

    def test_same_title_cannot_union_abstracts(self):
        def change(docs, data):
            data["researcher_profile_publications"][1].update(title="Malware detection", abstract_excerpt="Digital")
            add_publication(docs, data, 1, abstract_excerpt="Forensic")
        r = original.evaluate("Digital forensic malware detection", change)
        self.assertEqual(r["candidates"], [])
        self.assertEqual(r["partial_candidates"][0]["best_evidence_missing_terms"], ["digital"])

    def test_full_tier_never_includes_partial_evidence_or_counts(self):
        def change(docs, data):
            add_publication(docs, data, 1, title="Malware forensic detection")
        c = original.evaluate("Digital forensic malware detection", change)["candidates"][0]
        self.assertEqual(c["qualifying_source_contribution_count"], 1)
        self.assertTrue(all(e["evidence_scope"] == "direct_query_terms" for e in c["evidence"]))

    def test_partial_and_full_people_are_separate(self):
        def change(docs, data):
            data["researcher_profile_publications"][0]["title"] = "Blockchain information security"
            data["researcher_profile_publications"][1]["title"] = "Blockchain security"
        r = original.evaluate("Blockchain information security", change)
        self.assertEqual([c["researcher_id"] for c in r["candidates"]], ["zu:0"])
        self.assertEqual([c["researcher_id"] for c in r["partial_candidates"]], ["zu:1"])

    def test_normalized_titles_only_not_fuzzy_or_cross_person_merge(self):
        self.assertEqual(strict.title_key("Same: TITLE!"), strict.title_key("same title"))
        self.assertNotEqual(strict.title_key("Trial 1"), strict.title_key("Trial 2"))
        docs, data, _ = original.fixture()
        data["researcher_profile_publications"][1]["title"] = data["researcher_profile_publications"][0]["title"]
        before = copy.deepcopy(data)
        ev = strict.prepare_evidence(docs, data, "gold", "silver")
        self.assertEqual(ev["possible_version_groups"], [])
        self.assertEqual(data, before)

    def test_topic_labels_cannot_influence_scores(self):
        before = original.evaluate("Blockchain security")["candidates"][0]
        def change(docs, data):
            data["researcher_profile_topics"][0]["topic_name"] = "Unrelated astronomy"
        after = original.evaluate("Blockchain security", change)["candidates"][0]
        self.assertEqual(before["relevance_score_heuristic"], after["relevance_score_heuristic"])

    def test_preprints_remain_labelled(self):
        def change(docs, data):
            data["researcher_profile_publications"][0].update(publication_type="preprint", is_preprint=True)
            docs[0]["eligible_preprint_count"] = 1
        c = original.evaluate("Blockchain security", change)["candidates"][0]
        self.assertTrue(c["preprint_evidence_present"])
        self.assertEqual(c["funding_status"], "not_established")

    def test_unchanged_helpers_and_standalone_import(self):
        old = ast.parse((ROOT / "notebooks/13_match_researchers.py").read_text())
        new = ast.parse((ROOT / "notebooks/14_match_researchers_strict.py").read_text())
        funcs = lambda tree: {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        a, b = funcs(old), funcs(new)
        changed = {"prepare_evidence", "screen_query", "contribution_match", "match_candidates", "run_notebook",
                   "validate_documents", "source_snapshot"}  # 14 takes the profile count from Gold
        for name in a.keys() & b.keys() - changed:
            self.assertEqual(ast.dump(a[name]), ast.dump(b[name]), name)
        self.assertNotIn("write", {n.attr for n in ast.walk(new) if isinstance(n, ast.Attribute)})
        self.assertNotIn("%run", (ROOT / "notebooks/14_match_researchers_strict.py").read_text())

    # Reuse the same safety regressions with the strict module substituted.
    test_retractions_aliases_duplicate_groups_fail = original.MatchingPolicyTests.test_retractions_aliases_duplicate_groups_fail
    test_snapshot_or_cross_person_link_fails = original.MatchingPolicyTests.test_snapshot_or_cross_person_link_fails
    test_incomplete_evidence_counts_rejected = original.MatchingPolicyTests.test_incomplete_evidence_counts_rejected
    test_stale_candidate_not_accepted_even_for_negative_query = original.MatchingPolicyTests.test_stale_candidate_not_accepted_even_for_negative_query
    test_raw_scores_do_not_change_evidence_ranking = original.MatchingPolicyTests.test_raw_scores_do_not_change_evidence_ranking
    test_query_bounds = original.MatchingPolicyTests.test_query_bounds
    test_abstract_only_passing_mention_does_not_qualify = original.MatchingPolicyTests.test_abstract_only_passing_mention_does_not_qualify
    test_out_of_scope_high_retrieval_scores_abstain = original.MatchingPolicyTests.test_out_of_scope_high_retrieval_scores_abstain
    test_negation_needs_refinement_not_ignored = original.MatchingPolicyTests.test_negation_needs_refinement_not_ignored


class StrictIntegrationTests(original.MatchingIntegrationTests):
    def setUp(self):
        swap = patch.object(original, "matching", strict)
        swap.start()
        self.addCleanup(swap.stop)
        super().setUp()

    def test_validate_includes_version_review_without_mutation(self):
        result, calls, _ = self.run_mode()
        self.assertIn("possible_version_groups", result)
        self.assertEqual(result["policy_version"], "matching_v2_direct_evidence")
        self.assertEqual(len(calls.call_args_list), 3)

    def test_evaluation_reports_partial_separately(self):
        result, _, _ = self.run_mode("evaluate")
        self.assertTrue(all("partial_candidates" in c for c in result["checks"]))


if __name__ == "__main__":
    unittest.main()
