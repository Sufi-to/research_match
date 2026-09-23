import ast
import copy
import importlib.util
import json
import re
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from researchmatch import matching_policy as policy, service
from researchmatch.presentation import safe_url
from researchmatch.warehouse import Warehouse, WarehouseError, decode
from test_matching import fixture
from test_ai_search import scan_payload


def payload(values=None, kinds=None):
    values = [["2", "true", '["x"]', '{"k":"v"}']] if values is None else values
    kinds = ["LONG", "BOOLEAN", "ARRAY", "MAP"] if kinds is None else kinds
    return {"statement_id": "12345678-1234-1234-1234-123456789012", "status": {"state": "SUCCEEDED"},
            "manifest": {"format": "JSON_ARRAY", "truncated": False, "total_row_count": len(values),
                         "total_chunk_count": 1,
                         "schema": {"columns": [{"name": f"c{n}", "type_name": k} for n, k in enumerate(kinds)]}},
            "result": {"chunk_index": 0, "row_offset": 0, "data_array": values}}


class WarehouseTests(unittest.TestCase):
    def setUp(self):
        self.w = Mock()
        self.reader = Warehouse(self.w, "test-warehouse", policy.config())
        self.table = "`workspace`.`researchmatch_gold`.`researcher_search_documents`"
        self.w.api_client.do.return_value = payload()

    def test_typed_bounded_read(self):
        rows = self.reader.sql("SELECT * FROM " + self.table).limit(8).collect()
        self.assertEqual(rows[0].asDict(), {"c0": 2, "c1": True, "c2": ["x"], "c3": {"k": "v"}})
        body = self.w.api_client.do.call_args.kwargs["body"]
        self.assertTrue(body["statement"].endswith("LIMIT 8"))
        self.assertEqual(body["on_wait_timeout"], "CANCEL")
        self.assertEqual(body["disposition"], "INLINE")

    def test_no_user_sql_or_unknown_tables(self):
        for q in ("DELETE FROM " + self.table, "SELECT * FROM other", "SELECT * FROM " + self.table + "; DROP TABLE x",
                  "SELECT read_files('https://example.com') FROM " + self.table,
                  "DESCRIBE HISTORY " + self.table):
            with self.subTest(q=q), self.assertRaises(WarehouseError):
                self.reader.sql(q).limit(2).collect()
        self.w.api_client.do.assert_not_called()

    def test_unbounded_select_refused(self):
        with self.assertRaises(WarehouseError):
            self.reader.sql("SELECT * FROM " + self.table).collect()

    def test_failed_pending_canceled_never_success(self):
        for state in ("PENDING", "FAILED", "CANCELED"):
            self.w.api_client.do.return_value = {"status": {"state": state, "error": {"message": "SECRET"}}}
            with self.assertRaises(WarehouseError) as ctx:
                self.reader.sql("SELECT * FROM " + self.table).limit(2).collect()
            self.assertNotIn("SECRET", str(ctx.exception))

    def test_truncated_oversized_and_bad_shape_fail(self):
        for update in ({"truncated": True}, {"total_row_count": 3}, {"total_chunk_count": 99}):
            p = payload()
            p["manifest"].update(update)
            self.w.api_client.do.return_value = p
            with self.assertRaises(WarehouseError):
                self.reader.sql("SELECT * FROM " + self.table).limit(2).collect()

    def test_chunks_construct_path_not_external_url(self):
        p = payload([["one"]], ["STRING"])
        p["manifest"].update(total_row_count=2, total_chunk_count=2)
        p["result"]["next_chunk_internal_link"] = "https://evil.test"
        self.w.api_client.do.side_effect = [p, {"chunk_index": 1, "row_offset": 1, "data_array": [["two"]]}]
        rows = self.reader.sql("SELECT * FROM " + self.table).limit(3).collect()
        self.assertEqual([r["c0"] for r in rows], ["one", "two"])
        self.assertEqual(self.w.api_client.do.call_args.kwargs["path"],
                         "/api/2.0/sql/statements/12345678-1234-1234-1234-123456789012/result/chunks/1")

    def test_external_result_links_rejected(self):
        p = payload()
        p["result"]["external_links"] = [{"external_link": "https://secret.example"}]
        self.w.api_client.do.return_value = p
        with self.assertRaises(WarehouseError):
            self.reader.sql("SELECT * FROM " + self.table).limit(2).collect()

    def test_wrong_chunk_offset_and_row_count_fail(self):
        for update in ({"row_offset": 1}, {"data_array": []}, {"data_array": [["one"]]}):
            p = payload()
            p["result"].update(update)
            self.w.api_client.do.return_value = p
            with self.assertRaises(WarehouseError):
                self.reader.sql("SELECT * FROM " + self.table).limit(2).collect()

    def test_metadata_and_version_pinning_allowed(self):
        self.reader.sql("DESCRIBE DETAIL " + self.table).first()
        self.reader.sql("DESCRIBE HISTORY " + self.table + " LIMIT 1").first()
        self.reader.sql("SELECT document_id, researcher_id FROM " + self.table +
                        " VERSION AS OF 2 WHERE build_id = '12345678-1234-1234-1234-123456789012'").limit(8).collect()

    def test_invalid_types_refused(self):
        for value, kind in (("bad", "BOOLEAN"), ("[]", "MAP"), ("NaN", "DOUBLE"), ("a", "BINARY")):
            with self.assertRaises((ValueError, WarehouseError)):
                decode(value, {"type_name": kind})


class AppContractTests(unittest.TestCase):
    def test_policy_matches_notebook_14(self):
        original = ast.parse((ROOT / "notebooks/14_match_researchers_strict.py").read_text())
        mirrored = ast.parse((ROOT / "app/researchmatch/matching_policy.py").read_text())
        def elements(tree):
            return [ast.dump(n) for n in tree.body if isinstance(n, (ast.FunctionDef, ast.Assign, ast.Import, ast.ImportFrom))
                    and not (isinstance(n, ast.FunctionDef) and n.name == "run_notebook")]
        self.assertEqual(elements(original), elements(mirrored))

    def test_safe_links(self):
        for url in ("https://doi.org/10.1/abc", "https://openalex.org/W1", "https://www.zu.ac.ae/main"):
            self.assertEqual(safe_url(url), url)
        for url in ("javascript:alert(1)", "https://doi.org.evil.test", "https://user:pass@doi.org", "//doi.org",
                    "http://doi.org/x", "https://doi.org:123/x", "https://doi.org/\nfoo", None):
            self.assertIsNone(safe_url(url))

    def test_invalid_query_fails_before_remote_calls(self):
        w = Mock()
        with self.assertRaises(ValueError):
            service.execute("", workspace=w)
        w.api_client.do.assert_not_called()

    def run_service(self, query=None, changed=False):
        docs, data, snap = fixture()
        ev = policy.prepare_evidence(docs, data, "gold", "silver")
        cfg = policy.config(endpoint="vs_endpoint_1", model="databricks-gte-large-en")
        index = policy.index_spec(cfg, "databricks-gte-large-en")
        index["status"] = {"ready": True}
        del index["delta_sync_index_spec"]["columns_to_sync"]
        def api(w, method, path, **kwargs):
            if "/endpoints/" in path:
                return {"endpoint_type": "STANDARD", "endpoint_status": {"state": "ONLINE"}}
            if path.endswith("/scan"):
                return scan_payload(docs)
            if path.endswith("/query"):
                return {"manifest": {"columns": [{"name": c} for c in policy.RESULT_COLUMNS]},
                        "result": {"data_array": [[d[c] for c in policy.RESULT_COLUMNS] for d in docs]}}
            return index
        with patch.object(service, "Warehouse") as reader, \
                patch.object(policy, "source_snapshot", return_value=snap), \
                patch.object(policy, "load_evidence", return_value=(ev, {"researcher_profile_publications": 1})), \
                patch.object(policy, "api_call", side_effect=api) as calls:
            reader.return_value.sql.return_value.first.return_value = {"version": 2 if changed else 1}
            return service.execute(query, workspace=Mock(), environ={"DATABRICKS_WAREHOUSE_ID": "id"}), calls

    def test_service_validate_no_inference(self):
        result, calls = self.run_service()
        self.assertEqual(result["status"], "validation_passed")
        self.assertEqual(result["researchers"], 7)
        self.assertEqual(len(calls.call_args_list), 3)

    def test_service_match_partial_and_full(self):
        full, _ = self.run_service("Blockchain security")
        part, _ = self.run_service("Blockchain information security")
        self.assertEqual(full["status"], "candidates_for_review")
        self.assertEqual(part["status"], "partial_evidence_only")
        self.assertEqual(part["candidates"], [])

    def test_service_changed_evidence_withholds_result(self):
        with self.assertRaisesRegex(ValueError, "withheld"):
            self.run_service("Blockchain security", changed=True)

    def test_service_end_to_end_through_real_sql_adapter(self):
        docs, data, _ = fixture()
        gid = "9c1571ed-a5fb-45e3-8f5f-b066801e7cd5"
        sid = "3230bb0f-28c7-4830-90b5-5a21034794cb"
        for d in docs:
            d.update(gold_build_id=gid, silver_build_id=sid)
        for rows in data.values():
            for r in rows:
                r.update(build_id=gid, silver_build_id=sid)
        cfg = policy.config(endpoint="vs_endpoint_1", model="databricks-gte-large-en")
        index = policy.index_spec(cfg, "databricks-gte-large-en")
        index["status"] = {"ready": True}

        def sql_payload(rows):
            names = list(rows[0])
            def kind(v):
                return "BOOLEAN" if type(v) is bool else "LONG" if type(v) is int else "ARRAY" if isinstance(v, list) else "MAP" if isinstance(v, dict) else "STRING"
            p = payload()
            p["manifest"].update(total_row_count=len(rows), schema={"columns": [
                {"name": n, "type_name": kind(rows[0][n])} for n in names]})
            def cell(v):
                return json.dumps(v) if isinstance(v, (list, dict, bool)) else str(v) if v is not None else None
            p["result"]["data_array"] = [[cell(r[n]) for n in names] for r in rows]
            return p

        seen_queries = []
        def api(**kwargs):
            path = kwargs["path"]
            if path == "/api/2.0/sql/statements":
                sql = kwargs["body"]["statement"]
                seen_queries.append(sql)
                target = re.findall(r"`([a-z_]+)`", sql)[-1]
                if sql.startswith("DESCRIBE HISTORY"):
                    return sql_payload([{"version": 3 if target == "identity_decisions" else 2}])
                if sql.startswith("DESCRIBE DETAIL"):
                    return sql_payload([{"format": "delta", "properties": {"delta.enableChangeDataFeed": "true"}}])
                if target == "current_build":
                    gold = "researchmatch_gold" in sql
                    return sql_payload([{"build_id": gid if gold else sid, "silver_build_id": sid,
                                         "status": "validated", "transform_version": "gold_v1_verified_contributions" if gold else "silver_v2_concept_versions",
                                         "source_versions_json": '{"identity_decisions": 3}'}])
                return sql_payload(docs if target == "researcher_search_documents" else data[target])
            if "/endpoints/" in path:
                return {"endpoint_type": "STANDARD", "endpoint_status": {"state": "ONLINE"}}
            if path.endswith("/scan"):
                return scan_payload(docs)
            if path.endswith("/query"):
                return {"manifest": {"columns": [{"name": c} for c in policy.RESULT_COLUMNS]},
                        "result": {"data_array": [[d[c] for c in policy.RESULT_COLUMNS] for d in docs]}}
            return index
        workspace = Mock()
        workspace.api_client.do.side_effect = api
        result = service.execute("Blockchain security", workspace=workspace,
                                 environ={"DATABRICKS_WAREHOUSE_ID": "test"})
        self.assertEqual(result["status"], "candidates_for_review")
        self.assertTrue(any("VERSION AS OF 2" in s for s in seen_queries))
        self.assertTrue(all("Blockchain" not in s for s in seen_queries))


try:
    from streamlit.testing.v1 import AppTest
except ImportError:
    AppTest = None


@unittest.skipIf(AppTest is None, "Install app/requirements.txt for UI tests")
class UITests(unittest.TestCase):
    def app(self):
        return AppTest.from_file(str(ROOT / "app/app.py"), default_timeout=15)

    def test_page_load_no_compute(self):
        with patch.object(service, "execute") as call:
            at = self.app().run()
            self.assertEqual(len(at.exception), 0)
            call.assert_not_called()

    def test_submit_validation_and_no_stale_success_after_error(self):
        result = {"status": "validation_passed", "researchers": 7, "eligible_publications": 437,
                  "possible_version_group_count": 19, "policy_version": policy.MATCH_POLICY,
                  "gold_build_id": "gold", "silver_build_id": "silver", "checked_at": "now"}
        with patch.object(service, "execute", return_value=result) as call:
            at = self.app().run()
            at.sidebar.button[0].click().run()
            self.assertEqual(len(at.success), 1)
            self.assertEqual(len(at.exception), 0)
            call.side_effect = RuntimeError("DO NOT DISPLAY THIS SECRET")
            at.text_area[0].set_value("Blockchain security")
            next(b for b in at.button if b.label == "Find evidence-backed candidates").click().run()
            self.assertEqual(len(at.error), 1)
            self.assertEqual(len(at.success), 0)
            self.assertNotIn("SECRET", at.error[0].value)

    def test_actual_policy_cards_render_partial_evidence(self):
        docs, data, snap = fixture()
        ev = policy.prepare_evidence(docs, data, "gold", "silver")
        rows = [{k: d[k] for k in policy.RESULT_COLUMNS} for d in docs]
        result = policy.match_candidates(policy.query_plan("Blockchain information security"), rows, ev, snap)
        result["checked_at"] = "now"
        with patch.object(service, "execute", return_value=result):
            at = self.app().run()
            at.text_area[0].set_value("Blockchain information security")
            next(b for b in at.button if b.label == "Find evidence-backed candidates").click().run()
            self.assertEqual(len(at.exception), 0)
            self.assertTrue(any("Missing terms: information" in text.value for text in at.text))
            self.assertTrue(any("Partial evidence" in caption.value for caption in at.caption))

    def test_empty_submit_does_not_call_backend(self):
        with patch.object(service, "execute") as call:
            at = self.app().run()
            next(b for b in at.button if b.label == "Find evidence-backed candidates").click().run()
            call.assert_not_called()
            self.assertEqual(len(at.error), 1)
