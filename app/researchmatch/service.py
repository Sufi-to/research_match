"""Validated, cached app state + at most one search per request.

A full validation (snapshot, endpoint, index content scan, evidence, version
re-checks) takes ~27 SQL statements. It is cached in-process and reused while it
is younger than CACHE_TTL_SECONDS and a one-statement Gold build probe still
matches. "Check connection and data" always revalidates; any failure clears the
cache so the next request revalidates from scratch.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import os
import threading
import time
from urllib.parse import quote

from . import matching_policy as policy
from . import profiles as profile_view
from .warehouse import Warehouse

CACHE_TTL_SECONDS = 900
_cache = {"state": None}
_lock = threading.Lock()


def configuration(environ=None):
    env = os.environ if environ is None else environ
    cfg = policy.config(catalog=env.get("RESEARCHMATCH_CATALOG", "workspace"),
                        endpoint=env.get("RESEARCHMATCH_ENDPOINT", "vs_endpoint_1"),
                        model="databricks-gte-large-en")
    return cfg, env.get("DATABRICKS_WAREHOUSE_ID", "")


@contextmanager
def stage(name):
    # Tag (not wrap) the exception so callers and tests still see its original type.
    try:
        yield
    except Exception as error:
        if not hasattr(error, "researchmatch_stage"):
            try:
                error.researchmatch_stage = name
            except AttributeError:
                pass
        raise


def connect(workspace, environ):
    cfg, warehouse_id = configuration(environ)
    if workspace is None:
        from databricks.sdk import WorkspaceClient
        workspace = WorkspaceClient()  # App service principal; no .env or token UI.
    return cfg, workspace, Warehouse(workspace, warehouse_id, cfg)


def now():
    return datetime.now(timezone.utc).isoformat()


def clear_cache():
    with _lock:
        _cache["state"] = None


def build_probe(reader, cfg):
    """One statement: the published Gold build, its Silver build and document count."""
    with stage("probe"):
        rows = reader.sql("SELECT build_id, silver_build_id, document_count FROM "
                          + policy.sql_name(cfg["catalog"], cfg["gold"], "current_build")).limit(2).collect()
        if len(rows) != 1:
            raise ValueError("Expected exactly one published build")
        return (rows[0]["build_id"], rows[0]["silver_build_id"], rows[0]["document_count"])


def check_search_resources(workspace, cfg, snapshot):
    with stage("endpoint"):
        endpoint = policy.api_call(workspace, "GET", policy.BASE + "/endpoints/" + quote(cfg["endpoint"], safe=""))
        if endpoint.get("endpoint_type") != "STANDARD" or not policy.endpoint_online(endpoint):
            raise ValueError("Existing search endpoint is not online")
    with stage("index"):
        index = policy.api_call(workspace, "GET", policy.index_path(cfg))
        model = policy.choose_model(cfg["model"], set(), index)
        policy.validate_index(index, cfg, model)
        if index.get("status", {}).get("ready") is not True:
            raise ValueError("Existing index is not ready")
        policy.scan_matches(workspace, cfg, snapshot["documents"])


def validate_state(cfg, workspace, reader):
    """The full integrity sequence; the index checks and evidence reads run concurrently."""
    with stage("data"):
        snapshot = policy.source_snapshot(reader, cfg)
    with ThreadPoolExecutor(max_workers=2) as pool:
        search = pool.submit(check_search_resources, workspace, cfg, snapshot)
        with stage("data"):
            evidence, versions = policy.load_evidence(reader, cfg, snapshot)
        search.result()
    with stage("freshness"):
        policy.assert_unchanged(reader, cfg, snapshot)
        for table, version in versions.items():
            target = policy.sql_name(cfg["catalog"], cfg["gold"], table)
            if int(reader.sql("DESCRIBE HISTORY " + target + " LIMIT 1").first()["version"]) != version:
                raise ValueError("Evidence changed; results withheld")
    probe = (snapshot["gold_build_id"], snapshot["silver_build_id"], len(snapshot["documents"]))
    return {"snapshot": snapshot, "evidence": evidence, "versions": versions, "probe": probe,
            "validated_at": now(), "validated_monotonic": time.monotonic()}


def current_state(cfg, workspace, reader, force=False):
    """Reuse the cached validation while fresh and the build probe matches; else revalidate."""
    with _lock:
        state = _cache["state"]
        if (not force and state is not None
                and time.monotonic() - state["validated_monotonic"] < CACHE_TTL_SECONDS
                and build_probe(reader, cfg) == state["probe"]):
            return dict(state, checked_at=now(), cached=True)
        _cache["state"] = None
        state = validate_state(cfg, workspace, reader)
        _cache["state"] = state
        return dict(state, checked_at=state["validated_at"], cached=False)


def provenance(result, state):
    snapshot = state["snapshot"]
    result.update(policy_version=policy.MATCH_POLICY, gold_build_id=snapshot["gold_build_id"],
                  silver_build_id=snapshot["silver_build_id"], evidence_versions=state["versions"],
                  validated_at=state["validated_at"], checked_at=state["checked_at"],
                  cache_ttl_minutes=CACHE_TTL_SECONDS // 60)
    return result


def execute(query=None, required_terms="", top_k=3, workspace=None, environ=None):
    with stage("input"):
        if type(top_k) is not int or not 1 <= top_k <= 3:
            raise ValueError("Choose one to three candidates per list")
        # Check user input before any billable reads; query text never enters SQL.
        if query is not None:
            policy.query_plan(query, required_terms)
    cfg, workspace, reader = connect(workspace, environ)
    try:
        # "Check connection and data" (query=None) always runs the full validation.
        state = current_state(cfg, workspace, reader, force=query is None)
        evidence = state["evidence"]
        if query is None:
            result = {"status": "validation_passed", "researchers": len(evidence["documents"]),
                      "eligible_publications": sum(map(len, evidence["by_person"].values())),
                      "possible_version_group_count": len(evidence["possible_version_groups"])}
        else:
            with stage("search"):
                # validate_results inside run_match rejects index rows that don't match the snapshot.
                result = policy.run_match(workspace, cfg, state["snapshot"], evidence, query, "HYBRID",
                                          required_terms, top_k)
    except Exception:
        clear_cache()
        raise
    return provenance(result, state)


def profiles(workspace=None, environ=None):
    """Researcher profiles from the same validated (possibly cached) Gold evidence; no search."""
    cfg, workspace, reader = connect(workspace, environ)
    try:
        state = current_state(cfg, workspace, reader)
    except Exception:
        clear_cache()
        raise
    return provenance({"status": "profiles", "profiles": profile_view.build(state["evidence"])}, state)
