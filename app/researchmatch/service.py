"""One explicit request = fresh bounded reads + verified index + at most one search."""
from datetime import datetime, timezone
import os
from urllib.parse import quote

from . import matching_policy as policy
from .warehouse import Warehouse


def configuration(environ=None):
    env = os.environ if environ is None else environ
    cfg = policy.config(catalog=env.get("RESEARCHMATCH_CATALOG", "workspace"),
                        endpoint=env.get("RESEARCHMATCH_ENDPOINT", "vs_endpoint_1"),
                        model="databricks-gte-large-en")
    return cfg, env.get("DATABRICKS_WAREHOUSE_ID", "")


def execute(query=None, required_terms="", top_k=3, workspace=None, environ=None):
    if type(top_k) is not int or not 1 <= top_k <= 3:
        raise ValueError("Choose one to three candidates per list")
    # Check user input before any billable reads; query text never enters SQL.
    if query is not None:
        policy.query_plan(query, required_terms)
    cfg, warehouse_id = configuration(environ)
    if workspace is None:
        from databricks.sdk import WorkspaceClient
        workspace = WorkspaceClient()  # App service principal; no .env or token UI.
    reader = Warehouse(workspace, warehouse_id, cfg)
    snapshot = policy.source_snapshot(reader, cfg)
    endpoint = policy.api_call(workspace, "GET", policy.BASE + "/endpoints/" + quote(cfg["endpoint"], safe=""))
    if endpoint.get("endpoint_type") != "STANDARD" or not policy.endpoint_online(endpoint):
        raise ValueError("Existing search endpoint is not online")
    index = policy.api_call(workspace, "GET", policy.index_path(cfg))
    model = policy.choose_model(cfg["model"], set(), index)
    policy.validate_index(index, cfg, model)
    if index.get("status", {}).get("ready") is not True:
        raise ValueError("Existing index is not ready")
    policy.scan_matches(workspace, cfg, snapshot["documents"])
    evidence, versions = policy.load_evidence(reader, cfg, snapshot)
    policy.assert_unchanged(reader, cfg, snapshot)
    if query is None:
        result = {"status": "validation_passed", "researchers": len(evidence["documents"]),
                  "eligible_publications": sum(map(len, evidence["by_person"].values())),
                  "possible_version_group_count": len(evidence["possible_version_groups"])}
    else:
        result = policy.run_match(workspace, cfg, snapshot, evidence, query, "HYBRID", required_terms, top_k)
    policy.assert_unchanged(reader, cfg, snapshot)
    for table, version in versions.items():
        target = policy.sql_name(cfg["catalog"], cfg["gold"], table)
        if int(reader.sql("DESCRIBE HISTORY " + target + " LIMIT 1").first()["version"]) != version:
            raise ValueError("Evidence changed; results withheld")
    result.update(policy_version=policy.MATCH_POLICY, gold_build_id=snapshot["gold_build_id"],
                  silver_build_id=snapshot["silver_build_id"], evidence_versions=versions,
                  checked_at=datetime.now(timezone.utc).isoformat())
    return result
