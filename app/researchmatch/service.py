"""One explicit request = fresh bounded reads + verified index + at most one search."""
from contextlib import contextmanager
from datetime import datetime, timezone
import os
from urllib.parse import quote

from . import matching_policy as policy
from . import profiles as profile_view
from .warehouse import Warehouse


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


def confirm_unchanged(reader, cfg, snapshot, versions):
    with stage("freshness"):
        policy.assert_unchanged(reader, cfg, snapshot)
        for table, version in versions.items():
            target = policy.sql_name(cfg["catalog"], cfg["gold"], table)
            if int(reader.sql("DESCRIBE HISTORY " + target + " LIMIT 1").first()["version"]) != version:
                raise ValueError("Evidence changed; results withheld")


def provenance(result, snapshot, versions):
    result.update(policy_version=policy.MATCH_POLICY, gold_build_id=snapshot["gold_build_id"],
                  silver_build_id=snapshot["silver_build_id"], evidence_versions=versions,
                  checked_at=datetime.now(timezone.utc).isoformat())
    return result


def execute(query=None, required_terms="", top_k=3, workspace=None, environ=None):
    with stage("input"):
        if type(top_k) is not int or not 1 <= top_k <= 3:
            raise ValueError("Choose one to three candidates per list")
        # Check user input before any billable reads; query text never enters SQL.
        if query is not None:
            policy.query_plan(query, required_terms)
    cfg, workspace, reader = connect(workspace, environ)
    with stage("data"):
        snapshot = policy.source_snapshot(reader, cfg)
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
    with stage("data"):
        evidence, versions = policy.load_evidence(reader, cfg, snapshot)
    with stage("freshness"):
        policy.assert_unchanged(reader, cfg, snapshot)
    if query is None:
        result = {"status": "validation_passed", "researchers": len(evidence["documents"]),
                  "eligible_publications": sum(map(len, evidence["by_person"].values())),
                  "possible_version_group_count": len(evidence["possible_version_groups"])}
    else:
        with stage("search"):
            result = policy.run_match(workspace, cfg, snapshot, evidence, query, "HYBRID", required_terms, top_k)
    confirm_unchanged(reader, cfg, snapshot, versions)
    return provenance(result, snapshot, versions)


def profiles(workspace=None, environ=None):
    """Researcher profiles from the same validated Gold evidence; no search or inference."""
    cfg, workspace, reader = connect(workspace, environ)
    with stage("data"):
        snapshot = policy.source_snapshot(reader, cfg)
        evidence, versions = policy.load_evidence(reader, cfg, snapshot)
    confirm_unchanged(reader, cfg, snapshot, versions)
    return provenance({"status": "profiles", "profiles": profile_view.build(evidence)}, snapshot, versions)
