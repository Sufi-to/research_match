# Databricks notebook source
# MAGIC %md
# MAGIC # ResearchMatch AI Search — seven verified researchers
# MAGIC Default `discover` only lists resources and checks Gold. No inference or writes.
# MAGIC Run All. Set widget execution behavior to **Do Nothing** before changing mode.
# MAGIC Modes: discover → probe → create → status → test. `sync` is for later Gold refreshes.
# MAGIC Uses the installed Databricks SDK and notebook authentication, not an API key.
# MAGIC No automatic installation, manual token setup, permission changes, deletion or paid upgrade.
# MAGIC See docs/ai_search_runbook.md. Live operations consume Free Edition allowance.

# COMMAND ----------

import hashlib
import json
import math
import re
from urllib.parse import quote

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
MAX_PROFILES = 7  # Deliberately fixed to the approved first retrieval pilot.
BASE = "/api/2.0/vector-search"
TEST_QUERIES = [
    ("blockchain", "Blockchain applications for information security", ["Abdallah Tubaishat"]),
    ("forensics", "Digital forensics and malware detection", ["Andrew Marrington", "Dima Rabadi"]),
    ("medical_imaging", "Machine learning for brain tumor detection in medical images", ["Ahed Abugabah"]),
    ("adoption", "Technology adoption and organizational employee performance", ["Ahmed Shuhaiber"]),
    ("transport", "Traffic prediction and transportation planning for autonomous vehicles", ["Fatma Outay"]),
    ("employability", "Higher education employability and career development", ["Graham Manville"]),
    ("education", "Reflective teaching practices and learning in higher education", ["Abdallah Tubaishat"]),
    ("out_of_scope", "Exoplanet atmospheric spectroscopy and stellar nucleosynthesis", []),
]


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


def pages(w, path, field):
    rows, token, seen = [], None, set()
    for _ in range(100):
        payload = api_call(w, "GET", path, query={"page_token": token} if token else None)
        rows.extend(payload.get(field, []))
        token = payload.get("next_page_token")
        if not token:
            return rows
        if token in seen:
            raise ValueError("Repeated API pagination token")
        seen.add(token)
    raise ValueError("Resource inventory exceeded bounded page limit")


def optional_get(w, path):
    try:
        return api_call(w, "GET", path)
    except Exception as exc:
        # A forbidden, quota or network error is never treated as absence.
        if getattr(exc, "error_code", None) in ("RESOURCE_DOES_NOT_EXIST", "NOT_FOUND"):
            return None
        raise


def index_path(cfg, suffix=""):
    return BASE + "/indexes/" + quote(cfg["index"], safe="") + suffix


def validate_documents(docs, gold_build, silver_build):
    if len(docs) != MAX_PROFILES or len({d["document_id"] for d in docs}) != MAX_PROFILES:
        raise ValueError("Expected exactly seven unique Gold search documents")
    if len({d["researcher_id"] for d in docs}) != MAX_PROFILES:
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
    validate_documents(docs, g["build_id"], s["build_id"])
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


def discover(w, cfg):
    serving = pages(w, "/api/2.0/serving-endpoints", "endpoints")
    names = {e["name"] for e in serving}
    models = [{"endpoint": name, "listed_in_workspace": name in names, **info}
              for name, info in MODELS.items()]
    endpoints = pages(w, BASE + "/endpoints", "endpoints")
    index = optional_get(w, index_path(cfg))
    print(json.dumps({"embedding_candidates": models,
                      "ai_search_endpoints": [{k: e.get(k) for k in ("name", "endpoint_type", "endpoint_status")}
                                              for e in endpoints],
                      "existing_index": None if index is None else {k: index.get(k) for k in
                          ("name", "endpoint_name", "primary_key", "delta_sync_index_spec", "status")}}, indent=2))
    return names, endpoints, index


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


def probe_model(w, model, docs):
    if model not in MODELS or not MODELS[model]["pilot_supported"]:
        raise ValueError("Unsupported pilot embedding model")
    usage = []
    # Actual complete documents, one bounded request each. Do not print embedding vectors.
    for d in sorted(docs, key=lambda x: x["document_id"]):
        response = api_call(w, "POST", "/serving-endpoints/" + quote(model, safe="") + "/invocations",
                            body={"input": [d["search_text"]]})
        data = response.get("data", [])
        vector = data[0].get("embedding", []) if len(data) == 1 else []
        if (len(vector) != MODELS[model]["dimension"]
                or any(type(x) not in (int, float) or not math.isfinite(x) for x in vector)
                or not any(vector)):
            raise ValueError("Embedding probe did not return one finite nonzero 1024-dimensional vector")
        tokens = response.get("usage", {}).get("prompt_tokens")
        if tokens is not None and (tokens <= 0 or tokens >= MODELS[model]["window_tokens"]):
            raise ValueError("Embedding token usage is at/over context window; review tokenization before indexing")
        usage.append({"document_id": d["document_id"], "characters": len(d["search_text"]),
                      "prompt_tokens": tokens})
    print(json.dumps({"model_probe": "passed", "embedding_model": model, "documents": usage,
                      "note": "Live inference succeeded; this alone is not proof of index compatibility or retrieval quality."}, indent=2))
    return usage


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
    synced = spec.get("columns_to_sync", spec.get("columns_to_index"))
    if synced is None or not (set(SYNC_COLUMNS) - {"document_id", "search_text"}).issubset(synced):
        raise ValueError("Index lacks explicitly configured lineage/hash metadata; inspect configuration")


def validate_endpoint(endpoints, cfg):
    if len(endpoints) > 1:
        raise ValueError("Expected at most one AI Search endpoint in this Free Edition pilot")
    if not endpoints:
        return None
    e = endpoints[0]
    if e.get("name") != cfg["endpoint"]:
        raise ValueError("An endpoint already exists. Set endpoint_name to its exact name to reuse it; do not create/delete another")
    if e.get("endpoint_type") != "STANDARD":
        raise ValueError("This pilot requires a STANDARD endpoint; existing resources were not changed")
    return e


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
        original = expected.get(row.get("document_id"))
        if not original or any(k not in row or row[k] != original[k] for k in SYNC_COLUMNS):
            raise ValueError("Index content is stale or differs from Gold; run sync and recheck")
    return True


def parse_results(response):
    columns = [c["name"] for c in response.get("manifest", {}).get("columns", [])]
    if len(columns) != len(set(columns)) or not set(RESULT_COLUMNS).issubset(columns):
        raise ValueError("Search result manifest lacks expected evidence/lineage columns")
    rows = response.get("result", {}).get("data_array", [])
    if any(len(row) != len(columns) for row in rows) or len(rows) > 3:
        raise ValueError("Unexpected search result shape")
    return [dict(zip(columns, row)) for row in rows]


def validate_results(rows, docs):
    expected = {d["document_id"]: d for d in docs}
    if len({r["document_id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate researcher search results")
    for row in rows:
        original = expected.get(row["document_id"])
        if not original or any(row[k] != original[k] for k in RESULT_COLUMNS):
            raise ValueError("Search returned stale or non-pilot identity/content")


def tokens(text):
    stop = {"a", "an", "and", "the", "of", "for", "in", "to", "with", "on", "using", "research"}
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2 and t not in stop}


def evidence_preview(query, rid, publications):
    # Transparent lexical support selection, NOT semantic reranking/LLM explanation.
    terms = tokens(query)
    candidates = []
    for p in publications:
        if p["researcher_id"] != rid:
            continue
        overlap = terms & tokens(p["title"] + " " + (p["abstract_excerpt"] or ""))
        if overlap:
            candidates.append((len(overlap), p["publication_year"] or 0, p["publication_evidence_id"], p, overlap))
    candidates.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return [{"publication_evidence_id": p["publication_evidence_id"], "title": p["title"],
             "url": p["evidence_url"], "publication_type": p["publication_type"],
             "is_preprint": p["is_preprint"], "overlapping_terms": sorted(overlap)}
            for _, _, _, p, overlap in candidates[:2]]


def test_searches(w, cfg, snapshot, publications):
    results, checks = [], []
    for test_id, query, hypothesized in TEST_QUERIES:
        for method in ("ANN", "HYBRID"):
            response = api_call(w, "POST", index_path(cfg, "/query"), body={
                "query_text": query, "query_type": method, "num_results": 3,
                "columns": list(RESULT_COLUMNS), "filters_json": json.dumps({
                    "gold_build_id": snapshot["gold_build_id"], "is_searchable": True})})
            rows = parse_results(response)
            validate_results(rows, snapshot["documents"])
            names = [r["display_name"] for r in rows]
            checks.append({"test_id": test_id, "query_type": method, "top_3": names,
                           "hypothesized_candidates": hypothesized,
                           "hypothesis_hit_at_3": bool(set(names) & set(hypothesized)) if hypothesized else None,
                           "review": "out_of_scope: nearest results are NOT validated matches" if not hypothesized
                                     else "manual relevance review required; not an expert-labelled benchmark"})
            for rank, row in enumerate(rows, 1):
                results.append({"test_id": test_id, "query": query, "query_type": method, "rank": rank,
                                "researcher": row["display_name"], "document_id": row["document_id"],
                                "raw_retrieval_score": row.get("score"),
                                "evidence_selection": "lexical overlap in eligible Gold titles/abstract excerpts",
                                "evidence": evidence_preview(query, row["researcher_id"], publications)})
    return {"gold_build_id": snapshot["gold_build_id"], "silver_build_id": snapshot["silver_build_id"],
            "checks": checks, "results": results,
            "warning": "Scores are not confidence probabilities or comparable across ANN/HYBRID. No automatic suitability threshold."}

# COMMAND ----------

def run_notebook(spark, dbutils, workspace=None):
    dbutils.widgets.dropdown("mode", "discover", ["discover", "probe", "create", "status", "sync", "test"])
    for name, value in (("catalog", "workspace"), ("silver_schema", "researchmatch_silver"),
                        ("gold_schema", "researchmatch_gold"), ("endpoint_name", "researchmatch-search"),
                        ("embedding_model", "auto")):
        dbutils.widgets.text(name, value)
    get = lambda n: dbutils.widgets.get(n).strip()
    mode = get("mode")
    if mode not in ("discover", "probe", "create", "status", "sync", "test"):
        raise ValueError("Unknown mode")
    cfg = config(*(get(n) for n in ("catalog", "silver_schema", "gold_schema", "endpoint_name", "embedding_model")))
    if workspace is None:
        from databricks.sdk import WorkspaceClient
        workspace = WorkspaceClient()  # Platform-managed notebook auth; no user-supplied/stored PAT.
    snapshot = source_snapshot(spark, cfg)
    print(json.dumps({k: v for k, v in snapshot.items() if k != "documents"}, indent=2))
    listed, endpoints, index = discover(workspace, cfg)
    if mode == "discover":
        print("Discovery complete. Listing is not proof of query permission. No inference, resources or Delta writes performed.")
        return {"mode": mode, "listed_supported_models": [m for m in MODELS if m in listed and MODELS[m]["pilot_supported"]]}
    model = choose_model(cfg["model"], listed, index)
    if index:
        validate_index(index, cfg, model)
    if mode == "probe":
        result = probe_model(workspace, model, snapshot["documents"])
        assert_unchanged(spark, cfg, snapshot)
        return {"mode": mode, "model": model, "probe": result}
    endpoint = validate_endpoint(endpoints, cfg)
    if mode == "create" and endpoint is None:
        if index:
            raise ValueError("Index exists but endpoint inventory disagrees")
        probe_model(workspace, model, snapshot["documents"])
        assert_unchanged(spark, cfg, snapshot)
        api_call(workspace, "POST", BASE + "/endpoints", body={"name": cfg["endpoint"], "endpoint_type": "STANDARD"})
        print("Endpoint creation submitted. Wait a few minutes, then rerun create to build the index once ONLINE. No second endpoint will be created.")
        return {"state": "endpoint_creation_submitted", "model": model}
    if endpoint is None:
        raise ValueError("No endpoint exists yet; run create first")
    if not endpoint_online(endpoint):
        print("Endpoint is not ONLINE yet. Wait, then rerun create or status; no mutation performed.")
        return {"state": "endpoint_pending"}
    if index is None:
        if mode != "create":
            raise ValueError("Endpoint is online but index is absent; run create")
        probe_model(workspace, model, snapshot["documents"])
        assert_unchanged(spark, cfg, snapshot)
        api_call(workspace, "POST", BASE + "/indexes", body=index_spec(cfg, model))
        print("Triggered Delta Sync index creation submitted with initial ingestion. Wait, then run status. Do not issue repeated syncs while it initializes.")
        return {"state": "index_creation_submitted", "model": model}
    if mode == "sync":
        assert_unchanged(spark, cfg, snapshot)
        api_call(workspace, "POST", index_path(cfg, "/sync"))
        print("One sync submitted. Wait for completion, then run status and test. Do not repeatedly run sync.")
        return {"state": "sync_submitted"}
    if index.get("status", {}).get("ready") is not True:
        print("Index is not ready yet. Inspect its status message, wait, then rerun status; do not recreate it.")
        return {"state": "index_pending"}
    scan_matches(workspace, cfg, snapshot["documents"])
    assert_unchanged(spark, cfg, snapshot)
    print("Index content verified: seven document IDs, all synced metadata, text and hashes match current Gold.")
    if mode != "test":
        return {"state": "index_verified", "model": model, "documents": MAX_PROFILES}
    table = sql_name(cfg["catalog"], cfg["gold"], "researcher_profile_publications")
    pubs = [r.asDict(recursive=True) for r in spark.sql(
        "SELECT researcher_id, publication_evidence_id, title, abstract_excerpt, publication_year, "
        "evidence_url, publication_type, is_preprint FROM " + table +
        " WHERE build_id = '" + snapshot["gold_build_id"] + "'").limit(1001).collect()]
    if len(pubs) > 1000 or not pubs:
        raise ValueError("Publication evidence exceeds bounded pilot scope or is missing")
    result = test_searches(workspace, cfg, snapshot, pubs)
    assert_unchanged(spark, cfg, snapshot)
    # Print only after all queries and freshness checks pass. No Gold mutations.
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("Live search tests completed. Review candidate rankings/evidence; execution success is not relevance validation.")
    return result


if "spark" in globals() and "dbutils" in globals():
    search_result = run_notebook(spark, dbutils)
