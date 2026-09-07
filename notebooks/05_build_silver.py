# Databricks notebook source
# MAGIC %md
# MAGIC # ResearchMatch Silver — bounded pilot build
# MAGIC Run SQL 04 first. Default mode is **validate**: no Delta writes.
# MAGIC Set widget execution behavior to **Do Nothing** before changing settings.
# MAGIC Only human-accepted identity decisions create researcher–work links.
# MAGIC Read `docs/silver_runbook.md`. Run one build at a time; no API calls/packages.

# COMMAND ----------

import hashlib
import html
from html.parser import HTMLParser
import itertools
import json
import math
import re
import unicodedata
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

TRANSFORM_VERSION = "silver_v1"
ZU_ID = "https://openalex.org/I91044093"
MAX_INPUT_ROWS = 60000
MAX_INPUT_BYTES = 512 * 1024 * 1024
MAX_OUTPUT_ROWS = 500000
MAX_OUTPUT_BYTES = 256 * 1024 * 1024

# Every derived table has build_id + row_key; the remaining contract is below.
# ! means NOT NULL. Business rows are immutable within a completed build.
CONTRACTS = {
    "researchers": "researcher_id:string! display_name:string! normalized_name:string! profile_url:string! college_codes:array<string>! personnel_sections:array<string>! biography:string research_activity_text:string profile_orcids:array<string>! identity_status:string! quality_flags:array<string>! source_record_keys:array<string>! observed_at:timestamp!",
    "researcher_aliases": "researcher_id:string! alias:string! normalized_alias:string! source_record_keys:array<string>!",
    "researcher_identifiers": "researcher_id:string! identifier_type:string! identifier_value:string! verification_status:string! source_record_keys:array<string>!",
    "researcher_affiliations": "researcher_id:string! institution_id:string! college_code:string personnel_section:string profile_url:string! observed_at:timestamp! verification_status:string! source_record_keys:array<string>!",
    "authors": "openalex_author_id:string! display_name:string normalized_name:string orcid:string works_count:bigint! last_known_institution_ids:array<string>! source_record_keys:array<string>!",
    "entity_resolution_review": "candidate_id:string! researcher_id:string! openalex_author_id:string! match_method:string! evidence_hash:string! evidence_json:string! status:string! decision_id:string quality_flags:array<string>!",
    "works": "work_id:string! title:string doi:string publication_year:int publication_type:string abstract:string abstract_source:string oai_subjects:array<string>! is_retracted:boolean! eligible_for_expertise:boolean! quality_flags:array<string>! source_record_keys:array<string>!",
    "work_sources": "work_id:string! source_system:string! source_id:string! doi:string source_title:string match_method:string! is_deleted:boolean! source_record_keys:array<string>!",
    "work_authorships": "work_id:string! source_system:string! source_id:string! author_position:int! openalex_author_id:string raw_author_name:string author_orcid:string institution_ids:array<string>! is_corresponding:boolean! source_record_keys:array<string>!",
    "researcher_works": "researcher_id:string! work_id:string! openalex_author_ids:array<string>! candidate_ids:array<string>! verification_status:string! eligible_for_expertise:boolean! source_record_keys:array<string>!",
    "topics": "topic_id:string! display_name:string subfield_name:string field_name:string domain_name:string source_record_keys:array<string>!",
    "work_topics": "work_id:string! topic_id:string! score:double! is_primary:boolean! source_record_keys:array<string>!",
    "researcher_topic_scores": "researcher_id:string! topic_id:string! score:double! supporting_work_count:bigint! scoring_version:string! as_of_year:int!",
    "coauthor_edges": "researcher_id_a:string! researcher_id_b:string! shared_work_count:bigint! work_ids:array<string>!",
    "funders": "funder_id:string! display_name:string country_code:string source_record_keys:array<string>!",
    "awards": "award_id:string! title:string funder_id:string funder_award_id:string amount:double currency:string start_date:string end_date:string institution_awarded_json:string investigators_json:string attribution_status:string! source_record_keys:array<string>!",
    "work_awards": "work_id:string! award_id:string! resolution_status:string! source_record_keys:array<string>!",
    "award_evidence": "award_id:string! work_id:string assertion_type:string! assertion_json:string! source_record_keys:array<string>!",
    "researcher_awards": "researcher_id:string! award_id:string! role:string verification_status:string! evidence_id:string!",
    "quality_issues": "entity_type:string! entity_id:string! issue_code:string! severity:string! detail:string! source_record_keys:array<string>!",
}
CONTROL_CONTRACTS = {
    "build_runs": "build_id:string! transform_version:string! input_fingerprint:string! status:string! started_at:timestamp! completed_at:timestamp as_of_year:int! source_versions_json:string! counts_json:string error_message:string created_by:string!",
    "identity_decisions": "decision_id:string! candidate_id:string! evidence_hash:string! decision:string! rationale:string! reviewed_by:string! reviewed_at:timestamp!",
}


def fields(table):
    spec = CONTROL_CONTRACTS[table] if table in CONTROL_CONTRACTS else "build_id:string! row_key:string! " + CONTRACTS[table]
    return [(name, kind.rstrip("!"), not kind.endswith("!"))
            for name, kind in (token.split(":", 1) for token in spec.split())]


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def js(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def key(*parts):
    return hashlib.sha256(js(parts).encode()).hexdigest()


def clean(value):
    return " ".join(html.unescape(str(value or "")).split()) or None


def normalized_name(value):
    text = unicodedata.normalize("NFKD", clean(value) or "").casefold()
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", text.replace("_", " ")).split())


def canonical_url(value):
    p = urlsplit(value)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise ValueError("Invalid public profile URL")
    # Path case is meaningful; do not lowercase it or remove campus components.
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, "", ""))


def normalize_doi(value):
    text = unquote(html.unescape(str(value or ""))).strip().lower()
    text = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|info:doi/|doi:\s*)", "", text)
    # Known malformed repository form: https://10.xxxx/suffix.
    text = re.sub(r"^https?://(?=10\.)", "", text)
    return text if re.fullmatch(r"10\.\d{4,9}/[^\s<>]+", text) else None


def normalize_orcid(value):
    match = re.fullmatch(r"(?:https?://orcid\.org/)?(\d{4}-\d{4}-\d{4}-\d{3}[\dXx])/?", str(value or "").strip())
    if not match:
        return None
    digits = match[1].replace("-", "").upper()
    total = 0
    for digit in digits[:-1]:
        total = (total + int(digit)) * 2
    check = (12 - total % 11) % 11
    return "https://orcid.org/" + match[1].upper() if digits[-1] == ("X" if check == 10 else str(check)) else None


def integer(value, default=None):
    try:
        if isinstance(value, bool):
            return default
        return int(value) if value is not None else default
    except (TypeError, ValueError, OverflowError):
        return default


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def reconstruct_abstract(index):
    if not index:
        return None, None
    if not isinstance(index, dict):
        return None, "invalid_abstract_index"
    positions = {}
    for word, offsets in index.items():
        if not isinstance(word, str) or not isinstance(offsets, list):
            return None, "invalid_abstract_index"
        for position in offsets:
            if type(position) is not int or not 0 <= position < 100000 or position in positions:
                return None, "invalid_abstract_index"
            positions[position] = word
    if not positions:
        return None, None
    if set(positions) != set(range(max(positions) + 1)):
        return None, "incomplete_abstract_index"
    return clean(" ".join(positions[i] for i in range(len(positions)))), None


class ProfileParser(HTMLParser):
    """Extract only the observed faculty-detail block, not site navigation."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.active = None
        self.heading = None
        self.section = "overview"
        self.sections = defaultdict(list)
        self.hrefs = []
        self.skip = 0
        self.tab_sections = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self.hrefs.append(attrs["href"].strip())
        if tag in ("script", "style"):
            self.skip += 1
        if tag == "div":
            self.depth += 1
            if "facutly-detail" in (attrs.get("class") or "").split():
                self.active = self.depth
            section = {"summary_bio": "bio", "research_prof_act": "research and professional activities",
                       "qualification": "education", "teaching_area": "teaching areas"}.get(attrs.get("id"))
            if self.active is not None and section:
                self.tab_sections[self.depth] = self.section
                self.section = section
        if self.active is not None and re.fullmatch(r"h[1-6]", tag):
            self.heading = []

    def handle_data(self, data):
        if self.active is not None and not self.skip:
            if self.heading is not None:
                self.heading.append(data)
            else:
                self.sections[self.section].append(data)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        if re.fullmatch(r"h[1-6]", tag) and self.heading is not None:
            self.section = normalized_name(" ".join(self.heading))
            self.heading = None
        if tag == "div":
            if self.depth in self.tab_sections:
                self.section = self.tab_sections.pop(self.depth)
            if self.active == self.depth:
                self.active = None
            self.depth -= 1


def profile_fields(raw_html):
    parser = ProfileParser()
    parser.feed(raw_html)
    sections = {k: clean(" ".join(v)) for k, v in parser.sections.items()}
    orcids = sorted({o for href in parser.hrefs if (o := normalize_orcid(href))})
    bio = next((v for k, v in sections.items() if k in ("bio", "biography", "biographical information") and v), None)
    research = clean("\n".join(v for k, v in sections.items() if "research" in k and v))
    return bio, research, orcids, bool(sections)


def oai_fields(raw_xml):
    root = ET.fromstring(raw_xml)
    metadata = root.find("{http://www.openarchives.org/OAI/2.0/}metadata")
    values = defaultdict(list)
    if metadata is not None:
        for element in metadata.iter():
            if element.tag.startswith("{http://purl.org/dc/elements/1.1/}"):
                value = clean("".join(element.itertext()))
                if value:
                    values[element.tag.split("}")[-1]].append(value)
    dois = sorted({doi for value in values["identifier"] if (doi := normalize_doi(value))})
    return values, dois


class Graph:
    def __init__(self, build_id, as_of_year):
        self.build_id = build_id
        self.year = as_of_year
        self.tables = {name: {} for name in CONTRACTS}
        self.row_count = 0
        self.byte_count = 0

    def put(self, table, identity, **values):
        row_key = key(table, identity)
        row = {"build_id": self.build_id, "row_key": row_key, **values}
        if set(row) != {name for name, _, _ in fields(table)}:
            raise ValueError("Column contract mismatch: " + table)
        for name, kind, nullable in fields(table):
            value = row[name]
            if value is None:
                if not nullable:
                    raise ValueError("Missing required " + table + "." + name)
                continue
            expected = {"string": str, "timestamp": datetime, "int": int, "bigint": int,
                        "boolean": bool, "double": float, "array<string>": list}[kind]
            if type(value) is not expected or (kind == "array<string>" and any(type(x) is not str for x in value)):
                raise ValueError("Wrong type for " + table + "." + name)
        previous = self.tables[table].get(row_key)
        if previous is not None and previous != row:
            raise ValueError("Conflicting output key in " + table)
        if previous is None:
            self.row_count += 1
            self.byte_count += len(json.dumps(row, default=str).encode())
            if self.row_count > MAX_OUTPUT_ROWS or self.byte_count > MAX_OUTPUT_BYTES:
                raise ValueError("Silver pilot output limit exceeded; use distributed transforms for a larger scope")
            self.tables[table][row_key] = row
        return row

    def issue(self, entity_type, entity_id, code, detail, sources=(), severity="warning"):
        self.put("quality_issues", [entity_type, entity_id, code], entity_type=entity_type,
                 entity_id=entity_id, issue_code=code, severity=severity, detail=detail,
                 source_record_keys=sorted(set(sources)))

    def rows(self, table):
        return list(self.tables[table].values())


def build_graph(read, decisions=(), build_id="validation", as_of_year=2026):
    """read(table) yields latest Bronze rows at frozen source versions, not API pages.

    Deliberately bounded Python pilot: retains structured output, not all raw payloads.
    No network, Spark, or filesystem access in this transformation core.
    """
    g = Graph(build_id, as_of_year)
    directories = defaultdict(list)
    for row in read("zu_faculty_discovered_profiles"):
        directories[canonical_url(row["profile_url"])].append(row)
    researchers = {}
    for row in read("zu_faculty_pages_raw"):
        if row["page_type"] != "profile":
            continue
        url = canonical_url(row["page_url"])
        listings = directories.get(url, [])
        if not listings:
            raise ValueError("Downloaded profile has no directory evidence: " + url)
        names = sorted({clean(r["displayed_name"]) for r in listings if clean(r["displayed_name"])})
        if not names:
            raise ValueError("Profile has no directory name: " + url)
        rid = "zu:" + key(url)
        bio, research, orcids, parsed = profile_fields(row["raw_html"])
        flags = []
        if not parsed:
            flags.append("profile_layout_unrecognized")
        if len(orcids) > 1:
            flags.append("multiple_profile_orcids")
        if "orcid.org" in row["raw_html"].lower() and not orcids:
            flags.append("unusable_profile_orcid")
        sources = sorted({row["record_key"], *(r["record_key"] for r in listings)})
        colleges = sorted({r["college_code"] for r in listings if r.get("college_code")})
        roles = sorted({r["personnel_section"] for r in listings if r.get("personnel_section")})
        researcher = g.put("researchers", rid, researcher_id=rid, display_name=names[0],
                          normalized_name=normalized_name(names[0]), profile_url=url, college_codes=colleges,
                          personnel_sections=roles, biography=bio, research_activity_text=research,
                          profile_orcids=orcids, identity_status="unmatched", quality_flags=flags,
                          source_record_keys=sources, observed_at=row["fetched_at"])
        researchers[rid] = researcher
        for alias in names:
            g.put("researcher_aliases", [rid, alias], researcher_id=rid, alias=alias,
                  normalized_alias=normalized_name(alias), source_record_keys=sources)
        for orcid in orcids:
            g.put("researcher_identifiers", [rid, "orcid", orcid], researcher_id=rid,
                  identifier_type="orcid", identifier_value=orcid, verification_status="source_asserted",
                  source_record_keys=[row["record_key"]])
        by_role = defaultdict(list)
        for item in listings:
            by_role[(item.get("college_code"), item.get("personnel_section"))].append(item["record_key"])
        for (college, role), keys in by_role.items():
            g.put("researcher_affiliations", [rid, college, role], researcher_id=rid,
                  institution_id=ZU_ID, college_code=college, personnel_section=role, profile_url=url,
                  observed_at=row["fetched_at"], verification_status="directory_observed",
                  source_record_keys=sorted(set(keys)))
        for flag in flags:
            g.issue("researcher", rid, flag, "Inspect the source profile before approval", sources)
        same_name_urls = [u for u, entries in directories.items() if u != url and any(
            normalized_name(e.get("displayed_name")) in {normalized_name(n) for n in names} for e in entries)]
        if same_name_urls:
            g.issue("researcher", rid, "other_directory_urls_same_name", js(sorted(same_name_urls)), sources)

    authors = {}
    for row in read("openalex_authors_raw"):
        data = json.loads(row["raw_json"])
        aid = row["openalex_author_id"]
        authors[aid] = g.put("authors", aid, openalex_author_id=aid,
                            display_name=clean(data.get("display_name")),
                            normalized_name=normalized_name(data.get("display_name")),
                            orcid=normalize_orcid(data.get("orcid")), works_count=integer(data.get("works_count"), 0),
                            last_known_institution_ids=sorted({i["id"] for i in data.get("last_known_institutions") or [] if i.get("id")}),
                            source_record_keys=[row["record_key"]])

    # Candidate generation is evidence only. Even exact ORCID candidates await review.
    author_orcids = defaultdict(set)
    for aid, author in authors.items():
        if author["orcid"]:
            author_orcids[author["orcid"]].add(aid)
    candidates = {}
    for rid, researcher in researchers.items():
        aliases = {r["normalized_alias"] for r in g.rows("researcher_aliases") if r["researcher_id"] == rid}
        for aid, author in authors.items():
            exact_orcid = author["orcid"] is not None and author["orcid"] in researcher["profile_orcids"]
            same_name = author["normalized_name"] in aliases
            token_match = any(len(set(n.split())) >= 2 and
                              set(n.split()).issubset(set(author["normalized_name"].split())) for n in aliases)
            if not (exact_orcid or same_name or token_match):
                continue
            flags = []
            if researcher["profile_orcids"] and author["orcid"] and not exact_orcid:
                flags.append("orcid_conflict")
            if len(researcher["profile_orcids"]) > 1:
                flags.append("multiple_profile_orcids")
            if author["orcid"] and len(author_orcids[author["orcid"]]) > 1:
                flags.append("shared_orcid_multiple_author_ids")
            evidence = {"profile_url": researcher["profile_url"], "profile_orcids": researcher["profile_orcids"],
                        "author_orcid": author["orcid"], "author_name": author["display_name"],
                        "zu_last_known_affiliation": ZU_ID in author["last_known_institution_ids"],
                        "researcher_source_keys": researcher["source_record_keys"],
                        "author_source_keys": author["source_record_keys"]}
            cid = key(rid, aid)
            candidates[cid] = g.put("entity_resolution_review", cid, candidate_id=cid,
                                   researcher_id=rid, openalex_author_id=aid,
                                   match_method="profile_orcid" if exact_orcid else "exact_normalized_name" if same_name else "name_tokens",
                                   evidence_hash=key(evidence), evidence_json=js(evidence), status="pending",
                                   decision_id=None, quality_flags=flags)

    latest_decisions = {}
    seen_decisions = set()
    for decision in decisions:
        if decision["decision_id"] in seen_decisions:
            raise ValueError("Duplicate identity decision_id")
        seen_decisions.add(decision["decision_id"])
        if decision["decision"] not in ("accept", "reject") or not clean(decision["rationale"]) or not clean(decision["reviewed_by"]):
            raise ValueError("Identity decision requires accept/reject, reviewer, and rationale")
        cid = decision["candidate_id"]
        old = latest_decisions.get(cid)
        if old is None or (decision["reviewed_at"], decision["decision_id"]) > (old["reviewed_at"], old["decision_id"]):
            latest_decisions[cid] = decision
    accepted = defaultdict(list)
    for cid, decision in latest_decisions.items():
        candidate = candidates.get(cid)
        if candidate is None or decision["evidence_hash"] != candidate["evidence_hash"]:
            g.issue("identity_decision", decision["decision_id"], "stale_or_missing_candidate",
                    "Decision is not applied; review current evidence and submit a new decision")
            continue
        candidate["decision_id"] = decision["decision_id"]
        candidate["status"] = "verified" if decision["decision"] == "accept" else "rejected"
        if decision["decision"] == "accept":
            if "orcid_conflict" in candidate["quality_flags"] or "multiple_profile_orcids" in candidate["quality_flags"]:
                raise ValueError("Cannot accept conflicting ORCID evidence; correct the source evidence first")
            accepted[candidate["openalex_author_id"]].append(candidate)
    for aid, links in accepted.items():
        if len({r["researcher_id"] for r in links}) != 1:
            raise ValueError("An OpenAlex author cannot be accepted for multiple pilot researchers")
        rid = links[0]["researcher_id"]
        g.put("researcher_identifiers", [rid, "openalex", aid], researcher_id=rid,
              identifier_type="openalex", identifier_value=aid, verification_status="verified",
              source_record_keys=authors[aid]["source_record_keys"])
    for rid, researcher in researchers.items():
        statuses = [c["status"] for c in candidates.values() if c["researcher_id"] == rid]
        researcher["identity_status"] = "verified" if "verified" in statuses else "ambiguous" if "pending" in statuses else "unmatched"

    works = {}
    doi_index = defaultdict(set)
    topic_sources = defaultdict(set)
    topic_values = {}
    award_links = defaultdict(set)
    author_work_links = defaultdict(list)

    def add_source(wid, system, source_id, doi, title, method, deleted, source_key):
        g.put("work_sources", [system, source_id], work_id=wid, source_system=system,
              source_id=source_id, doi=doi, source_title=title, match_method=method,
              is_deleted=deleted, source_record_keys=[source_key])

    for row in read("openalex_works_raw"):
        data = json.loads(row["raw_json"])
        oid = row["openalex_work_id"]
        wid = "oa:" + oid.rsplit("/", 1)[-1]
        doi = normalize_doi(data.get("doi"))
        abstract, abstract_issue = reconstruct_abstract(data.get("abstract_inverted_index"))
        flags = [abstract_issue] if abstract_issue else []
        title = clean(data.get("title") or data.get("display_name"))
        if not title:
            flags.append("missing_title")
        if not doi:
            flags.append("missing_doi")
        year = integer(data.get("publication_year"))
        if year is not None and not 1500 <= year <= as_of_year + 1:
            flags.append("invalid_publication_year")
            year = None
        retracted = data.get("is_retracted") is True
        works[wid] = g.put("works", wid, work_id=wid, title=title, doi=doi, publication_year=year,
                           publication_type=clean(data.get("type")), abstract=abstract,
                           abstract_source="openalex" if abstract else None, oai_subjects=[],
                           is_retracted=retracted, eligible_for_expertise=bool(title) and not retracted,
                           quality_flags=flags, source_record_keys=[row["record_key"]])
        if doi:
            doi_index[doi].add(wid)
        add_source(wid, "openalex", oid, doi, title, "openalex_id", False, row["record_key"])
        authorships = data.get("authorships") or []
        if not any((a.get("author") or {}).get("id") for a in authorships):
            flags.append("no_openalex_author_ids")
        for position, entry in enumerate(authorships, 1):
            author = entry.get("author") or {}
            aid = author.get("id")
            g.put("work_authorships", ["openalex", oid, position], work_id=wid,
                  source_system="openalex", source_id=oid, author_position=position,
                  openalex_author_id=aid, raw_author_name=clean(entry.get("raw_author_name") or author.get("display_name")),
                  author_orcid=normalize_orcid(author.get("orcid")),
                  institution_ids=sorted({i["id"] for i in entry.get("institutions") or [] if i.get("id")}),
                  is_corresponding=entry.get("is_corresponding") is True, source_record_keys=[row["record_key"]])
            if aid in accepted:
                candidate = accepted[aid][0]
                # Reject contradictory work-level ORCID assertions even after author approval.
                work_orcid = normalize_orcid(author.get("orcid"))
                expected = researchers[candidate["researcher_id"]]["profile_orcids"]
                if work_orcid and expected and work_orcid not in expected:
                    g.issue("authorship", key(wid, position), "authorship_orcid_conflict",
                            "Accepted author ID has a contradictory work-level ORCID", [row["record_key"]])
                else:
                    author_work_links[(candidate["researcher_id"], wid)].append((aid, candidate["candidate_id"], row["record_key"]))
        primary = (data.get("primary_topic") or {}).get("id")
        per_work_topics = {}
        for topic in [*(data.get("topics") or []), *([data["primary_topic"]] if data.get("primary_topic") else [])]:
            tid = topic.get("id")
            if not tid:
                continue
            score = number(topic.get("score"))
            if score is None or not 0 <= score <= 1:
                g.issue("work", wid, "invalid_topic_score", "Invalid source topic score omitted", [row["record_key"]])
                continue
            per_work_topics[tid] = max(score, per_work_topics.get(tid, 0.0))
            topic_sources[tid].add(row["record_key"])
            # Source partitions can arrive in any order. Choose a deterministic label.
            if tid not in topic_values or js(topic) < js(topic_values[tid]):
                topic_values[tid] = topic
        for tid, score in per_work_topics.items():
            g.put("work_topics", [wid, tid], work_id=wid, topic_id=tid, score=score,
                  is_primary=tid == primary, source_record_keys=[row["record_key"]])
        for award in data.get("awards") or []:
            if award.get("id"):
                award_links[(wid, award["id"])].add(row["record_key"])

    for doi, ids in doi_index.items():
        if len(ids) > 1:
            for wid in ids:
                works[wid]["quality_flags"].append("doi_multiple_openalex_works")
                g.issue("work", wid, "doi_multiple_openalex_works", doi, works[wid]["source_record_keys"])

    # OAI enriches a work only on a unique normalized DOI. Never fuzzy-merge titles.
    oai_doi_index = defaultdict(set)
    for row in read("zu_scholars_oai_raw"):
        sid = row["oai_identifier"]
        if row["is_deleted"]:
            g.issue("oai_record", sid, "oai_deleted", "Tombstone retained in Bronze; not used for enrichment", [row["record_key"]], "info")
            continue
        data, dois = oai_fields(row["raw_record_xml"])
        doi = dois[0] if len(dois) == 1 else None
        matches = doi_index.get(doi, set())
        wid = next(iter(matches)) if len(matches) == 1 else "oai:" + sid
        title = data["title"][0] if data["title"] else None
        year_values = [integer(v[:4]) for v in data["date"] if re.match(r"^\d{4}(?:$|-)", v)]
        year = next((v for v in year_values if v and 1500 <= v <= as_of_year + 1), None)
        description = clean("\n".join(data["description"]))
        if wid not in works:
            works[wid] = g.put("works", wid, work_id=wid, title=title, doi=doi,
                               publication_year=year, publication_type=data["type"][0] if data["type"] else None,
                               abstract=description, abstract_source="oai_description" if description else None,
                               oai_subjects=[], is_retracted=False, eligible_for_expertise=False,
                               quality_flags=["oai_only_unverified_authorship", "retraction_status_unknown"],
                               source_record_keys=[])
        work = works[wid]
        if len(dois) > 1:
            work["quality_flags"].append("multiple_oai_dois")
        if len(matches) > 1:
            work["quality_flags"].append("ambiguous_doi_link")
        if doi:
            oai_doi_index[doi].add(wid)
        if not work["title"] and title:
            work["title"] = title
            work["quality_flags"] = [f for f in work["quality_flags"] if f != "missing_title"]
        elif title and normalized_name(title) != normalized_name(work["title"]):
            work["quality_flags"].append("source_title_disagreement")
        if work["abstract"] is None and description:
            work["abstract"] = description
            work["abstract_source"] = "oai_description"
        if work["publication_year"] is None:
            work["publication_year"] = year
        work["oai_subjects"] = sorted(set(work["oai_subjects"]) | set(data["subject"]))
        work["source_record_keys"] = sorted(set(work["source_record_keys"]) | {row["record_key"]})
        add_source(wid, "zu_scholars", sid, doi, title, "unique_doi" if len(matches) == 1 else "oai_identifier", False, row["record_key"])
        for position, creator in enumerate(data["creator"], 1):
            g.put("work_authorships", ["zu_scholars", sid, position], work_id=wid,
                  source_system="zu_scholars", source_id=sid, author_position=position,
                  openalex_author_id=None, raw_author_name=creator, author_orcid=None,
                  institution_ids=[], is_corresponding=False, source_record_keys=[row["record_key"]])
    for doi, ids in oai_doi_index.items():
        if len(ids) > 1:
            for wid in ids:
                works[wid]["quality_flags"].append("doi_unresolved_multiple_works")
    for wid, work in works.items():
        if not work["abstract"]:
            work["quality_flags"].append("missing_abstract")
        if not work["title"]:
            work["quality_flags"].append("missing_title")
        if work["is_retracted"]:
            work["quality_flags"].append("retracted")
        work["quality_flags"] = sorted(set(work["quality_flags"]))
        work["eligible_for_expertise"] = (wid.startswith("oa:") and bool(work["title"]) and not work["is_retracted"]
                                            and not any(f in work["quality_flags"] for f in
                                                        ("doi_multiple_openalex_works", "ambiguous_doi_link", "doi_unresolved_multiple_works")))
    for tid, topic in topic_values.items():
        g.put("topics", tid, topic_id=tid, display_name=clean(topic.get("display_name")),
              subfield_name=clean((topic.get("subfield") or {}).get("display_name")),
              field_name=clean((topic.get("field") or {}).get("display_name")),
              domain_name=clean((topic.get("domain") or {}).get("display_name")),
              source_record_keys=sorted(topic_sources[tid]))
    for (rid, wid), evidence in author_work_links.items():
        g.put("researcher_works", [rid, wid], researcher_id=rid, work_id=wid,
              openalex_author_ids=sorted({a for a, _, _ in evidence}), candidate_ids=sorted({c for _, c, _ in evidence}),
              verification_status="verified", eligible_for_expertise=works[wid]["eligible_for_expertise"],
              source_record_keys=sorted({s for _, _, s in evidence}))

    funders = {}
    for row in read("openalex_funders_raw"):
        data = json.loads(row["raw_json"])
        fid = row["openalex_funder_id"]
        funders[fid] = g.put("funders", fid, funder_id=fid, display_name=clean(data.get("display_name")),
                             country_code=data.get("country_code"), source_record_keys=[row["record_key"]])
    awards = {}
    for row in read("openalex_awards_raw"):
        data = json.loads(row["raw_json"])
        aid = row["openalex_award_id"]
        fid = (data.get("funder") or {}).get("id")
        assertions = {k: data.get(k) for k in ("institution_awarded", "lead_investigator", "co_lead_investigator", "investigators", "provenance")}
        awards[aid] = g.put("awards", aid, award_id=aid, title=clean(data.get("display_name")),
                            funder_id=fid, funder_award_id=data.get("funder_award_id"), amount=number(data.get("amount")),
                            currency=data.get("currency"), start_date=data.get("start_date"), end_date=data.get("end_date"),
                            institution_awarded_json=js(data.get("institution_awarded")),
                            investigators_json=js({k: v for k, v in assertions.items() if "investigator" in k}),
                            attribution_status="unverified_external_evidence", source_record_keys=[row["record_key"]])
        g.put("award_evidence", [aid, "openalex_award"], award_id=aid, work_id=None,
              assertion_type="openalex_award_metadata", assertion_json=js(assertions), source_record_keys=[row["record_key"]])
        if fid and fid not in funders:
            g.issue("award", aid, "unresolved_funder", fid, [row["record_key"]])
    for (wid, aid), sources in award_links.items():
        g.put("work_awards", [wid, aid], work_id=wid, award_id=aid,
              resolution_status="resolved" if aid in awards else "unresolved", source_record_keys=sorted(sources))
        g.put("award_evidence", [wid, aid, "publication_link"], award_id=aid, work_id=wid,
              assertion_type="publication_mentions_award", assertion_json=js({"attribution_to_researchers": "not_established"}),
              source_record_keys=sorted(sources))
    linked_awards = {aid for _, aid in award_links}
    for aid in awards.keys() - linked_awards:
        g.issue("award", aid, "no_collected_publication_link", "Award retrieved independently; no collected publication references it",
                awards[aid]["source_record_keys"], "info")

    # Initial scoring is transparent and provisional, not a calibrated match score.
    topics_by_work = defaultdict(list)
    author_count = defaultdict(int)
    for item in g.rows("work_topics"):
        topics_by_work[item["work_id"]].append(item)
    for item in g.rows("work_authorships"):
        if item["source_system"] == "openalex":
            author_count[item["work_id"]] += 1
    scores = defaultdict(lambda: [0.0, set()])
    researchers_by_work = defaultdict(set)
    for link in g.rows("researcher_works"):
        if not link["eligible_for_expertise"]:
            continue
        rid, wid = link["researcher_id"], link["work_id"]
        researchers_by_work[wid].add(rid)
        year = works[wid]["publication_year"]
        recency = 0.5 if year is None else 0.5 ** (max(0, as_of_year - year) / 5.0)
        for topic in topics_by_work[wid]:
            entry = scores[(rid, topic["topic_id"])]
            entry[0] += topic["score"] * recency / max(1, author_count[wid])
            entry[1].add(wid)
    for (rid, tid), (score, supporting) in scores.items():
        g.put("researcher_topic_scores", [rid, tid], researcher_id=rid, topic_id=tid, score=float(score),
              supporting_work_count=len(supporting), scoring_version="v1_half_life5_fractional_authorship",
              as_of_year=as_of_year)
    edges = defaultdict(set)
    for wid, ids in researchers_by_work.items():
        for pair in itertools.combinations(sorted(ids), 2):
            edges[pair].add(wid)
    for (a, b), ids in edges.items():
        g.put("coauthor_edges", [a, b], researcher_id_a=a, researcher_id_b=b,
              shared_work_count=len(ids), work_ids=sorted(ids))
    validate_graph(g)
    return g


def validate_graph(g):
    # Include post-enrichment mutations in the memory/row guard, not just initial inserts.
    actual_bytes = sum(len(json.dumps(row, default=str).encode())
                       for rows in g.tables.values() for row in rows.values())
    if actual_bytes > MAX_OUTPUT_BYTES:
        raise ValueError("Silver pilot output limit exceeded after enrichment")
    g.byte_count = actual_bytes
    ids = lambda table, column: {r[column] for r in g.rows(table)}
    researchers = ids("researchers", "researcher_id")
    works = ids("works", "work_id")
    topics = ids("topics", "topic_id")
    for table, column, target in [
        ("researcher_identifiers", "researcher_id", researchers),
        ("researcher_aliases", "researcher_id", researchers),
        ("researcher_affiliations", "researcher_id", researchers),
        ("entity_resolution_review", "researcher_id", researchers),
        ("researcher_works", "researcher_id", researchers),
        ("researcher_works", "work_id", works),
        ("work_sources", "work_id", works), ("work_authorships", "work_id", works),
        ("work_topics", "work_id", works), ("work_topics", "topic_id", topics),
        ("work_awards", "work_id", works),
        ("researcher_topic_scores", "researcher_id", researchers),
        ("researcher_topic_scores", "topic_id", topics),
    ]:
        if not ids(table, column).issubset(target):
            raise ValueError("Orphan relationship: " + table + "." + column)
    if g.rows("researcher_awards"):
        raise ValueError("V1 cannot create researcher-award attribution without approved institutional evidence")
    eligible = {r["work_id"] for r in g.rows("works") if r["eligible_for_expertise"]}
    if any(r["eligible_for_expertise"] and r["work_id"] not in eligible for r in g.rows("researcher_works")):
        raise ValueError("Ineligible work entered expertise evidence")
    approved = {r["candidate_id"]: r for r in g.rows("entity_resolution_review") if r["status"] == "verified"}
    for link in g.rows("researcher_works"):
        if not link["candidate_ids"] or any(cid not in approved or approved[cid]["researcher_id"] != link["researcher_id"]
                                            for cid in link["candidate_ids"]):
            raise ValueError("Researcher-work relationship lacks approved identity evidence")
    award_ids = ids("awards", "award_id")
    for link in g.rows("work_awards"):
        if (link["resolution_status"] == "resolved") != (link["award_id"] in award_ids):
            raise ValueError("Award resolution status disagrees with available evidence")
    for edge in g.rows("coauthor_edges"):
        if (edge["researcher_id_a"] not in researchers or edge["researcher_id_b"] not in researchers
                or edge["researcher_id_a"] >= edge["researcher_id_b"]
                or not set(edge["work_ids"]).issubset(eligible)
                or edge["shared_work_count"] != len(set(edge["work_ids"]))):
            raise ValueError("Invalid coauthor edge")

# COMMAND ----------

BRONZE_KEYS = {
    "zu_faculty_discovered_profiles": "record_key",
    "zu_faculty_pages_raw": "page_url",
    "openalex_authors_raw": "openalex_author_id",
    "openalex_works_raw": "openalex_work_id",
    "zu_scholars_oai_raw": "oai_identifier",
    "openalex_funders_raw": "openalex_funder_id",
    "openalex_awards_raw": "openalex_award_id",
}


def qualified(catalog, schema, table):
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v) for v in (catalog, schema, table)):
        raise ValueError("Unsafe SQL identifier")
    return ".".join("`" + v + "`" for v in (catalog, schema, table))


def spark_schema(table):
    from pyspark.sql.types import StructType, StructField, StringType, TimestampType, LongType, IntegerType, BooleanType, DoubleType, ArrayType
    kinds = {"string": StringType(), "timestamp": TimestampType(), "int": IntegerType(),
             "bigint": LongType(), "boolean": BooleanType(), "double": DoubleType(), "array<string>": ArrayType(StringType())}
    return StructType([StructField(n, kinds[t], nullable) for n, t, nullable in fields(table)])


def bounded_read(read):
    totals = {"rows": 0, "bytes": 0}
    def iterator(table):
        seen = set()
        for row in read(table):
            totals["rows"] += 1
            totals["bytes"] += sum(len(v.encode()) for v in row.values() if isinstance(v, str))
            if totals["rows"] > MAX_INPUT_ROWS or totals["bytes"] > MAX_INPUT_BYTES:
                raise ValueError("Silver pilot input limit exceeded; use distributed transforms for a larger scope")
            entity = row[BRONZE_KEYS[table]]
            if entity in seen:
                raise ValueError("Input reader returned multiple latest rows for " + table)
            seen.add(entity)
            yield row
        if not seen and table not in ("openalex_authors_raw", "openalex_awards_raw", "openalex_funders_raw"):
            raise ValueError("Required Bronze source is empty: " + table)
        print("Read " + table + ": " + str(len(seen)) + " latest records", flush=True)
    return iterator, totals


def dataframe_chunks(rows, max_rows=10000, max_bytes=4 * 1024 * 1024):
    chunk, size = [], 0
    for row in rows:
        n = len(json.dumps(row, default=str).encode())
        if n > max_bytes:
            raise ValueError("Silver row exceeds write buffer limit")
        if chunk and (len(chunk) >= max_rows or size + n > max_bytes):
            yield chunk
            chunk, size = [], 0
        chunk.append(row)
        size += n
    if chunk:
        yield chunk


def write_build(spark, catalog, schema, graph, run):
    """Publish only after every derived table passes persisted-count validation."""
    runs = qualified(catalog, schema, "build_runs")
    spark.createDataFrame([run], spark_schema("build_runs")).write.mode("append").saveAsTable(runs)
    view = "silver_run_" + uuid.uuid4().hex
    try:
        for table in CONTRACTS:
            target = qualified(catalog, schema, table)
            for chunk in dataframe_chunks(graph.rows(table)):
                spark.createDataFrame(chunk, spark_schema(table)).write.mode("append").saveAsTable(target)
            counts = spark.sql(f"SELECT COUNT(*) AS n, COUNT(DISTINCT row_key) AS keys FROM {target} WHERE build_id = '{graph.build_id}'").first()
            expected = len(graph.tables[table])
            if counts["n"] != expected or counts["keys"] != expected:
                raise ValueError("Persisted row count/key check failed: " + table)
            print("Wrote and checked " + table + ": " + str(expected), flush=True)
        run.update(status="succeeded", completed_at=now())
    except Exception as exc:
        run.update(status="failed", completed_at=now(), error_message=str(exc)[:4000])
        raise
    finally:
        spark.createDataFrame([run], spark_schema("build_runs")).createOrReplaceTempView(view)
        try:
            spark.sql(f"MERGE INTO {runs} t USING `{view}` s ON t.build_id = s.build_id WHEN MATCHED THEN UPDATE SET *").collect()
        finally:
            spark.catalog.dropTempView(view)


def run_notebook(spark, dbutils):
    dbutils.widgets.text("catalog", "workspace")
    dbutils.widgets.text("bronze_schema", "researchmatch_bronze")
    dbutils.widgets.text("silver_schema", "researchmatch_silver")
    dbutils.widgets.dropdown("mode", "validate", ["validate", "build"])
    dbutils.widgets.text("expected_pilot_profiles", "50")
    dbutils.widgets.text("as_of_year", "2026")
    get = lambda n: dbutils.widgets.get(n).strip()
    catalog, bronze, silver, mode = (get(n) for n in ("catalog", "bronze_schema", "silver_schema", "mode"))
    year, expected_profiles = int(get("as_of_year")), int(get("expected_pilot_profiles"))
    if mode not in ("validate", "build") or not 2020 <= year <= 2100 or not 1 <= expected_profiles <= 200:
        raise ValueError("Use validate/build, a valid scoring year, and 1–200 expected pilot profiles")
    for table in [*CONTRACTS, *CONTROL_CONTRACTS]:
        target = qualified(catalog, silver, table)
        if not spark.catalog.tableExists(target):
            raise ValueError("Run SQL 04 first: missing " + target)
        actual = {f.name: f.dataType.simpleString() for f in spark.table(target).schema.fields}
        if actual != {n: kind for n, kind, _ in fields(table)}:
            raise ValueError("Silver schema mismatch: " + target)
        if spark.sql("DESCRIBE DETAIL " + target).first()["format"] != "delta":
            raise ValueError("Expected Delta: " + target)
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    versions = {}
    for table in [*BRONZE_KEYS, "ingestion_runs"]:
        target = qualified(catalog, bronze, table)
        versions[table] = int(spark.sql("DESCRIBE HISTORY " + target + " LIMIT 1").first()["version"])
    decision_target = qualified(catalog, silver, "identity_decisions")
    versions["identity_decisions"] = int(spark.sql("DESCRIBE HISTORY " + decision_target + " LIMIT 1").first()["version"])
    decisions = [r.asDict() for r in spark.sql(
        f"SELECT * FROM {decision_target} VERSION AS OF {versions['identity_decisions']} LIMIT 10001").collect()]
    if len(decisions) > 10000:
        raise ValueError("Too many identity decisions for the bounded pilot")
    run_source = qualified(catalog, bronze, "ingestion_runs")
    latest_runs = spark.sql(
        f"SELECT dataset_name, status FROM {run_source} VERSION AS OF {versions['ingestion_runs']} "
        "QUALIFY ROW_NUMBER() OVER (PARTITION BY dataset_name ORDER BY started_at DESC, ingestion_run_id DESC) = 1").collect()
    successful = {r["dataset_name"] for r in latest_runs if r["status"] == "succeeded"}
    missing = set(BRONZE_KEYS) - successful
    if missing:
        raise ValueError("Complete full Bronze loads first: " + ", ".join(sorted(missing)))
    # Operational log-only changes do not change the derived research graph.
    fingerprint = key(TRANSFORM_VERSION, catalog, bronze, silver,
                      {k: v for k, v in versions.items() if k != "ingestion_runs"}, year, expected_profiles)
    runs = qualified(catalog, silver, "build_runs")
    if mode == "build":
        previous = spark.sql(f"SELECT build_id FROM {runs} WHERE status = 'succeeded' AND input_fingerprint = '{fingerprint}' ORDER BY completed_at DESC LIMIT 1").first()
        if previous:
            print("Identical successful build already exists: " + previous["build_id"] + ". No writes performed.")
            return previous["build_id"]

    def read(table):
        target = qualified(catalog, bronze, table)
        ordering = "fetched_at DESC, " + ("source_updated_at DESC NULLS LAST, " if table.startswith("openalex_") else "") + "record_key"
        if table == "zu_faculty_discovered_profiles":
            ordering = "discovered_at DESC, record_key"
        where = "WHERE metadata_prefix = 'oai_dc'" if table == "zu_scholars_oai_raw" else ""
        query = (f"SELECT * FROM {target} VERSION AS OF {versions[table]} {where} "
                 f"QUALIFY ROW_NUMBER() OVER (PARTITION BY {BRONZE_KEYS[table]} ORDER BY {ordering}) = 1")
        # Bound the largest downloaded partition; no RDD/cache APIs on serverless.
        # OAI order determines which description supplies an otherwise missing abstract.
        frame = spark.sql(query).limit(MAX_INPUT_ROWS + 1)
        frame = frame.orderBy("oai_identifier") if table == "zu_scholars_oai_raw" else frame.repartition(16)
        for row in frame.toLocalIterator():
            yield row.asDict(recursive=True)

    reader, input_totals = bounded_read(read)
    build_id = str(uuid.uuid4())
    graph = build_graph(reader, decisions, build_id, year)
    if len(graph.tables["researchers"]) != expected_profiles:
        raise ValueError("Unexpected pilot size; expected " + str(expected_profiles) + ", got " + str(len(graph.tables["researchers"])))
    counts = {table: len(rows) for table, rows in graph.tables.items()}
    summary = {"mode": mode, "input_fingerprint": fingerprint, "input": input_totals,
               "counts": counts, "identity_statuses": {s: sum(r["status"] == s for r in graph.rows("entity_resolution_review"))
                                                       for s in ("pending", "verified", "rejected")}}
    print(json.dumps(summary, indent=2))
    if mode == "validate":
        print("Silver validation passed. No Delta data was written. Review counts, then choose mode=build.")
        return summary
    run = {"build_id": build_id, "transform_version": TRANSFORM_VERSION, "input_fingerprint": fingerprint,
           "status": "started", "started_at": now(), "completed_at": None, "as_of_year": year,
           "source_versions_json": js(versions), "counts_json": js(counts), "error_message": None,
           "created_by": spark.sql("SELECT current_user() AS user").first()["user"]}
    write_build(spark, catalog, silver, graph, run)
    print("Silver build succeeded: " + build_id + ". Explore the *_current views; identity review comes next.")
    return summary


if "spark" in globals() and "dbutils" in globals():
    silver_result = run_notebook(spark, dbutils)
