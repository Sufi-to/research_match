import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from collect_sources import Batch, CollectionError, collect_openalex, collect_targeted, extract_dois, listing_entries, parse_oai, safe_faculty_url
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


class TargetedCollectionTests(unittest.TestCase):
    TARGETS = {"people": [
        {"name": "Target One", "author_ids": ["A1"], "orcids": ["0000-0001-0000-0001"],
         "registry_query": 'given-and-family-names:"Target One"'},
        {"name": "Target Two", "orcids": ["0000-0002-0000-000X"]}]}
    PROTECTED = {"author_ids": ["https://openalex.org/A9"], "orcids": ["https://orcid.org/0000-0009-0000-0009"]}

    def run_targeted(self, targets=None, protected=None, orcid_author="A2"):
        calls = []

        def api_rows(batch, entity, filters, reason, corpus=None):
            calls.append((entity, filters, reason))
            if entity == "authors" and filters.startswith("orcid:"):
                return iter([{"id": "https://openalex.org/" + orcid_author, "orcid": "https://orcid.org/0000-0002-0000-000X"}])
            if entity == "authors":
                return iter([{"id": "https://openalex.org/" + a, "orcid": None} for a in filters.split(":", 1)[1].split("|")])
            if entity == "works":
                return iter([{"id": "https://openalex.org/W1", "awards": [], "funders": []}])
            return iter([])

        with tempfile.TemporaryDirectory() as directory:
            batch = Batch(directory, {}, delay=0)
            with patch("collect_sources.oa_list", side_effect=api_rows), \
                    patch.object(batch, "fetch", return_value=(b"{}", {})) as fetch:
                stats = collect_targeted(batch, targets or self.TARGETS, protected or self.PROTECTED)
                people = json.loads((Path(directory) / "derived/targeted_people.json").read_text())
            return stats, calls, fetch, people

    def test_fetches_only_targets_and_keeps_registry_evidence(self):
        stats, calls, fetch, people = self.run_targeted()
        self.assertEqual(stats["authors"], 2)
        self.assertEqual(stats["unique_works"], 1)
        self.assertIn(("authors", "openalex:A1|A2", "targeted_author"), calls)
        self.assertIn(("works", "authorships.author.id:A1|A2", "targeted_career"), calls)
        self.assertFalse(any(e not in ("authors", "works") for e, _, _ in calls))
        urls = [c.args[1] for c in fetch.call_args_list]
        self.assertTrue(all(u.startswith("https://pub.orcid.org/") for u in urls))
        self.assertEqual(sum(u.endswith("/employments") for u in urls), 2)
        self.assertEqual(people[1]["authors"], ["A2"])

    def test_refuses_protected_author_id(self):
        targets = {"people": [{"name": "Verified", "author_ids": ["A9"]}]}
        with self.assertRaises(CollectionError):
            self.run_targeted(targets)

    def test_refuses_protected_orcid(self):
        targets = {"people": [{"name": "Verified", "orcids": ["0000-0009-0000-0009"]}]}
        with self.assertRaises(CollectionError):
            self.run_targeted(targets)

    def test_orcid_resolving_to_verified_author_blocks_batch(self):
        with self.assertRaises(CollectionError):
            self.run_targeted(orcid_author="A9")

    def test_rejects_malformed_identifiers(self):
        with self.assertRaises(CollectionError):
            self.run_targeted({"people": [{"name": "Bad", "author_ids": ["W1"]}]})
        with self.assertRaises(CollectionError):
            self.run_targeted({"people": [{"name": "Bad", "orcids": ["https://orcid.org/x"]}]})

    def test_orcid_requests_ask_for_json(self):
        with tempfile.TemporaryDirectory() as directory:
            batch = Batch(directory, {}, delay=0)
            batch.session.get = Mock(return_value=Mock(status_code=200, content=b"{}", headers={}))
            batch.fetch("orcid_registry_raw", "https://pub.orcid.org/v3.0/0000-0001-0000-0001/employments")
            self.assertEqual(batch.session.get.call_args.kwargs["headers"]["Accept"], "application/json")

    def test_package_accepts_complete_supplementary_batch_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "targeted"
            batch = Batch(path, {}, delay=0)
            batch.manifest["sources"] = {"openalex": {"status": "partial", "targeted": True}}
            batch.save()
            self.assertFalse(inventory([path])[0]["ready_for_bronze_loader"])
            batch.manifest["sources"]["openalex"]["status"] = "complete"
            batch.save()
            report, _ = inventory([path])
            self.assertTrue(report["supplementary"])
            self.assertTrue(report["ready_for_bronze_loader"])


if __name__ == "__main__":
    unittest.main()
