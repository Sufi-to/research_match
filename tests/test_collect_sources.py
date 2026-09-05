import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from collect_sources import Batch, CollectionError, collect_openalex, extract_dois, listing_entries, parse_oai, safe_faculty_url
from package_collection import inventory


class CollectionTests(unittest.TestCase):
    def test_oai_namespaces_dois_and_cursor(self):
        xml = b'''<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
        <ListRecords><record><header><identifier>test:1</identifier></header><metadata>
        <dc:identifier>https://doi.org/10.1234/ABC</dc:identifier>
        <dc:identifier>https://example.org/paper.pdf</dc:identifier>
        </metadata></record><resumptionToken>opaque+value==</resumptionToken></ListRecords></OAI-PMH>'''
        records, token = parse_oai(xml)
        self.assertEqual(token, "opaque+value==")
        self.assertEqual(extract_dois(records), {"10.1234/abc"})

    def test_oai_protocol_error_is_not_empty_success(self):
        with self.assertRaises(CollectionError):
            parse_oai(b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><error code="badResumptionToken"/></OAI-PMH>')

    def test_faculty_sections_missing_links_and_scope(self):
        url = "https://www.zu.ac.ae/faculty/index.aspx"
        html = '''<h3 class="en-static-heading">Adjunct Faculty</h3>
        <div class="picture-block"><h4 class="media-heading">Example Person</h4>
        <a href=" _profiles/person.aspx ">profile</a></div>
        <h3 class="en-static-heading">Staff</h3><div class="picture-block">
        <h4 class="media-heading">Missing Link</h4><a href="#">empty</a></div>'''
        rows, _ = listing_entries(html, url, "CB", "/faculty/")
        self.assertEqual(rows[0]["personnel_section"], "adjunct_faculty")
        self.assertEqual(rows[1]["personnel_section"], "staff")
        self.assertIsNone(rows[1]["profile_url"])
        self.assertIsNone(safe_faculty_url(url, "https://elsewhere.example/paper.pdf", "/faculty/"))
        self.assertIsNone(safe_faculty_url(url, "_profiles/person.pdf", "/faculty/"))

    @patch("collect_sources.load_api_key", return_value="secret-for-test")
    def test_cache_integrity_and_credentials(self, _):
        with tempfile.TemporaryDirectory() as directory:
            batch = Batch(directory, {}, delay=0)
            response = Mock(status_code=200, content=b'{"id":"https://openalex.org/I1"}', headers={"Content-Type": "application/json"})
            batch.session.get = Mock(return_value=response)
            _, meta = batch.fetch("institutions", "https://api.openalex.org/institutions/I1")
            batch.fetch("institutions", "https://api.openalex.org/institutions/I1")
            self.assertEqual(batch.session.get.call_count, 1)
            self.assertNotIn("secret-for-test", batch.manifest_path.read_text())
            self.assertEqual(batch.session.get.call_args.kwargs["headers"]["Authorization"], "Bearer secret-for-test")
            (Path(directory) / meta["path"]).write_bytes(b"corrupt")
            with self.assertRaises(CollectionError):
                batch.fetch("institutions", "https://api.openalex.org/institutions/I1")

    def test_rate_limit_does_not_cache_error_or_retry_forever(self):
        with tempfile.TemporaryDirectory() as directory:
            batch = Batch(directory, {}, delay=0)
            batch.session.get = Mock(return_value=Mock(status_code=429))
            with self.assertRaises(CollectionError):
                batch.fetch("works", "https://api.openalex.org/works")
            self.assertEqual(batch.session.get.call_count, 1)
            self.assertFalse(batch.manifest["files"])

    def test_package_excludes_unmanifested_files_and_detects_missing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "batch"
            batch = Batch(path, {}, delay=0)
            (path / ".env").write_text("OPENALEX_API_KEY=must-never-be-packaged")
            (path / "unexpected.txt").write_text("not a source file")
            batch.manifest["sources"] = {"faculty": {"status": "complete"}, "oai": {"status": "complete"}}
            batch.save()
            report, files = inventory([path])
            self.assertFalse(report["ready_for_bronze_loader"])
            self.assertEqual([name for _, name in files], ["batch/manifest.json"])

    def test_linked_award_retrieval_handles_full_url_filter_contract(self):
        work = {"id": "https://openalex.org/W1", "doi": None, "awards": [{"id": "https://openalex.org/G1"}]}
        award = {"id": "https://openalex.org/G1", "funder": {"id": "https://openalex.org/F1"}}

        def api_rows(batch, entity, filters, reason, corpus=None):
            if entity == "works" and corpus is None:
                return iter([work])
            if entity == "awards" and filters == "id:https://openalex.org/G1":
                return iter([award])
            if entity == "funders":
                return iter([{"id": "https://openalex.org/F1"}])
            return iter([])

        with tempfile.TemporaryDirectory() as directory:
            batch = Batch(directory, {}, delay=0)
            batch.manifest["sources"] = {"faculty": {"status": "complete"}, "oai": {"status": "complete"}}
            batch.save()
            batch.derived("oai_dois.json", [])
            batch.derived("pilot_researchers.json", [])
            with patch("collect_sources.oa_list", side_effect=api_rows), patch("collect_sources.oa_one", return_value={"display_name": "Zayed University"}):
                stats = collect_openalex(batch, {"institution_id": "I91044093"})
            self.assertEqual(stats["unique_awards"], 1)
            self.assertEqual(stats["unresolved_award_ids"], 0)
            self.assertEqual(stats["unique_funders"], 1)


if __name__ == "__main__":
    unittest.main()
