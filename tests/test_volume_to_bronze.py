"""Offline tests; live Spark/Delta execution is a separate Databricks smoke test."""
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("volume_bronze", ROOT / "notebooks/03_load_volume_to_bronze.py")
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)


def metadata(content, **kwargs):
    return dict({"request_url": "https://api.openalex.org/works", "request_parameters": {},
                 "fetched_at": "2026-09-05T12:00:00Z", "http_status": 200,
                 "content_type": "application/json", "query_reason": "zu_institution",
                 "sha256": loader.digest(content), "bytes": len(content), "record_count": 1}, **kwargs)


class AdapterTests(unittest.TestCase):
    def test_contracts_match_bronze_ddl(self):
        ddl = (ROOT / "sql/01_create_bronze_tables.sql").read_text()
        for table in loader.SPECS:
            body = re.search(r"CREATE TABLE IF NOT EXISTS " + table + r" \((.*?)\)\s*USING DELTA", ddl, re.S)[1]
            fields = []
            for line in body.splitlines():
                match = re.match(r"\s*(\w+)\s+(ARRAY<STRING>|STRING|TIMESTAMP|BIGINT|INT|BOOLEAN)(\s+NOT NULL)?", line)
                if match:
                    fields.append((match[1], match[2].lower(), not bool(match[3])))
            self.assertEqual(loader.columns(table), fields, table)

    def test_openalex_versions_ignore_json_order_and_fetch_context(self):
        first = b'{"results":[{"id":"https://openalex.org/W1","title":"Example","doi":null}]}'
        second = b'{"results":[{"doi":null,"title":"Example","id":"https://openalex.org/W1"}]}'
        a = loader.parse_rows("openalex_works_raw", first, metadata(first), {}, "run-a")[0]
        meta = metadata(second, fetched_at="2026-09-06T12:00:00Z", request_parameters={"cursor": "next"})
        b = loader.parse_rows("openalex_works_raw", second, meta, {}, "run-b")[0]
        self.assertEqual(a["record_key"], b["record_key"])
        self.assertIsNone(a["doi"])
        changed = second.replace(b"Example", b"Changed")
        c = loader.parse_rows("openalex_works_raw", changed, metadata(changed), {}, "run-c")[0]
        self.assertNotEqual(a["record_key"], c["record_key"])

    def test_all_openalex_entity_adapters_and_nested_payload(self):
        for table, (id_column, prefix) in loader.ENTITY_IDS.items():
            record = {"id": "https://openalex.org/" + prefix + "1", "extra": {"future_field": [1, None]},
                      "funder": {"id": "https://openalex.org/F2"}, "funder_award_id": "grant-1"}
            content = json.dumps(record).encode()
            row = loader.parse_rows(table, content, metadata(content), {}, "run")[0]
            self.assertEqual(row[id_column], record["id"])
            self.assertEqual(json.loads(row["raw_json"]), record)
            if prefix == "G":
                self.assertEqual(row["openalex_funder_id"], "https://openalex.org/F2")

    def test_openalex_rejects_wrong_entity_and_record_count(self):
        content = b'{"results":[{"id":"https://openalex.org/A1"}]}'
        with self.assertRaisesRegex(ValueError, "entity ID"):
            loader.parse_rows("openalex_works_raw", content, metadata(content), {}, "run")
        with self.assertRaisesRegex(ValueError, "record count"):
            loader.parse_rows("openalex_authors_raw", content, metadata(content, record_count=2), {}, "run")
        self.assertEqual(loader.parse_rows("openalex_works_raw", b'{"results":[]}',
                                          metadata(b'{"results":[]}', record_count=0), {}, "run"), [])

    def test_oai_deleted_record_and_resumption_prefix(self):
        content = b'''<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
        <responseDate>2026-09-05T00:00:00Z</responseDate><ListRecords><record>
        <header status="deleted"><identifier>oai:test:1</identifier><datestamp>2026-09-04</datestamp>
        <setSpec>publication:works</setSpec></header></record></ListRecords></OAI-PMH>'''
        meta = metadata(content, request_url="https://zuscholars.zu.ac.ae/do/oai/",
                        request_parameters={"verb": "ListRecords", "resumptionToken": "opaque+=="})
        row = loader.parse_rows("zu_scholars_oai_raw", content, meta, {"metadata_prefix": "oai_dc"}, "run")[0]
        self.assertTrue(row["is_deleted"])
        self.assertEqual(row["set_specs"], ["publication:works"])
        self.assertEqual(row["metadata_prefix"], "oai_dc")
        self.assertIn("opaque%2B%3D%3D", row["request_url"])
        self.assertEqual(row["record_key"], loader.digest("|".join([
            "oai:test:1", "2026-09-04", "oai_dc", row["record_hash"]])))

    def test_oai_protocol_errors_and_empty_count(self):
        content = b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><error code="noRecordsMatch"/></OAI-PMH>'
        self.assertEqual(loader.parse_rows("zu_scholars_oai_raw", content, metadata(content, record_count=0), {}, "run"), [])
        with self.assertRaisesRegex(ValueError, "record count"):
            loader.parse_rows("zu_scholars_oai_raw", content, metadata(content), {}, "run")
        with self.assertRaisesRegex(ValueError, "protocol error"):
            loader.parse_rows("zu_scholars_oai_raw", content.replace(b"noRecordsMatch", b"badResumptionToken"),
                              metadata(content), {}, "run")

    def test_directory_scope_sections_and_missing_links(self):
        html = '''<h3 class="en-static-heading">Adjunct Faculty</h3>
        <div class="picture-block"><div><h4 class="media-heading">Example <span>Person</span></h4>
        <a href="_profiles/person.aspx">profile</a></div></div>
        <h3 class="en-static-heading">Staff</h3><div class="picture-block">
        <h4 class="media-heading">Missing Link</h4><a href="#">empty</a></div>
        <div class="picture-block"><h4 class="media-heading">External Link</h4>
        <a href="https://elsewhere.example/_profiles/person.aspx">external</a></div>'''
        meta = metadata(html.encode(), request_url="https://www.zu.ac.ae/CB/faculty_and_staff/index.aspx",
                        college_code="CB", page_type="listing")
        rows = loader.parse_rows("zu_faculty_discovered_profiles", html.encode(), meta, {}, "run")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["displayed_name"], "Example Person")
        self.assertEqual(rows[0]["personnel_section"], "adjunct_faculty")
        self.assertEqual(rows[0]["crawl_status"], "discovered")

    def test_url_credentials_removed_and_identifiers_restricted(self):
        url = loader.sanitized_url({"request_url": "https://api.openalex.org/works?api_key=fake",
                                    "request_parameters": {"Authorization": "fake", "filter": "institutions.id:I1"}})
        self.assertNotIn("fake", url)
        self.assertIn("filter=", url)
        with self.assertRaises(ValueError):
            loader.sanitized_url({"request_url": "https://user:fake@example.org/"})
        with self.assertRaises(ValueError):
            loader.quoted_table("workspace; DROP SCHEMA x", "bronze", "table")


class ManifestAndWriteTests(unittest.TestCase):
    def test_preflight_failure_logs_without_business_writes(self):
        spark = Mock()
        spark.sql.return_value.first.return_value = {"user": "test-user"}
        table = "openalex_works_raw"
        plan = {table: {"files": [], "available_files": 1, "bounded": False}}
        errors = [{"dataset": table, "file_path": "/Volumes/test/bad.json", "message": "checksum mismatch"}]
        with patch.dict(sys.modules, {"pyspark.sql": SimpleNamespace(functions=Mock())}), \
                patch.object(loader, "append_control") as append, \
                patch.object(loader, "merge_rows") as merge:
            with self.assertRaisesRegex(RuntimeError, "No business tables"):
                loader.load_plan(spark, "workspace", "bronze", plan, errors, 1000, 8 * 1024 * 1024)
            merge.assert_not_called()
            self.assertEqual([call.args[3] for call in append.call_args_list],
                             ["ingestion_runs", "ingestion_errors"])
            self.assertEqual(append.call_args_list[0].args[4][0]["status"], "failed")

    def test_manifest_selection_corruption_and_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            batch = Path(directory) / "batch1"
            raw = batch / "raw"
            raw.mkdir(parents=True)
            content = b'{"results":[{"id":"https://openalex.org/W1"}]}'
            (raw / "one.json").write_bytes(content)
            meta = metadata(content, dataset="openalex_works_raw", path="raw/one.json")
            manifest = {"format_version": 1, "batch_id": "batch1", "config": {},
                        "sources": {"openalex": {"status": "complete"}}, "files": {"1": meta}}
            manifest_path = batch / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            plan = loader.select_inputs(directory, ["batch1"], ["openalex_works_raw"], 0)
            summary, errors = loader.validate_inputs(plan)
            self.assertEqual(errors, [])
            self.assertEqual(summary["openalex_works_raw"]["unique_versions"], 1)
            (raw / "one.json").write_bytes(b"corrupt")
            _, errors = loader.validate_inputs(plan)
            self.assertIn("checksum", errors[0]["message"])
            meta["path"] = "../outside.json"
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "escapes"):
                loader.select_inputs(directory, ["batch1"], ["openalex_works_raw"], 0)

    def test_chunks_deduplicate_across_buffers_and_enforce_bounds(self):
        rows = [{"record_key": str(i), "raw_json": "x" * 10} for i in range(5)]
        chunks = list(loader.unique_chunks(iter(rows + rows), max_rows=2, max_bytes=100))
        self.assertEqual([len(chunk) for chunk in chunks], [2, 2, 1])
        byte_chunks = list(loader.unique_chunks(rows, max_rows=100, max_bytes=22))
        self.assertEqual([len(chunk) for chunk in byte_chunks], [2, 2, 1])

    @patch.object(loader, "spark_schema", return_value="explicit schema")
    def test_business_merge_is_insert_only_and_cleans_temp_view(self, _):
        spark = Mock()
        loader.merge_rows(spark, "`workspace`.`bronze`.`openalex_works_raw`", "openalex_works_raw", [{"record_key": "k"}])
        query = spark.sql.call_args.args[0]
        self.assertIn("ON t.record_key = s.record_key WHEN NOT MATCHED THEN INSERT *", query)
        self.assertNotIn("UPDATE", query)
        self.assertNotIn("DELETE", query)
        spark.catalog.dropTempView.assert_called_once()
        spark.reset_mock()
        spark.sql.side_effect = RuntimeError("merge failed")
        with self.assertRaises(RuntimeError):
            loader.merge_rows(spark, "target", "openalex_works_raw", [])
        spark.catalog.dropTempView.assert_called_once()

    @patch.object(loader, "spark_schema", return_value="explicit schema")
    def test_control_merge_updates_run_id_only(self, _):
        spark = Mock()
        loader.merge_rows(spark, "runs", "ingestion_runs", [], control=True)
        self.assertIn("ON t.ingestion_run_id = s.ingestion_run_id WHEN MATCHED THEN UPDATE SET *",
                      spark.sql.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
