#!/usr/bin/env python3
"""Download public ResearchMatch sources outside Databricks into a resumable batch.

Raw response bytes are immutable; manifest.json is an atomic checkpoint. Re-running
the same batch reuses checksum-verified responses. Use a NEW batch for a refresh.
No Spark, publication downloads, or database credentials are involved.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
NS = {"oai": "http://www.openarchives.org/OAI/2.0/", "dc": "http://purl.org/dc/elements/1.1/"}
USER_AGENT = "ResearchMatchMetadataCollector/0.1"


def load_api_key():
    """Read only the expected key; .env is data and is never shell-executed."""
    key = os.getenv("OPENALEX_API_KEY", "").strip()
    path = ROOT / ".env"
    if not key and path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.strip().partition("=")
            if separator and name.strip() == "OPENALEX_API_KEY":
                key = value.strip().strip("\"'")
                break
    return key


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


class CollectionError(RuntimeError):
    pass


class Batch:
    def __init__(self, path, config, delay=0.4, max_requests=300):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.path / "manifest.json"
        if self.manifest_path.exists():
            self.manifest = json.loads(self.manifest_path.read_text())
            if self.manifest["config"] != config:
                raise CollectionError("Configuration changed: use a new batch directory.")
        else:
            self.manifest = {"format_version": 1, "batch_id": self.path.name,
                             "created_at": now(), "config": config, "files": {}, "sources": {}}
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.delay = delay
        self.max_requests = max_requests
        self.requests = 0
        self.key = load_api_key()
        self.save()

    def save(self):
        self.manifest["updated_at"] = now()
        atomic_json(self.manifest_path, self.manifest)

    def fetch(self, dataset, url, params=None, extension="json", reason=None):
        params = params or {}
        if "api_key" in params or urlsplit(url).query:
            raise CollectionError("Use separate sanitized parameters; never put credentials in URLs.")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in {
                "www.zu.ac.ae", "zuscholars.zu.ac.ae", "api.openalex.org", "pub.orcid.org"}:
            raise CollectionError("Source URL is outside the configured public sources.")
        identity = json.dumps([dataset, url, params], sort_keys=True).encode()
        request_id = sha(identity)
        cached = self.manifest["files"].get(request_id)
        if cached:
            content = (self.path / cached["path"]).read_bytes()
            if sha(content) != cached["sha256"]:
                raise CollectionError("Cached payload checksum mismatch: " + cached["path"])
            return content, cached
        headers = {}
        if parsed.hostname == "api.openalex.org" and self.key:
            headers["Authorization"] = "Bearer " + self.key
        if parsed.hostname == "pub.orcid.org":
            headers["Accept"] = "application/json"  # The public ORCID API defaults to XML.
        for attempt in range(4):
            if self.requests >= self.max_requests:
                raise CollectionError("Request budget reached; rerun this batch to resume.")
            self.requests += 1
            time.sleep(self.delay)
            try:
                response = self.session.get(url, params=params, headers=headers, timeout=(15, 60))
            except requests.RequestException:
                if attempt == 3:
                    raise CollectionError("Network request failed after retries: " + url) from None
                time.sleep(2 ** attempt)
                continue
            if response.status_code in (401, 403, 429):
                # Do not retry a daily allowance or authentication block indefinitely.
                raise CollectionError(f"HTTP {response.status_code} at {url}; check access/API allowance and resume later.")
            if response.status_code >= 500 and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            if response.status_code != 200:
                raise CollectionError(f"HTTP {response.status_code} at {url}")
            break
        content = response.content
        if extension == "json":
            try:
                json.loads(content)
            except ValueError:
                raise CollectionError("Expected JSON from " + url) from None
        elif extension == "xml":
            try:
                ET.fromstring(content)
            except ET.ParseError:
                raise CollectionError("Expected XML from " + url) from None
        elif extension == "html" and "html" not in response.headers.get("Content-Type", "").lower():
            raise CollectionError("Expected HTML from " + url)
        relative = f"raw/{dataset}/{request_id}.{extension}"
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        meta = {"path": relative, "dataset": dataset, "request_url": url,
                "request_parameters": params, "query_reason": reason, "fetched_at": now(),
                "http_status": response.status_code, "content_type": response.headers.get("Content-Type"),
                "sha256": sha(content), "bytes": len(content)}
        if parsed.hostname == "api.openalex.org":
            meta["rate_limit_remaining"] = response.headers.get("X-RateLimit-Remaining")
        self.manifest["files"][request_id] = meta
        self.save()
        return content, meta

    def annotate(self, meta, **values):
        meta.update(values)
        self.save()

    def derived(self, filename, value):
        atomic_json(self.path / "derived" / filename, value)

    def run(self, name, function):
        previous = self.manifest["sources"].get(name, {})
        self.manifest["sources"][name] = {"status": "running", "started_at": now()}
        self.save()
        try:
            stats = function()
            self.manifest["sources"][name].update(status="complete", **stats)
        except (CollectionError, ValueError, ET.ParseError, KeyError, OSError) as exc:
            message = str(exc)
            if self.key:
                message = message.replace(self.key, "[REDACTED]")
            self.manifest["sources"][name].update(status="partial", error=message,
                                                   previous_completed_stats=previous if previous.get("status") == "complete" else None)
        finally:
            self.manifest["sources"][name]["finished_at"] = now()
            self.save()
        print(name + ": " + json.dumps(self.manifest["sources"][name]), flush=True)


def parse_oai(content):
    root = ET.fromstring(content)
    if root.tag != "{" + NS["oai"] + "}OAI-PMH":
        raise CollectionError("Response is not an OAI-PMH envelope")
    errors = root.findall("oai:error", NS)
    if errors and not all(e.get("code") == "noRecordsMatch" for e in errors):
        raise CollectionError("OAI error: " + "; ".join(e.get("code", "unknown") for e in errors))
    records = root.findall("oai:ListRecords/oai:record", NS)
    token = root.find("oai:ListRecords/oai:resumptionToken", NS)
    return records, (token.text or "").strip() if token is not None else ""


def extract_dois(records):
    result = set()
    for record in records:
        for node in record.findall(".//dc:identifier", NS):
            text = (node.text or "").strip()
            match = re.search(r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*)(10\.\d{4,9}/\S+)", text, re.I)
            if not match and re.fullmatch(r"10\.\d{4,9}/\S+", text):
                result.add(text.lower())
            elif match:
                result.add(match.group(1).lower())
    return result


def collect_oai(batch, config):
    base = config["oai_url"]
    for verb in ("Identify", "ListMetadataFormats", "ListSets"):
        batch.fetch("zu_scholars_protocol", base, {"verb": verb}, "xml")
    token, seen, identifiers, dois = "", set(), set(), set()
    pages = records_count = deleted = 0
    while True:
        params = {"verb": "ListRecords", "resumptionToken": token} if token else {
            "verb": "ListRecords", "metadataPrefix": config["metadata_prefix"], "set": config["oai_set"]}
        content, meta = batch.fetch("zu_scholars_oai_raw", base, params, "xml")
        records, next_token = parse_oai(content)
        for record in records:
            header = record.find("oai:header", NS)
            identifier = header.findtext("oai:identifier", namespaces=NS) if header is not None else None
            if not identifier:
                raise CollectionError("OAI record missing identifier")
            identifiers.add(identifier)
            deleted += header.get("status") == "deleted"
        dois.update(extract_dois(records))
        records_count += len(records)
        pages += 1
        batch.annotate(meta, record_count=len(records), next_cursor=next_token)
        batch.derived("oai_dois.json", sorted(dois))
        if pages % 10 == 0:
            print(f"ZU Scholars: {pages} pages, {records_count} records", flush=True)
        if not next_token:
            break
        if next_token in seen:
            raise CollectionError("Repeated OAI cursor; refusing incomplete harvest")
        seen.add(next_token)
        token = next_token
    batch.derived("oai_identifiers.json", sorted(identifiers))
    return {"pages": pages, "records": records_count, "unique_identifiers": len(identifiers),
            "deleted_records": deleted, "unique_dois": len(dois), "scope": config["oai_set"]}


def safe_faculty_url(base, href, prefix):
    href = (href or "").strip()
    if not href or href.startswith("#"):
        return None
    parsed = urlsplit(urljoin(base, href))
    if parsed.hostname != "www.zu.ac.ae" or not parsed.path.startswith(prefix):
        return None
    if re.search(r"\.(pdf|jpg|png|gif|docx?|xlsx?)$", parsed.path, re.I):
        return None
    return urlunsplit(("https", parsed.netloc, parsed.path, "", ""))


def listing_entries(content, url, college, prefix):
    soup = BeautifulSoup(content, "html.parser")
    rows = []
    for card in soup.select(".picture-block"):
        name = card.select_one("h4.media-heading")
        if not name:
            continue
        section_node = card.find_previous("h3", class_="en-static-heading")
        section = section_node.get_text(" ", strip=True).lower() if section_node else "faculty"
        category = ("adjunct_faculty" if "adjunct" in section else "staff" if "staff" in section
                    else "administrators" if "administrator" in section else "faculty")
        links = [safe_faculty_url(url, a.get("href"), prefix) for a in card.select("a[href]")]
        profile = next((link for link in links if link and "_profiles/" in link), None)
        rows.append({"displayed_name": name.get_text(" ", strip=True), "profile_url": profile,
                     "listing_page_url": url, "college_code": college, "personnel_section": category,
                     "listing_text": card.get_text(" ", strip=True), "identity_status": "unverified"})
    children = sorted({link for a in soup.select("a[href]")
                       if (link := safe_faculty_url(url, a.get("href"), prefix))
                       and "_profiles/" not in link and urlsplit(link).path.endswith(("index", "index.aspx"))
                       and link != url})
    return rows, children


def collect_faculty(batch, config):
    robots, _ = batch.fetch("zu_faculty_protocol", "https://www.zu.ac.ae/robots.txt", extension="txt")
    policy = RobotFileParser()
    policy.parse(robots.decode("utf-8", errors="replace").splitlines())
    roster, pilot, issues = [], [], []
    for college in config["colleges"]:
        seed, code = college["listing_url"], college["code"]
        prefix = urlsplit(seed).path.rsplit("/", 1)[0] + "/"
        queue, visited, entries = [seed], set(), []
        while queue:
            url = queue.pop(0)
            if url in visited:
                continue
            if len(visited) >= 15:
                raise CollectionError("Faculty directory crawl exceeded 15 listing pages")
            if not policy.can_fetch(USER_AGENT, url):
                raise CollectionError("Faculty robots policy disallows " + url)
            content, meta = batch.fetch("zu_faculty_pages_raw", url, extension="html", reason="listing")
            batch.annotate(meta, college_code=code, page_type="listing")
            found, children = listing_entries(content, url, code, prefix)
            entries.extend(found)
            visited.add(url)
            queue.extend(child for child in children if child not in visited)
        if not entries:
            raise CollectionError("No faculty cards found for " + code)
        unique = {}
        for row in entries:
            key = row["profile_url"] or row["displayed_name"].casefold()
            # Prefer Faculty entries to duplicate administrative cards.
            if key not in unique or row["personnel_section"] == "faculty":
                unique[key] = row
        roster.extend(unique.values())
        eligible = sorted((r for r in unique.values() if r["profile_url"] and
                           r["personnel_section"] in ("faculty", "administrators") and
                           "professor" in r["listing_text"].lower()), key=lambda r: r["displayed_name"].casefold())
        limit = config["pilot_per_college"]
        selected = eligible[:limit] if limit else eligible
        for row in selected:
            try:
                if not policy.can_fetch(USER_AGENT, row["profile_url"]):
                    raise CollectionError("Robots policy disallows profile")
                content, meta = batch.fetch("zu_faculty_pages_raw", row["profile_url"], extension="html", reason="profile")
                batch.annotate(meta, college_code=code, page_type="profile", displayed_name=row["displayed_name"])
                soup = BeautifulSoup(content, "html.parser")
                row["orcids"] = sorted(set(re.findall(r"https?://orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[\dX]", str(soup), re.I)))
                row["profile_file"] = meta["path"]
                row["crawl_status"] = "fetched"
            except CollectionError as exc:
                row["crawl_status"] = "failed"
                issues.append({"url": row["profile_url"], "error": str(exc)})
            pilot.append(row)
        print(f"Faculty {code}: {len(unique)} roster entries, {len(selected)} pilot profiles", flush=True)
    batch.derived("faculty_roster.json", roster)
    batch.derived("pilot_researchers.json", pilot)
    batch.derived("faculty_issues.json", issues)
    if issues:
        raise CollectionError(f"{len(issues)} profile downloads failed; see derived/faculty_issues.json")
    return {"roster_entries": len(roster), "pilot_profiles": len(pilot),
            "profiles_with_orcid": sum(bool(r.get("orcids")) for r in pilot),
            "scope": "two-college candidate roster; pilot identities require Silver review"}


def oa_list(batch, entity, filters, reason, corpus=None):
    cursor, seen, count = "*", set(), 0
    while True:
        params = {"filter": filters, "per_page": 100, "cursor": cursor}
        if corpus:
            params["corpus"] = corpus
        content, meta = batch.fetch("openalex_" + entity + "_raw", "https://api.openalex.org/" + entity,
                                    params, reason=reason)
        data = json.loads(content)
        rows = data["results"]
        if not isinstance(rows, list):
            raise CollectionError("OpenAlex results is not a list")
        cursor = data.get("meta", {}).get("next_cursor")
        batch.annotate(meta, record_count=len(rows), source_total=data.get("meta", {}).get("count"), next_cursor=cursor)
        yield from rows
        count += len(rows)
        if count and count % 1000 == 0:
            print(f"OpenAlex {entity} ({reason}): {count} records", flush=True)
        if not rows or not cursor:
            break
        if cursor in seen:
            raise CollectionError("Repeated OpenAlex cursor")
        seen.add(cursor)


def oa_one(batch, entity, identifier, reason):
    if not re.fullmatch(r"[IAWFG]\d+", identifier):
        raise CollectionError("Invalid OpenAlex entity ID")
    content, meta = batch.fetch("openalex_" + entity + "_raw",
                                f"https://api.openalex.org/{entity}/{identifier}", reason=reason)
    data = json.loads(content)
    if not data.get("id"):
        raise CollectionError("OpenAlex singleton lacks id")
    batch.annotate(meta, record_count=1)
    return data


def chunks(items, size=50):
    items = sorted(set(items))
    for start in range(0, len(items), size):
        yield items[start:start + size]


def short_id(value):
    return (value or "").rsplit("/", 1)[-1]


def collect_openalex(batch, config):
    institution = oa_one(batch, "institutions", config["institution_id"], "zu_institution")
    if institution.get("display_name") != "Zayed University":
        raise CollectionError("Configured institution is not Zayed University")
    works = {}
    for work in oa_list(batch, "works", "institutions.id:" + config["institution_id"], "zu_institution"):
        works[work["id"]] = work
    for work in oa_list(batch, "works", "institutions.id:" + config["institution_id"], "zu_institution_expansion", corpus="expansion"):
        works[work["id"]] = work
    institution_query_works = len(works)
    inputs_path = getattr(batch, "inputs_path", batch.path)
    inputs_manifest = json.loads((inputs_path / "manifest.json").read_text())
    if any(inputs_manifest["sources"].get(s, {}).get("status") != "complete" for s in ("faculty", "oai")):
        raise CollectionError("Faculty/OAI input collection is not complete yet; institution works are cached. Resume after inputs complete.")
    if inputs_path.resolve() != batch.path.resolve():
        batch.manifest["upstream_batch"] = {"batch_id": inputs_manifest["batch_id"],
                                           "manifest_sha256": sha((inputs_path / "manifest.json").read_bytes())}
        batch.save()
    doi_path = inputs_path / "derived/oai_dois.json"
    if not doi_path.exists():
        raise CollectionError("Collect ZU Scholars first to prepare DOI resolution")
    source_dois = set(json.loads(doi_path.read_text()))
    found_dois = {re.sub(r"^https?://(?:dx\.)?doi.org/", "", w.get("doi") or "").lower() for w in works.values()}
    missing = source_dois - found_dois
    for group in chunks(missing):
        for work in oa_list(batch, "works", "doi:" + "|".join(group), "zu_scholars_doi", corpus="all"):
            works[work["id"]] = work
    batch.derived("openalex_work_ids.json", sorted(works))
    pilot_path = inputs_path / "derived/pilot_researchers.json"
    if not pilot_path.exists():
        raise CollectionError("Collect faculty first to prepare author resolution")
    pilot = json.loads(pilot_path.read_text())
    candidates = []
    author_ids = set()
    orcid_author_ids = set()
    for person in pilot:
        person_candidates = {}
        orcids = {o.lower() for o in person.get("orcids", [])}
        for group in chunks(orcids):
            for author in oa_list(batch, "authors", "orcid:" + "|".join(group), "orcid"):
                person_candidates[author["id"]] = {"author_id": author["id"], "method": "profile_orcid", "work_ids": []}
                orcid_author_ids.add(author["id"])
        target_name = " ".join(re.findall(r"\w+", person["displayed_name"].casefold()))
        for work in works.values():
            for authorship in work.get("authorships", []):
                author = authorship.get("author") or {}
                author_name = " ".join(re.findall(r"\w+", author.get("display_name", "").casefold()))
                zu_affiliation = any(short_id(i.get("id")) == config["institution_id"] for i in authorship.get("institutions", []))
                if author.get("id") and author_name == target_name and zu_affiliation:
                    candidate = person_candidates.setdefault(author["id"], {
                        "author_id": author["id"], "method": "exact_name_and_zu_authorship", "work_ids": []})
                    candidate["work_ids"].append(work["id"])
        if not person_candidates:
            content, meta = batch.fetch("openalex_authors_raw", "https://api.openalex.org/authors", {
                "search": person["displayed_name"], "per_page": 5}, reason="name_candidate")
            search_result = json.loads(content)
            batch.annotate(meta, record_count=len(search_result["results"]),
                           source_total=search_result.get("meta", {}).get("count"),
                           bounded_search=True)
            for author in search_result["results"]:
                person_candidates[author["id"]] = {
                    "author_id": author["id"], "method": "name_search_only", "work_ids": []}
        author_ids.update(person_candidates)
        candidates.append({"profile_url": person["profile_url"], "displayed_name": person["displayed_name"],
                           "candidates": list(person_candidates.values()), "verification_status": "needs_review"})
    batch.derived("author_identity_candidates.json", candidates)
    for group in chunks(author_ids):
        list(oa_list(batch, "authors", "openalex:" + "|".join(short_id(i) for i in group), "pilot_candidate"))
    # Profile-linked ORCID candidates justify collecting career works as raw evidence.
    # Name-only candidates wait for review before expanding their publication history.
    for group in chunks(orcid_author_ids):
        for work in oa_list(batch, "works", "authorships.author.id:" + "|".join(short_id(i) for i in group), "profile_orcid_career", corpus="all"):
            works[work["id"]] = work
    batch.derived("openalex_work_ids.json", sorted(works))
    # Obtain historical awards as source assertions, without claiming PI attribution.
    awards = {a["id"]: a for a in oa_list(batch, "awards", "institution_awarded.id:" + config["institution_id"], "zu_institution")}
    award_ids, funder_ids, retrieved_funders = collect_linked_awards(batch, works, awards)
    resolved_dois = {re.sub(r"^https?://(?:dx\.)?doi.org/", "", w.get("doi") or "").lower() for w in works.values()}
    batch.derived("unresolved_dois.json", sorted(source_dois - resolved_dois))
    return {"unique_works": len(works), "pilot_author_candidates": len(author_ids),
            "institution_query_works": institution_query_works,
            "institution_reported_works_count": institution.get("works_count"),
            "expansion_works": sum(bool(w.get("is_xpac")) for w in works.values()),
            "profile_orcid_authors_with_career_collection": len(orcid_author_ids),
            "pilot_profiles_with_candidates": sum(bool(p["candidates"]) for p in candidates),
            "unique_awards": len(awards), "funder_ids_requested": len(funder_ids),
            "unique_funders": len(retrieved_funders),
            "unresolved_award_ids": len(award_ids - set(awards)),
            "unresolved_funder_ids": len(funder_ids - retrieved_funders),
            "unresolved_dois": len(source_dois - resolved_dois),
            "scope": "ZU-affiliated works plus repository DOIs; pilot author candidates; linked award evidence"}


def collect_linked_awards(batch, works, awards):
    """Fetch awards and funders linked from works into awards; return requested/retrieved IDs."""
    award_ids, funder_ids = set(), set()
    for work in works.values():
        for award in work.get("awards") or []:
            identifier = award.get("id") if isinstance(award, dict) else award
            if identifier and re.fullmatch(r"G\d+", short_id(identifier)):
                award_ids.add(identifier)
        for funder in work.get("funders") or []:
            identifier = funder.get("id") if isinstance(funder, dict) else funder
            if identifier and re.fullmatch(r"F\d+", short_id(identifier)):
                funder_ids.add(identifier)
    for group in chunks(award_ids - set(awards)):
        # Unlike author/funder aliases, this endpoint requires full OpenAlex URLs.
        for award in oa_list(batch, "awards", "id:" + "|".join("https://openalex.org/" + short_id(i) for i in group), "linked_work"):
            awards[award["id"]] = award
    for award in awards.values():
        funder = award.get("funder") or {}
        if funder.get("id"):
            funder_ids.add(funder["id"])
    retrieved_funders = set()
    for group in chunks(funder_ids):
        retrieved_funders.update(f["id"] for f in oa_list(batch, "funders", "openalex:" + "|".join(short_id(i) for i in group), "linked_award_or_work"))
    batch.derived("unresolved_award_ids.json", sorted(award_ids - set(awards)))
    batch.derived("unresolved_funder_ids.json", sorted(funder_ids - retrieved_funders))
    return award_ids, funder_ids, retrieved_funders


ORCID_ID = r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]"


def collect_targeted(batch, targets, protected):
    """Supplementary batch: only the named candidate authors, their career works and linked awards.

    Never fetches faculty pages, OAI records or any protected (already verified) author:
    re-fetching those would change their Bronze records and make approved decisions stale.
    """
    protected_ids = {short_id(a) for a in protected["author_ids"]}
    protected_orcids = {short_id(o) for o in protected["orcids"]}
    author_ids, orcids = set(), set()
    for person in targets["people"]:
        for aid in person.get("author_ids", []):
            if not re.fullmatch(r"A\d+", aid):
                raise CollectionError("Invalid OpenAlex author ID for " + person["name"])
            author_ids.add(aid)
        for orcid in person.get("orcids", []):
            if not re.fullmatch(ORCID_ID, orcid):
                raise CollectionError("Invalid ORCID for " + person["name"])
            orcids.add(orcid)
    if author_ids & protected_ids or orcids & protected_orcids:
        raise CollectionError("Targets include an already verified author; refusing to change its records")
    # ORCID registry evidence is kept raw for audit; the Bronze loader does not read this dataset.
    for person in targets["people"]:
        if person.get("registry_query"):
            batch.fetch("orcid_registry_raw", "https://pub.orcid.org/v3.0/expanded-search/",
                        {"q": person["registry_query"], "rows": 5}, reason="registry_search")
        for orcid in person.get("orcids", []):
            batch.fetch("orcid_registry_raw", f"https://pub.orcid.org/v3.0/{orcid}/employments",
                        reason="registry_employment")
    by_orcid = {}
    for group in chunks(orcids):
        for author in oa_list(batch, "authors", "orcid:" + "|".join(group), "targeted_orcid"):
            author_ids.add(short_id(author["id"]))
            by_orcid.setdefault(short_id(author.get("orcid")), set()).add(short_id(author["id"]))
    if author_ids & protected_ids:
        raise CollectionError("An ORCID resolved to an already verified author; do not load this batch")
    authors = {}
    for group in chunks(author_ids):
        for author in oa_list(batch, "authors", "openalex:" + "|".join(group), "targeted_author"):
            authors[short_id(author["id"])] = author
    works = {}
    for group in chunks(authors):
        for work in oa_list(batch, "works", "authorships.author.id:" + "|".join(group), "targeted_career", corpus="all"):
            works[work["id"]] = work
    awards = {}
    award_ids, funder_ids, retrieved_funders = collect_linked_awards(batch, works, awards)
    batch.derived("targeted_people.json", [
        {"name": p["name"], "tier": p.get("tier"), "reason": p.get("reason"),
         "authors": sorted(a for a in authors if a in set(p.get("author_ids", []))
                           or any(a in by_orcid.get(o, ()) for o in p.get("orcids", [])))}
        for p in targets["people"]])
    batch.derived("openalex_work_ids.json", sorted(works))
    return {"targeted": True, "people": len(targets["people"]), "authors": len(authors),
            "unique_works": len(works), "unique_awards": len(awards),
            "funder_ids_requested": len(funder_ids), "unique_funders": len(retrieved_funders),
            "unresolved_award_ids": len(award_ids - set(awards)),
            "protected_authors_excluded": len(protected_ids),
            "scope": "supplementary: named candidate authors' career works and linked awards only"}


def verify(batch):
    for item in batch.manifest["files"].values():
        content = (batch.path / item["path"]).read_bytes()
        if len(content) != item["bytes"] or sha(content) != item["sha256"]:
            raise CollectionError("Integrity check failed: " + item["path"])
    print(json.dumps({"verified_files": len(batch.manifest["files"]),
                      "bytes": sum(f["bytes"] for f in batch.manifest["files"].values()),
                      "sources": batch.manifest["sources"]}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["all", "faculty", "oai", "openalex", "targeted", "verify"], default="all")
    parser.add_argument("--targets", help="targeted: JSON list of candidate authors/ORCIDs to collect")
    parser.add_argument("--protected", help="targeted: JSON of verified author IDs/ORCIDs that must not be re-fetched")
    parser.add_argument("--batch", required=True, help="Local batch directory; reuse to resume, new directory to refresh")
    parser.add_argument("--config", default=str(ROOT / "config/collection.json"))
    parser.add_argument("--inputs-batch", help="Optional separate completed faculty/OAI batch used by OpenAlex")
    parser.add_argument("--max-requests", type=int, default=300, help="HTTP request ceiling per invocation, including retries")
    parser.add_argument("--delay", type=float, default=0.4)
    args = parser.parse_args()
    if args.max_requests < 1 or args.delay < 0:
        parser.error("max-requests must be positive and delay nonnegative")
    config = json.loads(Path(args.config).read_text())
    batch = Batch(args.batch, config, args.delay, args.max_requests)
    batch.inputs_path = Path(args.inputs_batch) if args.inputs_batch else batch.path
    if args.source == "verify":
        verify(batch)
        return 0
    if args.source == "targeted":
        if not (args.targets and args.protected):
            parser.error("targeted needs --targets and --protected")
        targets = json.loads(Path(args.targets).read_text())
        protected = json.loads(Path(args.protected).read_text())
        # Recorded as the openalex source so the Bronze loader accepts its OpenAlex files.
        batch.run("openalex", lambda: collect_targeted(batch, targets, protected))
        return 0 if batch.manifest["sources"]["openalex"]["status"] == "complete" else 2
    for name, collector in [("faculty", collect_faculty), ("oai", collect_oai), ("openalex", collect_openalex)]:
        if args.source in ("all", name):
            batch.run(name, lambda collector=collector: collector(batch, config))
    batch.manifest["sources"]["zu_institutional_awards"] = {
        "status": "awaiting_approved_export", "required_for_pilot": False}
    batch.save()
    selected = [s for name, s in batch.manifest["sources"].items() if name in ("faculty", "oai", "openalex")]
    return 2 if any(s["status"] != "complete" for s in selected) else 0


if __name__ == "__main__":
    sys.exit(main())
