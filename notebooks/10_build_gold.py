# Databricks notebook source
# MAGIC %md
# MAGIC # ResearchMatch Gold — verified seven-profile pilot
# MAGIC Run SQL 09 first, then Run All. Default mode is **validate** (no writes).
# MAGIC Set Widget Panel Settings to **Do Nothing** before changing mode.
# MAGIC Uses one successful Silver v2 snapshot. No API calls, LLM, embeddings,
# MAGIC endpoint creation, packages, or Lakebase. See docs/gold_runbook.md.

# COMMAND ----------

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import html
import json
import math
import re
import uuid
from urllib.parse import quote, urlsplit

TRANSFORM_VERSION = "gold_v1_verified_contributions"
SILVER_VERSION = "silver_v2_concept_versions"
MAX_INPUT_ROWS = 100000
MAX_INPUT_BYTES = 128 * 1024 * 1024
MAX_OUTPUT_ROWS = 20000
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_SEARCH_CHARS = 6000
MAX_SEARCH_PUBLICATIONS = 12
MAX_SEARCH_TOPICS = 10

# All reads additionally require Silver build_id and row_key. Keep raw biographies,
# grant statements, author metrics and irrelevant work metadata out of this builder.
SOURCE_COLUMNS = {
    "researchers": "researcher_id display_name profile_url college_codes profile_orcids observed_at identity_status quality_flags",
    "researcher_identifiers": "researcher_id identifier_type identifier_value verification_status",
    "entity_resolution_review": "candidate_id researcher_id openalex_author_id status decision_id",
    "researcher_works": "researcher_id work_id candidate_ids eligible_for_expertise verification_status",
    "researcher_contributions": "researcher_id contribution_id representative_work_id source_work_ids eligible_for_expertise publication_type",
    "work_contributions": "work_id contribution_id representative_work_id is_representative verification_status",
    "works": "work_id title doi publication_year publication_type abstract abstract_source is_retracted eligible_for_expertise quality_flags source_record_keys",
    "work_topics": "work_id topic_id score",
    "topics": "topic_id display_name field_name subfield_name domain_name",
    "researcher_topic_scores": "researcher_id topic_id score supporting_work_count scoring_version as_of_year",
    "quality_issues": "entity_type entity_id issue_code severity",
    "researcher_awards": "researcher_id award_id verification_status",
}

DOCUMENT_SPEC = "document_id:string! researcher_id:string! display_name:string! profile_url:string! college_codes_text:string! top_topics_text:string! search_text:string! search_text_sha256:string! publication_evidence_ids:array<string>! topic_ids:array<string>! eligible_contribution_count:bigint! eligible_preprint_count:bigint! affiliation_status:string! funding_status:string! is_searchable:boolean!"
CONTRACTS = {
    "researcher_profiles": "researcher_id:string! display_name:string! profile_url:string! college_codes:array<string>! profile_orcids:array<string>! verified_openalex_author_ids:array<string>! profile_observed_at:timestamp! identity_status:string! affiliation_status:string! linked_source_record_count:bigint! contribution_count:bigint! eligible_contribution_count:bigint! excluded_contribution_count:bigint! eligible_preprint_count:bigint! recent_contribution_count:bigint! latest_publication_year:int funding_status:string! verified_award_count:bigint profile_summary:string! as_of_year:int!",
    "researcher_profile_publications": "publication_evidence_id:string! researcher_id:string! contribution_id:string! representative_work_id:string! source_work_ids:array<string>! evidence_rank:int! title:string! doi:string evidence_url:string! publication_year:int publication_type:string! is_preprint:boolean! abstract_excerpt:string repository_description_excerpt:string abstract_source:string quality_flags:array<string>! source_record_keys:array<string>!",
    "researcher_profile_topics": "researcher_id:string! topic_id:string! topic_name:string! field_name:string subfield_name:string domain_name:string topic_rank:int! score:double! supporting_contribution_count:bigint! recent_supporting_contribution_count:bigint! latest_support_year:int evidence_strength:string! scoring_version:string! as_of_year:int!",
    "researcher_topic_evidence": "researcher_id:string! topic_id:string! contribution_id:string! publication_evidence_id:string! representative_work_id:string! source_topic_score:double!",
    "profile_quality_metrics": "researcher_id:string! candidate_count:bigint! pending_candidate_count:bigint! eligible_openalex_abstract_count:bigint! eligible_repository_description_count:bigint! eligible_missing_abstract_count:bigint! same_title_group_count:bigint! source_issue_codes:array<string>! coverage_warnings:array<string>!",
    "researcher_search_document_history": DOCUMENT_SPEC,
}
CONTROL_CONTRACTS = {
    "build_runs": "build_id:string! silver_build_id:string! transform_version:string! input_fingerprint:string! status:string! started_at:timestamp! validated_at:timestamp as_of_year:int! source_versions_json:string! counts_json:string document_count:bigint! error_message:string created_by:string!",
    "researcher_search_documents": "gold_build_id:string! silver_build_id:string! " + DOCUMENT_SPEC,
}


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def js(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=str)


def key(*parts):
    return hashlib.sha256(js(parts).encode()).hexdigest()


def plain(value, limit=None):
    text = " ".join(re.sub(r"<[^>]*>", " ", html.unescape(str(value or ""))).split())
    return text[:limit] if limit is not None else text


def usable_abstract(value):
    text = plain(value)
    if text.casefold().strip(" .") in ("", "no abstract available", "no abstract", "abstract unavailable", "not available", "n/a"):
        return None
    return text[:1500]


def fields(table):
    spec = CONTROL_CONTRACTS.get(table)
    if spec is None:
        spec = "build_id:string! row_key:string! silver_build_id:string! " + CONTRACTS[table]
    return [(name, kind.rstrip("!"), not kind.endswith("!"))
            for name, kind in (part.split(":", 1) for part in spec.split())]


def public_url(value):
    p = urlsplit(value or "")
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise ValueError("Invalid public evidence/profile URL")
    return value


class Gold:
    def __init__(self, build_id, silver_build_id):
        self.build_id, self.silver_build_id = build_id, silver_build_id
        self.tables = {t: {} for t in CONTRACTS}
        self.byte_count = 0
        self.row_count = 0

    def put(self, table, identity, **values):
        row = {"build_id": self.build_id, "row_key": key(table, identity),
               "silver_build_id": self.silver_build_id, **values}
        if set(row) != {name for name, _, _ in fields(table)}:
            raise ValueError("Gold column contract mismatch: " + table)
        for name, kind, nullable in fields(table):
            value = row[name]
            if value is None:
                if not nullable:
                    raise ValueError("Missing Gold field: " + table + "." + name)
                continue
            expected = {"string": str, "timestamp": datetime, "bigint": int, "int": int,
                        "double": float, "boolean": bool, "array<string>": list}[kind]
            if type(value) is not expected or (kind == "array<string>" and any(type(v) is not str for v in value)):
                raise ValueError("Wrong Gold field type: " + table + "." + name)
            if kind == "double" and not math.isfinite(value):
                raise ValueError("Non-finite Gold score")
        if row["row_key"] in self.tables[table]:
            raise ValueError("Duplicate Gold row: " + table)
        self.tables[table][row["row_key"]] = row
        self.byte_count += len(js(row).encode())
        self.row_count += 1
        if self.byte_count > MAX_OUTPUT_BYTES or self.row_count > MAX_OUTPUT_ROWS:
            raise ValueError("Gold output exceeds bounded pilot limits")
        return row

    def rows(self, table):
        return list(self.tables[table].values())


def unique(rows, columns):
    result = {}
    for row in rows:
        identity = tuple(row[c] for c in columns)
        if identity in result:
            raise ValueError("Duplicate Silver business key: " + ",".join(columns))
        result[identity] = row
    return result


def load_source(read, silver_build_id):
    data, total_rows, total_bytes = {}, 0, 0
    for table, columns in SOURCE_COLUMNS.items():
        data[table], keys = [], set()
        for source in read(table):
            if source["build_id"] != silver_build_id:
                raise ValueError("Mixed Silver build snapshots")
            if source["row_key"] in keys:
                raise ValueError("Duplicate Silver row_key: " + table)
            keys.add(source["row_key"])
            row = {c: source[c] for c in columns.split()}
            total_rows += 1
            total_bytes += len(js(row).encode())
            if total_rows > MAX_INPUT_ROWS or total_bytes > MAX_INPUT_BYTES:
                raise ValueError("Gold input exceeds bounded pilot limits")
            data[table].append(row)
    return data, {"rows": total_rows, "bytes": total_bytes}


def publication_order(row):
    return (-(row["publication_year"] or 0), row["title"].casefold(), row["contribution_id"])


def make_search_document(profile, publications, topics, evidence):
    """Deterministic extractive document. No biography, grant text, or OAI description.

    Include complete labeled sections only; record exactly the evidence included.
    This is retrieval input, not a claim that all supplied source assertions are true.
    """
    selected_topics = topics[:MAX_SEARCH_TOPICS]
    header = ("Researcher: " + plain(profile["display_name"], 120) + "\n"
              "ZU college codes (directory observed): " + ", ".join(profile["college_codes"]) + "\n"
              "Identity verified; current employment and collaboration availability not confirmed.\n"
              "Research topics inferred from eligible collected contributions; not a complete publication record.\n")
    topic_text = "; ".join(plain(t["topic_name"], 160) + " (" + str(t["supporting_contribution_count"])
                          + (" contribution; limited evidence" if t["supporting_contribution_count"] == 1 else " contributions")
                          + ")" for t in selected_topics)
    text = header + "Topics: " + topic_text + "\n"
    if len(text) > MAX_SEARCH_CHARS:
        raise ValueError("Gold search header exceeds text budget")
    by_id = {p["publication_evidence_id"]: p for p in publications}
    ordered_ids = []
    # Cover leading topics, then fill with recent evidence. This is presentation
    # selection, not query-specific relevance ranking or scientific quality ranking.
    for topic in selected_topics:
        choices = [by_id[e["publication_evidence_id"]] for e in evidence if e["topic_id"] == topic["topic_id"]]
        if choices:
            ordered_ids.append(min(choices, key=publication_order)["publication_evidence_id"])
    ordered_ids.extend(p["publication_evidence_id"] for p in sorted(publications, key=publication_order))
    included, seen = [], set()
    for pid in ordered_ids:
        if pid in seen:
            continue
        seen.add(pid)
        if len(included) >= MAX_SEARCH_PUBLICATIONS:
            break
        p = by_id[pid]
        block = ("\nPublication [" + pid + "]: " + plain(p["title"], 220)
                 + " | " + str(p["publication_year"] or "year unknown") + " | " + p["publication_type"])
        if p["is_preprint"]:
            block += " (preprint; peer review not established)"
        if p["abstract_excerpt"]:
            block += "\nOpenAlex abstract excerpt (source text): " + plain(p["abstract_excerpt"], 220)
        block += "\n"
        if len(text) + len(block) > MAX_SEARCH_CHARS:
            continue
        text += block
        included.append(pid)
    if not included:
        raise ValueError("Search document has no publication evidence within its text budget")
    return {"document_id": "researcher:" + profile["researcher_id"],
            "researcher_id": profile["researcher_id"], "display_name": profile["display_name"],
            "profile_url": profile["profile_url"], "college_codes_text": ",".join(profile["college_codes"]),
            "top_topics_text": "; ".join(plain(t["topic_name"], 160) for t in selected_topics),
            "search_text": text, "search_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "publication_evidence_ids": included, "topic_ids": [t["topic_id"] for t in selected_topics],
            "eligible_contribution_count": profile["eligible_contribution_count"],
            "eligible_preprint_count": profile["eligible_preprint_count"],
            "affiliation_status": profile["affiliation_status"], "funding_status": profile["funding_status"],
            "is_searchable": True}


def build_gold(read, silver_build_id, build_id="local", as_of_year=2026, expected_profiles=7):
    if not 1 <= expected_profiles <= 50 or not 2020 <= as_of_year <= 2100:
        raise ValueError("Gold pilot requires 1–50 expected profiles and a valid scoring year")
    data, totals = load_source(read, silver_build_id)
    g = Gold(build_id, silver_build_id)
    researchers = unique(data["researchers"], ["researcher_id"])
    candidates = unique(data["entity_resolution_review"], ["candidate_id"])
    verified = {cid: c for cid, c in candidates.items() if c["status"] == "verified" and c["decision_id"]}
    selected = {rid[0]: r for rid, r in researchers.items() if r["identity_status"] == "verified"}
    if len(selected) != expected_profiles:
        raise ValueError("Expected " + str(expected_profiles) + " verified profiles, found " + str(len(selected)))
    if {c["researcher_id"] for c in verified.values()} != set(selected):
        raise ValueError("Verified profile/candidate scope disagrees")
    if data["researcher_awards"]:
        raise ValueError("Gold v1 funding policy requires review before exposing new researcher-award attribution")
    works = unique(data["works"], ["work_id"])
    mappings = unique(data["work_contributions"], ["work_id"])
    source_links = unique(data["researcher_works"], ["researcher_id", "work_id"])
    contributions = unique(data["researcher_contributions"], ["researcher_id", "contribution_id"])
    topic_index = unique(data["topics"], ["topic_id"])
    work_topics = unique(data["work_topics"], ["work_id", "topic_id"])
    work_topics_by_work = defaultdict(list)
    for (wid, tid), wt in work_topics.items():
        work_topics_by_work[wid].append((tid, wt))
    score_index = unique(data["researcher_topic_scores"], ["researcher_id", "topic_id"])
    for (rid, wid), link in source_links.items():
        if rid not in selected or link["verification_status"] != "verified" or not link["candidate_ids"]:
            raise ValueError("Source authorship is outside verified profile scope")
        if any((cid,) not in verified or verified[(cid,)]["researcher_id"] != rid for cid in link["candidate_ids"]):
            raise ValueError("Source authorship lacks accepted identity evidence")
    grouped_members = defaultdict(set)
    for (rid, wid) in source_links:
        mapping = mappings.get((wid,))
        if not mapping:
            raise ValueError("Missing contribution mapping")
        grouped_members[(rid, mapping["contribution_id"])].add(wid)
    if set(contributions) != set(grouped_members):
        raise ValueError("Contribution coverage disagrees with verified source links")
    for identity, c in contributions.items():
        rid, cid = identity
        representative = c["representative_work_id"]
        m, w = mappings.get((representative,)), works.get((representative,))
        if (set(c["source_work_ids"]) != grouped_members[identity] or not m or not w
                or not m["is_representative"] or m["contribution_id"] != cid
                or c["publication_type"] != w["publication_type"]
                or any(mappings[(wid,)]["representative_work_id"] != representative for wid in c["source_work_ids"])):
            raise ValueError("Invalid contribution representative or members")
        if not c["eligible_for_expertise"]:
            continue
        link = source_links.get((rid, representative))
        if (not link or not link["eligible_for_expertise"] or not w["eligible_for_expertise"]
                or w["is_retracted"] or not plain(w["title"]) or "concept_doi_alias" in w["quality_flags"]):
            raise ValueError("Unsafe contribution entered Gold expertise evidence")

    for rid, researcher in sorted(selected.items()):
        own = [c for (r, _), c in contributions.items() if r == rid]
        eligible = [c for c in own if c["eligible_for_expertise"]]
        if not eligible:
            raise ValueError("Verified researcher has no eligible contribution: " + researcher["display_name"])
        publications = []
        for c in eligible:
            w = works[(c["representative_work_id"],)]
            wid = w["work_id"]
            evidence_url = ("https://doi.org/" + quote(w["doi"], safe="/") if w["doi"] else
                            "https://openalex.org/" + wid.removeprefix("oa:"))
            if not wid.startswith("oa:"):
                raise ValueError("Gold v1 requires OpenAlex-backed representative evidence")
            abstract = usable_abstract(w["abstract"])
            publications.append({"publication_evidence_id": key("publication", rid, c["contribution_id"]),
                "researcher_id": rid, "contribution_id": c["contribution_id"], "representative_work_id": wid,
                "source_work_ids": sorted(c["source_work_ids"]), "title": plain(w["title"]), "doi": w["doi"],
                "evidence_url": public_url(evidence_url), "publication_year": w["publication_year"],
                "publication_type": w["publication_type"] or "unknown", "is_preprint": w["publication_type"] == "preprint",
                "abstract_excerpt": abstract if w["abstract_source"] == "openalex" else None,
                "repository_description_excerpt": abstract if w["abstract_source"] == "oai_description" else None,
                "abstract_source": w["abstract_source"], "quality_flags": sorted(w["quality_flags"]),
                "source_record_keys": sorted(w["source_record_keys"])})
        publications.sort(key=publication_order)
        for rank, p in enumerate(publications, 1):
            p["evidence_rank"] = rank
            g.put("researcher_profile_publications", [rid, p["contribution_id"]], **p)
        support = defaultdict(list)
        for p in publications:
            wid = p["representative_work_id"]
            for tid, wt in work_topics_by_work[wid]:
                if not math.isfinite(wt["score"]) or not 0 <= wt["score"] <= 1:
                    raise ValueError("Invalid source topic score")
                support[tid].append(p)
                g.put("researcher_topic_evidence", [rid, tid, p["contribution_id"]], researcher_id=rid,
                      topic_id=tid, contribution_id=p["contribution_id"], publication_evidence_id=p["publication_evidence_id"],
                      representative_work_id=wid, source_topic_score=float(wt["score"]))
        own_scores = [s for (r, _), s in score_index.items() if r == rid]
        if {s["topic_id"] for s in own_scores} != set(support):
            raise ValueError("Topic scores lack complete eligible contribution evidence")
        own_scores.sort(key=lambda s: (-s["score"], s["topic_id"]))
        own_topics = []
        for rank, score in enumerate(own_scores, 1):
            tid = score["topic_id"]
            t = topic_index.get((tid,))
            if (not t or not plain(t["display_name"]) or score["supporting_work_count"] != len(support[tid])
                    or score["as_of_year"] != as_of_year or not math.isfinite(score["score"]) or score["score"] < 0
                    or score["scoring_version"] != "v2_contribution_half_life5_fractional_authorship"):
                raise ValueError("Stale/inconsistent contribution topic score")
            years = [p["publication_year"] for p in support[tid] if p["publication_year"] is not None]
            own_topics.append(g.put("researcher_profile_topics", [rid, tid], researcher_id=rid, topic_id=tid,
                topic_name=plain(t["display_name"]), field_name=t["field_name"], subfield_name=t["subfield_name"],
                domain_name=t["domain_name"], topic_rank=rank, score=float(score["score"]),
                supporting_contribution_count=len(support[tid]),
                recent_supporting_contribution_count=sum(as_of_year - 4 <= y <= as_of_year for y in years),
                latest_support_year=max(years) if years else None,
                evidence_strength="multiple_contributions" if len(support[tid]) >= 2 else "single_contribution",
                scoring_version=score["scoring_version"], as_of_year=as_of_year))
        own_candidates = [c for c in candidates.values() if c["researcher_id"] == rid]
        aids = sorted(c["openalex_author_id"] for c in own_candidates if c["status"] == "verified")
        ids = sorted(i["identifier_value"] for i in data["researcher_identifiers"] if i["researcher_id"] == rid
                     and i["identifier_type"] == "openalex" and i["verification_status"] == "verified")
        if aids != ids:
            raise ValueError("Verified author identifiers disagree with identity decisions")
        years = [p["publication_year"] for p in publications if p["publication_year"] is not None]
        preprints = sum(p["is_preprint"] for p in publications)
        topic_summary = "; ".join(t["topic_name"] for t in own_topics[:3]) or "No structured topic labels available"
        summary = (plain(researcher["display_name"]) + " has " + str(len(publications))
                   + " eligible collected contributions, including " + str(preprints) + " preprints. "
                   "Leading source-derived research topics: " + topic_summary + ". "
                   "Coverage is incomplete; topic scores are not confidence ratings. Funding history is not established.")
        profile = g.put("researcher_profiles", rid, researcher_id=rid, display_name=plain(researcher["display_name"]),
            profile_url=public_url(researcher["profile_url"]), college_codes=sorted(researcher["college_codes"]),
            profile_orcids=sorted(researcher["profile_orcids"]), verified_openalex_author_ids=aids,
            profile_observed_at=researcher["observed_at"], identity_status="verified", affiliation_status="directory_observed",
            linked_source_record_count=sum(r == rid for r, _ in source_links), contribution_count=len(own),
            eligible_contribution_count=len(publications), excluded_contribution_count=len(own) - len(publications),
            eligible_preprint_count=preprints, recent_contribution_count=sum(as_of_year - 4 <= y <= as_of_year for y in years),
            latest_publication_year=max(years) if years else None, funding_status="not_established",
            verified_award_count=None, profile_summary=summary, as_of_year=as_of_year)
        pending = sum(c["status"] == "pending" for c in own_candidates)
        issue_codes = sorted(set(researcher["quality_flags"]) | {q["issue_code"] for q in data["quality_issues"]
                               if q["entity_type"] == "researcher" and q["entity_id"] == rid})
        oa_abstracts = sum(bool(p["abstract_excerpt"]) for p in publications)
        descriptions = sum(bool(p["repository_description_excerpt"]) for p in publications)
        title_groups = defaultdict(set)
        for p in publications:
            title_groups[plain(p["title"]).casefold()].add(p["contribution_id"])
        same_titles = sum(len(group) > 1 for group in title_groups.values())
        warnings = ["incomplete_publication_coverage", "funding_not_established", "current_employment_not_verified",
                    "unreviewed_version_relationships_may_remain"]
        if same_titles:
            warnings.append("same_title_records_need_version_review")
        if pending:
            warnings.append("pending_identity_candidates")
        if preprints:
            warnings.append("preprints_included")
        if descriptions:
            warnings.append("repository_descriptions_excluded_from_search")
        if oa_abstracts + descriptions < len(publications):
            warnings.append("missing_abstracts")
        if any(y > as_of_year for y in years):
            warnings.append("future_dated_publication_metadata")
        g.put("profile_quality_metrics", rid, researcher_id=rid, candidate_count=len(own_candidates),
            pending_candidate_count=pending, eligible_openalex_abstract_count=oa_abstracts,
            eligible_repository_description_count=descriptions,
            eligible_missing_abstract_count=len(publications) - oa_abstracts - descriptions,
            same_title_group_count=same_titles,
            source_issue_codes=issue_codes, coverage_warnings=sorted(warnings))
        topic_evidence = [e for e in g.rows("researcher_topic_evidence") if e["researcher_id"] == rid]
        doc = make_search_document(profile, publications, own_topics, topic_evidence)
        g.put("researcher_search_document_history", doc["document_id"], **doc)
    validate_gold(g)
    return g, totals


def validate_gold(g):
    profiles = {r["researcher_id"]: r for r in g.rows("researcher_profiles")}
    publications = {p["publication_evidence_id"]: p for p in g.rows("researcher_profile_publications")}
    topics = {(t["researcher_id"], t["topic_id"]): t for t in g.rows("researcher_profile_topics")}
    docs = g.rows("researcher_search_document_history")
    if {d["researcher_id"] for d in docs} != set(profiles) or len(docs) != len(profiles):
        raise ValueError("Exactly one search document per Gold profile is required")
    for d in docs:
        rid = d["researcher_id"]
        if (not d["search_text"] or len(d["search_text"]) > MAX_SEARCH_CHARS or not d["is_searchable"]
                or hashlib.sha256(d["search_text"].encode()).hexdigest() != d["search_text_sha256"]
                or len(d["publication_evidence_ids"]) != len(set(d["publication_evidence_ids"]))
                or any(pid not in publications or publications[pid]["researcher_id"] != rid for pid in d["publication_evidence_ids"])
                or any((rid, tid) not in topics for tid in d["topic_ids"])):
            raise ValueError("Invalid search text/evidence references")
    support = defaultdict(set)
    for e in g.rows("researcher_topic_evidence"):
        p = publications.get(e["publication_evidence_id"])
        if not p or p["researcher_id"] != e["researcher_id"] or p["contribution_id"] != e["contribution_id"]:
            raise ValueError("Orphan topic publication evidence")
        support[(e["researcher_id"], e["topic_id"])].add(e["contribution_id"])
    if set(support) != set(topics) or any(len(support[k]) != t["supporting_contribution_count"] for k, t in topics.items()):
        raise ValueError("Topic evidence counts disagree")
    for rid, p in profiles.items():
        own = [pub for pub in publications.values() if pub["researcher_id"] == rid]
        if (len(own) != p["eligible_contribution_count"] or sum(pub["is_preprint"] for pub in own) != p["eligible_preprint_count"]
                or len({pub["contribution_id"] for pub in own}) != len(own)
                or p["funding_status"] != "not_established" or p["verified_award_count"] is not None):
            raise ValueError("Profile counts or funding policy disagree")

# COMMAND ----------

def qualified(catalog, schema, table):
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v) for v in (catalog, schema, table)):
        raise ValueError("Unsafe SQL identifier")
    return ".".join("`" + v + "`" for v in (catalog, schema, table))


def spark_schema(table):
    from pyspark.sql.types import StructType, StructField, StringType, LongType, IntegerType, DoubleType, BooleanType, TimestampType, ArrayType
    types = {"string": StringType(), "bigint": LongType(), "int": IntegerType(), "double": DoubleType(),
             "boolean": BooleanType(), "timestamp": TimestampType(), "array<string>": ArrayType(StringType(), False)}
    return StructType([StructField(n, types[t], nullable) for n, t, nullable in fields(table)])


def chunks(rows, max_rows=1000, max_bytes=2 * 1024 * 1024):
    batch, size = [], 0
    for row in rows:
        n = len(js(row).encode())
        if n > max_bytes:
            raise ValueError("Gold row exceeds write buffer")
        if batch and (len(batch) >= max_rows or size + n > max_bytes):
            yield batch
            batch, size = [], 0
        batch.append(row)
        size += n
    if batch:
        yield batch


def serving_rows(g):
    return [{"gold_build_id": r["build_id"], **{k: v for k, v in r.items() if k not in ("build_id", "row_key")}}
            for r in g.rows("researcher_search_document_history")]


def update_run(spark, target, run):
    view = "gold_run_" + uuid.uuid4().hex
    spark.createDataFrame([run], spark_schema("build_runs")).createOrReplaceTempView(view)
    try:
        spark.sql(f"MERGE INTO {target} t USING `{view}` s ON t.build_id = s.build_id WHEN MATCHED THEN UPDATE SET *").collect()
    finally:
        spark.catalog.dropTempView(view)


def write_build(spark, catalog, schema, g, run, assert_source_current):
    """Immutable snapshots first; validated log second; one atomic serving MERGE last.

    current_build follows the single build ID in the serving table, NOT the latest
    log timestamp. A failed/unfinished history build cannot publish partial profiles.
    No cross-table transaction is claimed. Run one producer at a time.
    """
    runs = qualified(catalog, schema, "build_runs")
    spark.createDataFrame([run], spark_schema("build_runs")).write.mode("append").saveAsTable(runs)
    try:
        for table in CONTRACTS:
            target = qualified(catalog, schema, table)
            for batch in chunks(g.rows(table)):
                spark.createDataFrame(batch, spark_schema(table)).write.mode("append").saveAsTable(target)
            actual = spark.sql(f"SELECT COUNT(*) AS n, COUNT(DISTINCT row_key) AS keys FROM {target} WHERE build_id = '{g.build_id}'").first()
            if actual["n"] != len(g.tables[table]) or actual["keys"] != len(g.tables[table]):
                raise ValueError("Gold persisted row/key check failed: " + table)
            print("Wrote and checked " + table + ": " + str(actual["n"]))
        assert_source_current()
        run.update(status="validated", validated_at=now())
        update_run(spark, runs, run)
        target = qualified(catalog, schema, "researcher_search_documents")
        view = "gold_documents_" + uuid.uuid4().hex
        spark.createDataFrame(serving_rows(g), spark_schema("researcher_search_documents")).createOrReplaceTempView(view)
        try:
            # This table is solely the current generated Gold search projection.
            # Remove obsolete generated documents; history remains recoverable.
            spark.sql(f"MERGE INTO {target} t USING `{view}` s ON t.document_id = s.document_id "
                      "WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT * "
                      "WHEN NOT MATCHED BY SOURCE THEN DELETE").collect()
        finally:
            spark.catalog.dropTempView(view)
    except Exception as exc:
        if run["status"] != "validated":
            run["status"] = "failed"
        # An uncertain serving MERGE can have committed. Never invalidate a fully
        # validated snapshot; current_build derives actual publication from serving.
        run["error_message"] = str(exc)[:4000]
        update_run(spark, runs, run)
        raise


def run_notebook(spark, dbutils):
    dbutils.widgets.text("catalog", "workspace")
    dbutils.widgets.text("silver_schema", "researchmatch_silver")
    dbutils.widgets.text("gold_schema", "researchmatch_gold")
    dbutils.widgets.dropdown("mode", "validate", ["validate", "build"])
    dbutils.widgets.text("expected_verified_profiles", "7")
    get = lambda name: dbutils.widgets.get(name).strip()
    catalog, silver, gold, mode = (get(n) for n in ("catalog", "silver_schema", "gold_schema", "mode"))
    expected = int(get("expected_verified_profiles"))
    if mode not in ("validate", "build") or not 1 <= expected <= 50 or silver == gold:
        raise ValueError("Use validate/build, 1–50 profiles and separate Silver/Gold schemas")
    for table in [*CONTRACTS, *CONTROL_CONTRACTS]:
        target = qualified(catalog, gold, table)
        if not spark.catalog.tableExists(target):
            raise ValueError("Run SQL 09 first: missing " + target)
        actual = {f.name: f.dataType.simpleString() for f in spark.table(target).schema.fields}
        if actual != {n: kind for n, kind, _ in fields(table)}:
            raise ValueError("Gold schema mismatch: " + target)
        detail = spark.sql("DESCRIBE DETAIL " + target).first()
        if detail["format"] != "delta":
            raise ValueError("Gold requires Delta tables")
        if table == "researcher_search_documents" and detail["properties"].get("delta.enableChangeDataFeed") != "true":
            raise ValueError("Search source requires Change Data Feed; run SQL 09")
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    current_silver = qualified(catalog, silver, "current_build")
    source = spark.sql("SELECT * FROM " + current_silver).first()
    if not source or source["transform_version"] != SILVER_VERSION:
        raise ValueError("Build and verify Silver v2 before Gold")
    sid, year = source["build_id"], source["as_of_year"]
    if not re.fullmatch(r"[a-f0-9-]{36}", sid):
        raise ValueError("Unexpected Silver build ID")
    decision_target = qualified(catalog, silver, "identity_decisions")
    source_decision_version = json.loads(source["source_versions_json"])["identity_decisions"]

    def assert_source_current():
        latest = spark.sql("SELECT build_id FROM " + current_silver).first()
        decision_version = int(spark.sql("DESCRIBE HISTORY " + decision_target + " LIMIT 1").first()["version"])
        if not latest or latest["build_id"] != sid or decision_version != source_decision_version:
            raise ValueError("Silver/identity decisions changed; finish Silver rebuild and rerun Gold")

    assert_source_current()
    versions = {t: int(spark.sql("DESCRIBE HISTORY " + qualified(catalog, silver, t) + " LIMIT 1").first()["version"])
                for t in SOURCE_COLUMNS}
    fingerprint = key(TRANSFORM_VERSION, catalog, silver, gold, sid, versions, year, expected,
                      MAX_SEARCH_CHARS, MAX_SEARCH_TOPICS, MAX_SEARCH_PUBLICATIONS)
    current_gold = qualified(catalog, gold, "current_build")
    current = spark.sql("SELECT * FROM " + current_gold).first()
    serving = qualified(catalog, gold, "researcher_search_documents")
    existing = spark.sql("SELECT COUNT(*) AS n FROM " + serving).first()["n"]
    if existing and not current:
        raise ValueError("Serving documents lack a coherent validated Gold build; inspect before replacing")
    if mode == "build" and current and current["input_fingerprint"] == fingerprint:
        print("Identical Gold build is already published: " + current["build_id"] + ". No writes performed.")
        return {"skipped": True, "build_id": current["build_id"]}

    def read(table):
        target = qualified(catalog, silver, table)
        columns = "build_id, row_key, " + ", ".join(SOURCE_COLUMNS[table].split())
        frame = spark.sql(f"SELECT {columns} FROM {target} VERSION AS OF {versions[table]} WHERE build_id = '{sid}'")
        if table == "works":
            frame = frame.repartition(8)  # Bound abstract-heavy iterator partitions on serverless.
        for row in frame.limit(MAX_INPUT_ROWS + 1).toLocalIterator():
            yield row.asDict(recursive=True)

    g, input_totals = build_gold(read, sid, str(uuid.uuid4()), year, expected)
    assert_source_current()
    counts = {t: len(g.tables[t]) for t in CONTRACTS}
    summary = {"mode": mode, "silver_build_id": sid, "gold_build_id": g.build_id,
               "input_fingerprint": fingerprint, "input": input_totals, "counts": counts,
               "search_documents": counts["researcher_search_document_history"],
               "search_text_max_characters": max(len(d["search_text"]) for d in g.rows("researcher_search_document_history")),
               "output_estimated_bytes": g.byte_count}
    print(json.dumps(summary, indent=2))
    if mode == "validate":
        print("Gold validation passed. No tables or run logs written; no AI Search endpoint created.")
        return summary
    run = {"build_id": g.build_id, "silver_build_id": sid, "transform_version": TRANSFORM_VERSION,
           "input_fingerprint": fingerprint, "status": "started", "started_at": now(), "validated_at": None,
           "as_of_year": year, "source_versions_json": js(versions), "counts_json": js(counts),
           "document_count": summary["search_documents"], "error_message": None,
           "created_by": spark.sql("SELECT current_user() AS user").first()["user"]}
    write_build(spark, catalog, gold, g, run, assert_source_current)
    published = spark.sql("SELECT build_id FROM " + current_gold).first()
    if not published or published["build_id"] != g.build_id:
        raise ValueError("Gold publication check failed; inspect serving table and build_runs before retrying")
    print("Gold build published: " + g.build_id + ". Run SQL 11. AI Search has not been provisioned or synced.")
    return summary


if "spark" in globals() and "dbutils" in globals():
    gold_result = run_notebook(spark, dbutils)
