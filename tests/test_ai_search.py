import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("search", ROOT / "notebooks/12_setup_and_test_ai_search.py")
search = importlib.util.module_from_spec(spec)
spec.loader.exec_module(search)


def documents():
    rows = []
    for n in range(7):
        text = "Evidence-backed researcher profile " + str(n)
        row = {c: "value" for c in search.SYNC_COLUMNS}
        row.update(document_id=f"researcher:zu:{n}", researcher_id=f"zu:{n}", display_name=f"Researcher {n}",
                   search_text=text, search_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                   gold_build_id="gold", silver_build_id="silver", eligible_contribution_count=10,
                   eligible_preprint_count=1, affiliation_status="directory_observed",
                   funding_status="not_established", is_searchable=True)
        rows.append(row)
    return rows


def inventory(index=True, state="ONLINE"):
    cfg = search.config()
    endpoint = {"name": cfg["endpoint"], "endpoint_type": "STANDARD", "endpoint_status": {"state": state}}
    idx = search.index_spec(cfg, "databricks-gte-large-en") if index else None
    if idx:
        idx["status"] = {"ready": True, "indexed_row_count": 7}
    return {"databricks-gte-large-en"}, [endpoint], idx


def scan_payload(rows):
    def value(v):
        k = "bool_value" if type(v) is bool else "number_value" if type(v) in (int, float) else "string_value"
        return {k: v}
    return {"data": [{"fields": [{"key": k, "value": value(v)} for k, v in d.items()] +
                                [{"key": "search_text_vector", "value": {"list_value": {"values": []}}}]}
                     for d in rows], "last_primary_key": rows[-1]["document_id"] if rows else None}


def query_payload(rows):
    cols = list(reversed(search.RESULT_COLUMNS)) + ["score"]
    return {"manifest": {"columns": [{"name": c} for c in cols]},
            "result": {"data_array": [[d.get(c, .12) for c in cols] for d in rows]}}


class SearchPureTests(unittest.TestCase):
    def test_configuration_and_model_policy(self):
        self.assertEqual(search.config()["source"], "workspace.researchmatch_gold.researcher_search_documents")
        for kwargs in ({"catalog": "x;DROP"}, {"endpoint": "../other"}, {"model": "databricks-bge-large-en"},
                       {"gold": "researchmatch_silver"}):
            with self.assertRaises(ValueError):
                search.config(**kwargs)

    def test_model_selection_prefers_gte_and_uses_qwen_fallback(self):
        self.assertEqual(search.choose_model("auto", set(search.MODELS)), "databricks-gte-large-en")
        self.assertEqual(search.choose_model("auto", {"databricks-qwen3-embedding-0-6b"}), "databricks-qwen3-embedding-0-6b")
        with self.assertRaises(ValueError):
            search.choose_model("auto", {"databricks-bge-large-en"})

    def test_existing_model_is_sticky_no_auto_migration(self):
        idx = search.index_spec(search.config(), "databricks-qwen3-embedding-0-6b")
        self.assertEqual(search.choose_model("auto", set(search.MODELS), idx), "databricks-qwen3-embedding-0-6b")
        with self.assertRaisesRegex(ValueError, "another model"):
            search.choose_model("databricks-gte-large-en", set(search.MODELS), idx)

    def test_full_document_contract(self):
        search.validate_documents(documents(), "gold", "silver")
        for field, wrong in (("gold_build_id", "old"), ("is_searchable", False), ("funding_status", "zero"),
                             ("search_text_sha256", "bad"), ("document_id", "another"), ("search_text", "x" * 6001)):
            rows = documents()
            rows[0][field] = wrong
            with self.assertRaises(ValueError):
                search.validate_documents(rows, "gold", "silver")

    def test_extra_missing_duplicate_profiles_fail(self):
        for rows in (documents()[:-1], documents() + [documents()[0]], [documents()[0]] * 7):
            with self.assertRaises(ValueError):
                search.validate_documents(rows, "gold", "silver")

    def test_index_contract_no_direct_continuous_or_other_source(self):
        cfg = search.config()
        idx = search.index_spec(cfg, "databricks-gte-large-en")
        search.validate_index(idx, cfg, "databricks-gte-large-en")
        for key, value in (("source_table", "other.data"), ("pipeline_type", "CONTINUOUS"), ("columns_to_sync", [])):
            wrong = copy.deepcopy(idx)
            wrong["delta_sync_index_spec"][key] = value
            with self.assertRaises(ValueError):
                search.validate_index(wrong, cfg, "databricks-gte-large-en")
        idx["index_type"] = "DIRECT_ACCESS"
        with self.assertRaises(ValueError):
            search.validate_index(idx, cfg, "databricks-gte-large-en")

    def test_query_model_change_rejected(self):
        cfg = search.config()
        idx = search.index_spec(cfg, "databricks-gte-large-en")
        idx["delta_sync_index_spec"]["embedding_source_columns"][0]["model_endpoint_name_for_query"] = "other-model"
        with self.assertRaises(ValueError):
            search.validate_index(idx, cfg, "databricks-gte-large-en")

    def test_unrelated_or_multiple_endpoints_never_reused_silently(self):
        _, eps, _ = inventory()
        self.assertIsNone(search.validate_endpoint([], search.config()))
        for rows in (eps * 2, [{**eps[0], "name": "someone-elses"}], [{**eps[0], "endpoint_type": "STORAGE_OPTIMIZED"}]):
            with self.assertRaises(ValueError):
                search.validate_endpoint(rows, search.config())

    def test_ready_state_does_not_imply_content_freshness(self):
        rows = documents()
        rows[0]["search_text"] = "stale text"
        with patch.object(search, "api_call", return_value=scan_payload(rows)):
            with self.assertRaisesRegex(ValueError, "stale"):
                search.scan_matches(Mock(), search.config(), documents())

    def test_scan_validates_all_synced_columns_and_ignores_vector(self):
        with patch.object(search, "api_call", return_value=scan_payload(documents())):
            self.assertTrue(search.scan_matches(Mock(), search.config(), documents()))
        bad = scan_payload(documents())
        bad["data"][0]["fields"] = [f for f in bad["data"][0]["fields"] if f["key"] != "gold_build_id"]
        with patch.object(search, "api_call", return_value=bad), self.assertRaises(ValueError):
            search.scan_matches(Mock(), search.config(), documents())

    def test_scan_extra_duplicate_or_missing_rows_fail(self):
        for rows in (documents()[:-1], documents() + [documents()[0]], [documents()[0]] * 7):
            with patch.object(search, "api_call", return_value=scan_payload(rows)), self.assertRaises(ValueError):
                search.scan_matches(Mock(), search.config(), documents())

    def test_query_uses_manifest_not_assumed_column_order(self):
        rows = search.parse_results(query_payload(documents()[:3]))
        search.validate_results(rows, documents())
        self.assertEqual(rows[0]["display_name"], "Researcher 0")
        self.assertEqual(rows[0]["score"], .12)

    def test_query_stale_foreign_and_duplicate_results_fail(self):
        for field, value in (("gold_build_id", "old"), ("researcher_id", "foreign"), ("search_text_sha256", "old")):
            rows = search.parse_results(query_payload(documents()[:3]))
            rows[0][field] = value
            with self.assertRaises(ValueError):
                search.validate_results(rows, documents())
        rows = search.parse_results(query_payload([documents()[0]] * 2))
        with self.assertRaises(ValueError):
            search.validate_results(rows, documents())

    def test_bad_query_manifest_or_shape_fails(self):
        with self.assertRaises(ValueError):
            search.parse_results({})
        payload = query_payload(documents()[:1])
        payload["result"]["data_array"][0].pop()
        with self.assertRaises(ValueError):
            search.parse_results(payload)

    def test_probe_checks_actual_full_text_without_exposing_vectors(self):
        with patch.object(search, "api_call", return_value={"data": [{"embedding": [0.1] * 1024}],
                                                          "usage": {"prompt_tokens": 10}}) as call:
            result = search.probe_model(Mock(), "databricks-gte-large-en", documents())
            self.assertEqual(len(result), 7)
            self.assertEqual(call.call_count, 7)
            self.assertEqual(call.call_args.kwargs["body"]["input"], [documents()[-1]["search_text"]])

    def test_bad_embedding_or_overlength_usage_fails(self):
        for payload in ({"data": []}, {"data": [{"embedding": [0.] * 1024}]},
                        {"data": [{"embedding": [float("nan")] * 1024}]},
                        {"data": [{"embedding": [.1] * 1024}], "usage": {"prompt_tokens": 8192}}):
            with patch.object(search, "api_call", return_value=payload), self.assertRaises(ValueError):
                search.probe_model(Mock(), "databricks-gte-large-en", documents())

    def test_not_found_is_distinct_from_permissions_or_quota(self):
        for code in ("PERMISSION_DENIED", "RESOURCE_EXHAUSTED", None):
            error = RuntimeError("failure")
            error.error_code = code
            with patch.object(search, "api_call", side_effect=error), self.assertRaises(RuntimeError):
                search.optional_get(Mock(), "/index")
        error.error_code = "RESOURCE_DOES_NOT_EXIST"
        with patch.object(search, "api_call", side_effect=error):
            self.assertIsNone(search.optional_get(Mock(), "/index"))

    def test_inventory_pagination(self):
        with patch.object(search, "api_call", side_effect=[{"endpoints": [{"name": "a"}], "next_page_token": "next"},
                                                         {"endpoints": [{"name": "b"}]}]):
            self.assertEqual(len(search.pages(Mock(), "/path", "endpoints")), 2)

    def test_evidence_no_overlap_no_invented_explanation(self):
        p = {"researcher_id": "r", "publication_evidence_id": "p", "title": "Malware detection",
             "abstract_excerpt": "", "publication_year": 2025, "evidence_url": "https://doi.org/example",
             "publication_type": "preprint", "is_preprint": True}
        self.assertEqual(search.evidence_preview("Exoplanet atmosphere", "r", [p]), [])
        self.assertTrue(search.evidence_preview("Malware detection", "r", [p])[0]["is_preprint"])

    def test_queries_are_filtered_and_negative_case_not_accuracy_score(self):
        snap = {"gold_build_id": "gold", "silver_build_id": "silver", "documents": documents()}
        with patch.object(search, "api_call", return_value=query_payload(documents()[:3])) as call:
            result = search.test_searches(Mock(), search.config(), snap, [])
        self.assertEqual(call.call_count, 16)
        self.assertEqual(json.loads(call.call_args.kwargs["body"]["filters_json"]), {"gold_build_id": "gold", "is_searchable": True})
        self.assertEqual({c["query_type"] for c in result["checks"]}, {"ANN", "HYBRID"})
        self.assertIsNone(result["checks"][-1]["hypothesis_hit_at_3"])


class SearchNotebookTests(unittest.TestCase):
    def setUp(self):
        self.snap = {"gold_build_id": "gold", "silver_build_id": "silver", "source_version": 1,
                     "identity_version": 1, "documents": documents()}

    def run_mode(self, mode, resources=None, mutate=None):
        widgets = {"mode": mode, "catalog": "workspace", "silver_schema": "researchmatch_silver",
                   "gold_schema": "researchmatch_gold", "endpoint_name": "researchmatch-search", "embedding_model": "auto"}
        dbutils = SimpleNamespace(widgets=SimpleNamespace(dropdown=Mock(), text=Mock(), get=widgets.__getitem__))
        with patch.object(search, "source_snapshot", return_value=self.snap), \
                patch.object(search, "discover", return_value=resources if resources is not None else inventory()), \
                patch.object(search, "probe_model") as probe, patch.object(search, "scan_matches") as scan, \
                patch.object(search, "assert_unchanged", side_effect=mutate) as unchanged, \
                patch.object(search, "api_call") as api:
            result = search.run_notebook(Mock(), dbutils, Mock())
            return result, api, probe, scan, unchanged

    def test_discover_is_no_inference_no_mutation(self):
        result, api, probe, scan, _ = self.run_mode("discover")
        self.assertEqual(result["mode"], "discover")
        api.assert_not_called()
        probe.assert_not_called()
        scan.assert_not_called()

    def test_create_only_one_standard_endpoint_first(self):
        result, api, probe, _, _ = self.run_mode("create", ({"databricks-gte-large-en"}, [], None))
        self.assertEqual(result["state"], "endpoint_creation_submitted")
        self.assertEqual(api.call_count, 1)
        self.assertEqual(api.call_args.kwargs["body"], {"name": "researchmatch-search", "endpoint_type": "STANDARD"})
        probe.assert_called_once()

    def test_pending_endpoint_no_writes_or_inference(self):
        result, api, probe, _, _ = self.run_mode("create", inventory(index=False, state="PROVISIONING"))
        self.assertEqual(result["state"], "endpoint_pending")
        api.assert_not_called()
        probe.assert_not_called()

    def test_online_endpoint_creates_exact_triggered_index(self):
        result, api, probe, _, _ = self.run_mode("create", inventory(index=False))
        self.assertEqual(result["state"], "index_creation_submitted")
        self.assertEqual(api.call_args.kwargs["body"], search.index_spec(search.config(), "databricks-gte-large-en"))
        probe.assert_called_once()

    def test_existing_matching_index_create_is_non_mutating(self):
        result, api, probe, scan, _ = self.run_mode("create")
        self.assertEqual(result["state"], "index_verified")
        api.assert_not_called()
        probe.assert_not_called()
        scan.assert_called_once()

    def test_pending_index_not_claimed_verified(self):
        names, eps, idx = inventory()
        idx["status"]["ready"] = False
        result, api, _, scan, _ = self.run_mode("status", (names, eps, idx))
        self.assertEqual(result["state"], "index_pending")
        api.assert_not_called()
        scan.assert_not_called()

    def test_explicit_sync_once_only(self):
        result, api, probe, _, _ = self.run_mode("sync")
        self.assertEqual(result["state"], "sync_submitted")
        self.assertEqual(api.call_count, 1)
        self.assertTrue(api.call_args.args[2].endswith("/sync"))
        probe.assert_not_called()

    def test_changed_source_blocks_create(self):
        with self.assertRaisesRegex(ValueError, "changed"):
            self.run_mode("create", inventory(index=False), ValueError("changed"))

    def test_assert_unchanged_ignores_row_order_not_version(self):
        reordered = copy.deepcopy(self.snap)
        reordered["documents"].reverse()
        with patch.object(search, "source_snapshot", return_value=reordered):
            search.assert_unchanged(Mock(), search.config(), self.snap)
        reordered["source_version"] = 2
        with patch.object(search, "source_snapshot", return_value=reordered), self.assertRaises(ValueError):
            search.assert_unchanged(Mock(), search.config(), self.snap)


if __name__ == "__main__":
    unittest.main()
