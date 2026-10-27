"""Mirrored notebook 14 policy/read helpers. AST parity is tested; no notebook execution."""
# No notebook header/cell markers: Databricks would convert this file to a
# notebook in the app snapshot, making it unimportable.

from collections import defaultdict
import hashlib
import json
import math
import re
import unicodedata
from urllib.parse import quote

# Read/verify helpers mirrored from notebook 12 for standalone Workspace import.
# Tests compare their ASTs to prevent drift. Notebook 12 is NOT executed/imported here.
MODELS = {
    "databricks-gte-large-en": {"window_tokens": 8192, "dimension": 1024, "pilot_supported": True},
    "databricks-qwen3-embedding-0-6b": {"window_tokens": 32768, "dimension": 1024, "pilot_supported": True},
    "databricks-bge-large-en": {"window_tokens": 512, "dimension": 1024, "pilot_supported": False},
}

SYNC_COLUMNS = ["document_id", "researcher_id", "display_name", "profile_url", "search_text",
                "search_text_sha256", "gold_build_id", "silver_build_id", "college_codes_text",
                "top_topics_text", "eligible_contribution_count", "eligible_preprint_count",
                "affiliation_status", "funding_status", "is_searchable"]

RESULT_COLUMNS = ["document_id", "researcher_id", "display_name", "gold_build_id",
                  "silver_build_id", "search_text_sha256"]

MAX_PROFILES = 50  # Upper bound, as in Gold; the actual count comes from the build.

BASE = "/api/2.0/vector-search"

def sql_name(*parts):
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", p) for p in parts):
        raise ValueError("Invalid catalog/schema/table identifier")
    return ".".join("`" + p + "`" for p in parts)

def config(catalog="workspace", silver="researchmatch_silver", gold="researchmatch_gold",
           endpoint="researchmatch-search", model="auto"):
    sql_name(catalog, silver, gold)
    if silver == gold or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}", endpoint):
        raise ValueError("Use separate schemas and a valid endpoint name")
    if model != "auto" and (model not in MODELS or not MODELS[model]["pilot_supported"]):
        raise ValueError("Use auto, databricks-gte-large-en or databricks-qwen3-embedding-0-6b; BGE is too short")
    return {"catalog": catalog, "silver": silver, "gold": gold, "endpoint": endpoint, "model": model,
            "source": f"{catalog}.{gold}.researcher_search_documents",
            "index": f"{catalog}.{gold}.researcher_profiles_index"}

def api_call(w, method, path, body=None, query=None):
    # WorkspaceClient handles notebook authentication. Never print credentials/headers.
    kwargs = {"method": method, "path": path}
    if body is not None:
        kwargs["body"] = body
    if query is not None:
        kwargs["query"] = query
    return w.api_client.do(**kwargs) or {}

def index_path(cfg, suffix=""):
    return BASE + "/indexes/" + quote(cfg["index"], safe="") + suffix

def validate_documents(docs, gold_build, silver_build, expected):
    # expected = the published Gold build's document_count; MAX_PROFILES is the hard upper bound.
    if (type(expected) is not int or not 1 <= expected <= MAX_PROFILES
            or len(docs) != expected or len({d["document_id"] for d in docs}) != expected):
        raise ValueError("Gold search documents do not match the published build's document count")
    if len({d["researcher_id"] for d in docs}) != expected:
        raise ValueError("Gold researcher IDs must be unique")
    for d in docs:
        text = d["search_text"]
        if (d["gold_build_id"] != gold_build or d["silver_build_id"] != silver_build
                or d["document_id"] != "researcher:" + d["researcher_id"]
                or d["is_searchable"] is not True or not text or len(text) > 6000
                or len(text.encode("utf-8")) > 32764
                or hashlib.sha256(text.encode()).hexdigest() != d["search_text_sha256"]
                or d["funding_status"] != "not_established"
                or d["affiliation_status"] != "directory_observed"):
            raise ValueError("Gold document lineage/content/policy validation failed")

def source_snapshot(spark, cfg):
    cat, silver, gold = (cfg[k] for k in ("catalog", "silver", "gold"))
    def one(table):
        rows = spark.sql("SELECT * FROM " + table).limit(2).collect()
        if len(rows) != 1:
            raise ValueError("Expected exactly one published build: " + table)
        return rows[0].asDict(recursive=True)
    g = one(sql_name(cat, gold, "current_build"))
    s = one(sql_name(cat, silver, "current_build"))
    if (g["silver_build_id"] != s["build_id"] or g["status"] != "validated"
            or g["transform_version"] != "gold_v1_verified_contributions"
            or s["transform_version"] != "silver_v2_concept_versions"):
        raise ValueError("Gold is stale or unsupported; rebuild/verify Silver then Gold")
    for value in (g["build_id"], s["build_id"]):
        if not re.fullmatch(r"[a-f0-9-]{36}", value):
            raise ValueError("Unexpected build ID")
    decisions = sql_name(cat, silver, "identity_decisions")
    dv = int(spark.sql("DESCRIBE HISTORY " + decisions + " LIMIT 1").first()["version"])
    if dv != json.loads(s["source_versions_json"])["identity_decisions"]:
        raise ValueError("Identity decisions changed: rebuild Silver and Gold first")
    table = sql_name(cat, gold, "researcher_search_documents")
    detail = spark.sql("DESCRIBE DETAIL " + table).first()
    if detail["format"] != "delta" or detail["properties"].get("delta.enableChangeDataFeed") != "true":
        raise ValueError("Gold source must be Delta with Change Data Feed; check SQL 09")
    version = int(spark.sql("DESCRIBE HISTORY " + table + " LIMIT 1").first()["version"])
    docs = [r.asDict(recursive=True) for r in spark.sql(
        "SELECT " + ", ".join(SYNC_COLUMNS) + f" FROM {table} VERSION AS OF {version}"
    ).limit(MAX_PROFILES + 1).collect()]
    validate_documents(docs, g["build_id"], s["build_id"], g["document_count"])
    return {"gold_build_id": g["build_id"], "silver_build_id": s["build_id"],
            "source_version": version, "identity_version": dv, "documents": docs}

def assert_unchanged(spark, cfg, snapshot):
    latest = source_snapshot(spark, cfg)
    if latest != snapshot:
        # Compare rows independent of Spark row order.
        a, b = dict(latest), dict(snapshot)
        a["documents"] = sorted(a["documents"], key=lambda d: d["document_id"])
        b["documents"] = sorted(b["documents"], key=lambda d: d["document_id"])
        if a != b:
            raise ValueError("Gold/Silver changed during this operation; do not use these results")

def choose_model(requested, listed, index=None):
    existing = None
    if index:
        columns = index.get("delta_sync_index_spec", {}).get("embedding_source_columns", [])
        if len(columns) != 1:
            raise ValueError("Existing index has unexpected embedding configuration")
        existing = columns[0].get("embedding_model_endpoint_name")
    selected = existing if requested == "auto" and existing else requested
    if selected == "auto":
        selected = next((n for n in MODELS if MODELS[n]["pilot_supported"] and n in listed), None)
    if selected not in MODELS or not MODELS[selected]["pilot_supported"]:
        raise ValueError("No compatible listed model. Check Serving/UI; never substitute BGE or create custom serving")
    if existing and selected != existing:
        raise ValueError("Existing index uses another model; no automatic model change or index replacement")
    # Explicit documented names can be probed when list visibility is incomplete.
    return selected

def index_spec(cfg, model):
    return {"name": cfg["index"], "endpoint_name": cfg["endpoint"], "primary_key": "document_id",
            "index_type": "DELTA_SYNC", "delta_sync_index_spec": {
                "source_table": cfg["source"], "pipeline_type": "TRIGGERED",
                "embedding_source_columns": [{"name": "search_text", "embedding_model_endpoint_name": model}],
                "columns_to_sync": list(SYNC_COLUMNS)}}

def validate_index(index, cfg, model):
    expected = index_spec(cfg, model)
    if any(index.get(k) != expected[k] for k in ("name", "endpoint_name", "primary_key", "index_type")):
        raise ValueError("Existing index ownership/configuration mismatch; nothing will be replaced")
    spec, wanted = index.get("delta_sync_index_spec", {}), expected["delta_sync_index_spec"]
    if (any(spec.get(k) != wanted[k] for k in ("source_table", "pipeline_type"))
            or len(spec.get("embedding_source_columns", [])) != 1):
        raise ValueError("Index must be triggered Delta Sync over the exact Gold source")
    embedding = spec["embedding_source_columns"][0]
    if (embedding.get("name") != "search_text" or embedding.get("embedding_model_endpoint_name") != model
            or embedding.get("model_endpoint_name_for_query", model) not in (None, model)
            or spec.get("embedding_writeback_table")):
        raise ValueError("Unexpected index embedding/query/writeback configuration")
    # GET can omit these optional fields even for a successfully created index.
    # Missing configuration metadata is unknown, not proof of missing indexed data.
    # Ready indexes must still pass scan_matches before status/test can succeed.
    declared = [spec[field] for field in ("columns_to_sync", "columns_to_index")
                if spec.get(field) is not None]
    required = set(SYNC_COLUMNS) - {"document_id", "search_text"}
    for synced in declared:
        if (not isinstance(synced, list) or any(not isinstance(c, str) for c in synced)
                or not required.issubset(synced)):
            raise ValueError("Index reports an incomplete or invalid lineage/hash column list; inspect configuration")
    if not declared:
        print("Index description omits the optional synced-column list; actual indexed columns and values will be checked by the content scan before status/test succeeds.")

def endpoint_online(endpoint):
    state = endpoint.get("endpoint_status", {}).get("state")
    if state in ("OFFLINE", "FAILED", "DELETING"):
        raise ValueError("Endpoint is " + state + "; inspect Compute > AI Search")
    return state == "ONLINE"

def decode_value(value):
    if not isinstance(value, dict):
        return value
    for kind in ("string_value", "number_value", "bool_value"):
        if kind in value:
            return value[kind]
    if not value or "null_value" in value:
        return None
    raise ValueError("Unexpected scan value encoding for a scalar metadata field")

def scan_matches(w, cfg, docs):
    # Seven rows: request eight to detect unexpected extra documents. No vector printing.
    response = api_call(w, "POST", index_path(cfg, "/scan"), body={"num_results": MAX_PROFILES + 1})
    rows = []
    for entry in response.get("data", []):
        row = {}
        for f in entry.get("fields", []):
            if f["key"] in SYNC_COLUMNS:
                if f["key"] in row:
                    raise ValueError("Duplicate index scan field")
                row[f["key"]] = decode_value(f.get("value"))
        rows.append(row)
    if len(rows) != len(docs) or len({r.get("document_id") for r in rows}) != len(docs):
        raise ValueError("Index scan has missing/extra/duplicate rows; wait for sync then check again")
    expected = {d["document_id"]: d for d in docs}
    for row in rows:
        missing = sorted(set(SYNC_COLUMNS) - set(row))
        if missing:
            raise ValueError("Index scan is missing required columns: " + ", ".join(missing)
                             + ". Stop and inspect the index; no search tests were run.")
        original = expected.get(row.get("document_id"))
        if not original or any(row[k] != original[k] for k in SYNC_COLUMNS):
            raise ValueError("Index content is stale or differs from Gold; run sync and recheck")
    return True

def validate_results(rows, docs):
    expected = {d["document_id"]: d for d in docs}
    if len({r["document_id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate researcher search results")
    for row in rows:
        original = expected.get(row["document_id"])
        if not original or any(row[k] != original[k] for k in RESULT_COLUMNS):
            raise ValueError("Search returned stale or non-pilot identity/content")


MATCH_POLICY = "matching_v2_direct_evidence"
MIN_ANCHOR_COVERAGE = 0.60  # Applies to direct title/abstract terms ONLY; provisional partial-match gate.
EVIDENCE_COLUMNS = {
    "researcher_profile_publications": "researcher_id publication_evidence_id contribution_id representative_work_id title abstract_excerpt publication_year publication_type is_preprint evidence_url quality_flags",
    "researcher_profile_topics": "researcher_id topic_id topic_name supporting_contribution_count",
    "researcher_topic_evidence": "researcher_id topic_id contribution_id publication_evidence_id representative_work_id",
}
EVIDENCE_LIMITS = {"researcher_profile_publications": 5000, "researcher_profile_topics": 7500,
                   "researcher_topic_evidence": 25000}
STOP = set("a an and are as at be by can could do does find for from give has have how i in interested is it me my of on or our please researcher researchers research seeking some someone suitable team teams that the their them these they this to topic up us use using want we what which who will with work working would you your looking collaborator collaborators collaborate study studies based need help about into investigating researching focus focused".split())
GENERIC = set("application applications approach approaches analysis data framework frameworks model models method methods practice practices process processes science scientific system systems technique techniques technology technologies tool tools new improve improving investigate".split())
BROAD = set("artificial_intelligence machine_learning higher_education learning education digital information security network detection prediction development performance management planning medical image teaching".split())
INFLECTIONS = {"forensics": "forensic", "networks": "network", "images": "image", "tumours": "tumor",
               "tumour": "tumor", "tumors": "tumor", "vehicles": "vehicle", "careers": "career",
               "employees": "employee", "organisational": "organizational", "behaviour": "behavior",
               "behaviours": "behavior", "behaviors": "behavior", "applications": "application"}
PHRASES = {"artificial intelligence": "artificial_intelligence", "ai": "artificial_intelligence",
           "machine learning": "machine_learning", "ml": "machine_learning",
           "higher education": "higher_education", "internet of things": "iot"}
SMOKE_CASES = [
    ("blockchain", "Blockchain applications for information security"),
    ("forensics", "Digital forensics and malware detection"),
    ("medical_imaging", "Machine learning for brain tumor detection in medical images"),
    ("adoption", "Technology adoption and organizational employee performance"),
    ("transport", "Traffic prediction and transportation planning for autonomous vehicles"),
    ("employability", "Higher education employability and career development"),
    ("education", "Reflective teaching practices and learning in higher education"),
    ("astronomy", "Exoplanet atmospheric spectroscopy and stellar nucleosynthesis"),
    ("generic", "Artificial intelligence and machine learning"),
    ("mixed_domain", "Exoplanet detection using machine learning"),
    ("negation", "Malware detection without digital forensics"),
    ("paraphrase", "Investigating malicious software and digital forensic evidence"),
]


def evidence_terms(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9_]+", " ", text)
    for phrase, canonical in PHRASES.items():
        text = re.sub(r"\b" + re.escape(phrase) + r"\b", canonical, text)
    return {INFLECTIONS.get(t, t) for t in text.split() if len(t) > 2 and t not in STOP | GENERIC}


def title_key(title):
    # Conservative display/bonus discount, NOT an assertion that records are versions.
    # Preserve all words/numbers; no stopword removal, stemming or fuzzy matching.
    normalized = unicodedata.normalize("NFKC", title).casefold()
    return " ".join(re.findall(r"[^\W_]+", normalized, re.UNICODE))


def query_plan(query, required_terms=""):
    query = " ".join(query.split())
    if not 3 <= len(query) <= 500 or len(query.encode()) > 2000:
        raise ValueError("Enter a research topic of 3–500 characters (at most 2,000 UTF-8 bytes)")
    terms = evidence_terms(query)
    if re.search(r"\b(not|without|exclude|excluding|except)\b|rather than", query, re.I):
        return {"query": query, "status": "needs_refinement", "message": "Negation/exclusions are not supported by this pilot. State the positive research topic explicitly."}
    if len(terms) > 24:
        return {"query": query, "status": "needs_refinement", "message": "Narrow this to one research topic with at most 24 meaningful terms."}
    required = evidence_terms(required_terms)
    if required_terms.strip() and (not required or not required.issubset(terms)):
        raise ValueError("Required terms must be meaningful terms already present in the topic")
    anchors = terms - BROAD
    if not anchors:
        return {"query": query, "status": "needs_refinement", "message": "Add a specific subject or application area, such as malware, brain tumors or traffic—not just a broad method."}
    return {"query": query, "status": "ready", "terms": terms, "anchors": anchors, "required": required}


def prepare_evidence(docs, data, gold_build, silver_build):
    """Validate Gold evidence joins; never trust cross-researcher or mixed-build links."""
    by_researcher = {d["researcher_id"]: d for d in docs}
    pubs, topics, grouped = {}, {}, defaultdict(list)
    for table in EVIDENCE_COLUMNS:
        rows = data[table]
        if len(rows) > EVIDENCE_LIMITS[table]:
            raise ValueError("Evidence exceeds bounded pilot limit: " + table)
        keys = set()
        for row in rows:
            if (row["build_id"] != gold_build or row["silver_build_id"] != silver_build
                    or row["researcher_id"] not in by_researcher or row["row_key"] in keys):
                raise ValueError("Invalid evidence snapshot/researcher/row key")
            keys.add(row["row_key"])
    groups = set()
    for p in data["researcher_profile_publications"]:
        pid, rid = p["publication_evidence_id"], p["researcher_id"]
        group = (rid, p["contribution_id"])
        if (pid in pubs or group in groups or not p["title"].strip()
                or {"retracted", "concept_doi_alias"} & set(p["quality_flags"])
                or p["is_preprint"] != (p["publication_type"] == "preprint")
                or not re.match(r"https?://", p["evidence_url"])):
            raise ValueError("Unsafe or duplicate publication evidence")
        groups.add(group)
        pubs[pid] = p
    for t in data["researcher_profile_topics"]:
        key = (t["researcher_id"], t["topic_id"])
        if key in topics or not t["topic_name"].strip():
            raise ValueError("Duplicate/empty topic evidence")
        topics[key] = t
    linked, support = set(), defaultdict(set)
    for e in data["researcher_topic_evidence"]:
        p = pubs.get(e["publication_evidence_id"])
        tk = (e["researcher_id"], e["topic_id"])
        ek = (tk, e["publication_evidence_id"])
        if (not p or tk not in topics or ek in linked or p["researcher_id"] != e["researcher_id"]
                or p["contribution_id"] != e["contribution_id"]
                or p["representative_work_id"] != e["representative_work_id"]):
            raise ValueError("Orphan, conflicting or duplicate topic-publication link")
        linked.add(ek)
        support[tk].add(p["contribution_id"])
        grouped[e["publication_evidence_id"]].append(topics[tk])
    if set(support) != set(topics) or any(len(support[k]) != t["supporting_contribution_count"] for k, t in topics.items()):
        raise ValueError("Topic support counts disagree with publication links")
    title_groups = defaultdict(list)
    for p in pubs.values():
        normalized = title_key(p["title"])
        # Punctuation-only titles must not collapse into a shared empty bucket.
        group_key = (p["researcher_id"], normalized or p["publication_evidence_id"])
        title_groups[group_key].append(p)
    possible_versions, display_groups = [], {}
    for (rid, normalized), members in sorted(title_groups.items()):
        members.sort(key=lambda p: p["publication_evidence_id"])
        group_id = hashlib.sha256(json.dumps([rid, normalized], ensure_ascii=False).encode()).hexdigest()
        refs = [{"publication_evidence_id": p["publication_evidence_id"], "contribution_id": p["contribution_id"],
                 "title": p["title"], "url": p["evidence_url"], "publication_type": p["publication_type"],
                 "is_preprint": p["is_preprint"]} for p in members]
        if len(members) > 1:
            possible_versions.append({"researcher_id": rid, "display_name": by_researcher[rid]["display_name"],
                                      "display_group_id": group_id,
                                      "status": "same_title_relationship_unverified", "records": refs})
        for p in members:
            display_groups[p["publication_evidence_id"]] = (group_id, refs if len(members) > 1 else [])
    corpus, by_person = set(), defaultdict(list)
    for pid, p in pubs.items():
        title = evidence_terms(p["title"])
        abstract = evidence_terms(p["abstract_excerpt"] or "")
        ts = sorted(grouped[pid], key=lambda t: t["topic_id"])
        topic_terms = set().union(*(evidence_terms(t["topic_name"]) for t in ts))
        combined = title | abstract | topic_terms
        # Topic classification remains review context, never subject evidence.
        corpus |= title | abstract
        display_group, possible = display_groups[pid]
        by_person[p["researcher_id"]].append({"publication": p, "topics": ts, "title_terms": title,
                                              "abstract_terms": abstract, "topic_terms": topic_terms,
                                              "combined_terms": combined, "direct_terms": title | abstract,
                                              "display_group_id": display_group, "possible_versions": possible})
    for rid, doc in by_researcher.items():
        own = by_person[rid]
        if (len(own) != doc["eligible_contribution_count"]
                or sum(x["publication"]["is_preprint"] for x in own) != doc["eligible_preprint_count"]):
            raise ValueError("Gold publication counts disagree with profile metadata")
    return {"documents": by_researcher, "by_person": by_person, "corpus_terms": corpus,
            "possible_version_groups": possible_versions}


def screen_query(plan, evidence):
    if plan["status"] != "ready":
        return plan["status"], plan["message"]
    unsupported = (plan["anchors"] | plan["required"]) - evidence["corpus_terms"]
    if unsupported:
        return "insufficient_evidence", ("No collected title/abstract evidence for query terms: " + ", ".join(sorted(unsupported))
                                         + ". This is a lexical coverage limit, not proof no researcher has that expertise.")
    return "ready", ""


def contribution_match(plan, item):
    anchors, terms = plan["anchors"], plan["terms"]
    available = item["direct_terms"]
    hit = terms & available
    anchor_hit = anchors & available
    coverage = len(anchor_hit) / len(anchors)
    direct = anchors & item["title_terms"]
    # Require a title subject signal, direct anchor coverage and direct required
    # terms in THIS contribution. Topic labels cannot complete the request.
    if (not direct or coverage < MIN_ANCHOR_COVERAGE or len(hit) < min(2, len(terms))
            or not plan["required"].issubset(available)):
        return None
    p = item["publication"]
    matched_topics = [{"topic_id": t["topic_id"], "topic_name": t["topic_name"],
                       "matched_terms": sorted(terms & evidence_terms(t["topic_name"]))}
                      for t in item["topics"] if terms & evidence_terms(t["topic_name"])]
    return {"publication_evidence_id": p["publication_evidence_id"], "contribution_id": p["contribution_id"],
            "title": p["title"], "url": p["evidence_url"], "publication_year": p["publication_year"],
            "publication_type": p["publication_type"], "is_preprint": p["is_preprint"],
            "abstract_excerpt": p["abstract_excerpt"] or "",
            "matched_terms": sorted(hit), "missing_query_terms": sorted(terms - available),
            "matched_in_title": sorted(terms & item["title_terms"]),
            "matched_in_abstract": sorted(terms & item["abstract_terms"]),
            "anchor_terms_in_title": sorted(anchors & item["title_terms"]),
            "topic_only_anchor_terms": sorted((anchors & item["topic_terms"]) - available),
            "topic_only_query_terms": sorted((terms & item["topic_terms"]) - available),
            "topic_context_not_used_for_scoring": matched_topics, "anchor_coverage": coverage,
            "query_coverage": len(hit) / len(terms),
            "evidence_scope": "direct_query_terms" if terms.issubset(available) else "partial_direct_terms",
            "display_group_id": item["display_group_id"],
            "possible_versions": item["possible_versions"],
            "version_relationship_status": "unverified_same_title" if item["possible_versions"] else "not_assessed"}


def evidence_order(support):
    return (-support["query_coverage"], -support["anchor_coverage"], -len(support["anchor_terms_in_title"]),
            support["is_preprint"], -len(support["matched_in_title"]),
            -(support["publication_year"] or 0), support["publication_evidence_id"])


def select_display_groups(supports):
    # Keep one independently qualifying record per exact-title display group.
    # Do not union terms across versions or transfer another record's provenance.
    selected = {}
    for s in sorted(supports, key=evidence_order):
        selected.setdefault(s["display_group_id"], s)
    return sorted(selected.values(), key=evidence_order)


def match_candidates(plan, retrieved, evidence, snapshot, top_k=3):
    if not 1 <= top_k <= 3:
        raise ValueError("Pilot returns 1–3 candidates")
    validate_results(retrieved, snapshot["documents"])
    state, message = screen_query(plan, evidence)
    output = {"policy_version": MATCH_POLICY, "gold_build_id": snapshot["gold_build_id"],
              "silver_build_id": snapshot["silver_build_id"], "query": plan["query"],
              "status": state, "message": message, "candidates": [], "partial_candidates": [],
              "scope": "Seven verified pilot researchers; incomplete collected research evidence.",
              "limitations": ["Uncalibrated English lexical screening; paraphrases/negation can be missed.",
                              "Scores are not confidence, scientific quality or confirmed suitability.",
                              "Current employment/availability and funding history are not established.",
                              "Direct means lexical title/abstract support, not scientific validation.",
                              "Same-title display discounts do not verify or merge publication versions."]}
    if state != "ready":
        return output
    ranked, partial = [], []
    for retrieval_rank, row in enumerate(retrieved, 1):
        rid = row["researcher_id"]
        supports = [s for item in evidence["by_person"][rid] if (s := contribution_match(plan, item))]
        if not supports:
            continue
        full = [s for s in supports if s["evidence_scope"] == "direct_query_terms"]
        # Full and partial matches are never mixed into the same evidence count/list.
        tier = full or supports
        grouped_supports = select_display_groups(tier)
        best = grouped_supports[0]
        score = (.35 * len(best["anchor_terms_in_title"]) / len(plan["anchors"])
                 + .35 * best["anchor_coverage"] + .2 * best["query_coverage"]
                 + .1 * min(len(grouped_supports), 3) / 3)
        doc = evidence["documents"][rid]
        shown = grouped_supports[:3]
        # A single contribution must meet the gate; unrelated papers cannot be
        # stitched together to manufacture joint expertise for a compound topic.
        destination = ranked if full else partial
        destination.append({"researcher_id": rid, "display_name": doc["display_name"], "profile_url": doc["profile_url"],
                       "relevance_score_heuristic": round(score, 4), "retrieval_rank": retrieval_rank,
                       "qualifying_source_contribution_count": len(tier),
                       "support_group_count_for_ranking": len(grouped_supports),
                       "suspected_duplicate_records_discounted": len(tier) - len(grouped_supports),
                       "evidence_scope": best["evidence_scope"],
                       "best_evidence_missing_terms": best["missing_query_terms"],
                       "reason": ("All interpreted query terms occur in a single eligible title/abstract; review scientific relevance."
                                  if full else "Partial direct evidence only; missing terms are not supplied by topic labels. Not a full-query recommendation."),
                       "evidence": shown, "preprint_evidence_present": any(s["is_preprint"] for s in shown),
                       "possible_versions_include_preprint": any(p["is_preprint"] for s in shown for p in s["possible_versions"]),
                       "funding_status": doc["funding_status"], "affiliation_status": doc["affiliation_status"]})
    ranked.sort(key=lambda r: (-r["relevance_score_heuristic"], r["retrieval_rank"], r["researcher_id"]))
    partial.sort(key=lambda r: (-r["relevance_score_heuristic"], r["retrieval_rank"], r["researcher_id"]))
    output.update(status="candidates_for_review" if ranked else "partial_evidence_only" if partial else "insufficient_evidence",
                  message="Direct lexical evidence found; review citations, not scores, for scientific relevance." if ranked else
                          "Only partial evidence found; no full-query recommendations." if partial else
                          "Insufficient direct evidence among the retrieved pilot researchers under this screening policy.",
                  candidates=ranked[:top_k], partial_candidates=partial[:top_k], interpreted_terms=sorted(plan["terms"]),
                  anchor_terms=sorted(plan["anchors"]), required_terms=sorted(plan["required"]),
                  retrieved_candidate_count=len(retrieved))
    return output


def load_evidence(spark, cfg, snapshot):
    data, versions, size = {}, {}, 0
    for table, columns in EVIDENCE_COLUMNS.items():
        target = sql_name(cfg["catalog"], cfg["gold"], table)
        version = int(spark.sql("DESCRIBE HISTORY " + target + " LIMIT 1").first()["version"])
        versions[table] = version
        frame = spark.sql("SELECT build_id, silver_build_id, row_key, " + ", ".join(columns.split())
                          + f" FROM {target} VERSION AS OF {version} WHERE build_id = '{snapshot['gold_build_id']}'")
        data[table] = [r.asDict(recursive=True) for r in frame.limit(EVIDENCE_LIMITS[table] + 1).collect()]
        size += len(json.dumps(data[table], ensure_ascii=False).encode())
        if size > 16 * 1024 * 1024:
            raise ValueError("Matching evidence exceeds 16 MiB pilot guard")
    return prepare_evidence(snapshot["documents"], data, snapshot["gold_build_id"], snapshot["silver_build_id"]), versions


def retrieve_candidates(w, cfg, snapshot, query, query_type):
    if query_type not in ("ANN", "HYBRID"):
        raise ValueError("Use ANN or HYBRID")
    response = api_call(w, "POST", index_path(cfg, "/query"), body={
        "query_text": query, "query_type": query_type, "num_results": MAX_PROFILES,
        "columns": RESULT_COLUMNS, "filters_json": json.dumps({"gold_build_id": snapshot["gold_build_id"], "is_searchable": True})})
    columns = [c["name"] for c in response.get("manifest", {}).get("columns", [])]
    rows = response.get("result", {}).get("data_array", [])
    if (len(columns) != len(set(columns)) or not set(RESULT_COLUMNS).issubset(columns)
            or len(rows) > MAX_PROFILES or any(len(r) != len(columns) for r in rows)
            or response.get("next_page_token")):
        raise ValueError("Unexpected candidate response; cannot validate retrieval")
    result = [dict(zip(columns, r)) for r in rows]
    validate_results(result, snapshot["documents"])
    return result


def run_match(w, cfg, snapshot, evidence, query, query_type="HYBRID", required_terms="", top_k=3):
    plan = query_plan(query, required_terms)
    state, _ = screen_query(plan, evidence)
    rows = retrieve_candidates(w, cfg, snapshot, plan["query"], query_type) if state == "ready" else []
    result = match_candidates(plan, rows, evidence, snapshot, top_k)
    result["retrieval_type"] = query_type
    result["inference_queries"] = 1 if state == "ready" else 0
    return result
