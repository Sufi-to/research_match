"""Replay manifested local files through the real Bronze and Silver adapters.

Read-only validation, no API calls, no Databricks writes. The local harness retains
one source table's latest raw versions at a time; the notebook uses Spark iterators.
"""
import argparse
import importlib.util
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def replay(landing_path, batch_folders, decisions=()):
    """Reusable read-only replay for Silver and Gold offline validation."""
    bronze = module("bronze", ROOT / "notebooks/03_load_volume_to_bronze.py")
    silver = module("silver", ROOT / "notebooks/05_build_silver.py")
    plan = bronze.select_inputs(landing_path, batch_folders, list(silver.BRONZE_KEYS), 0)

    def read(table):
        latest = {}
        for ref in plan[table]["files"]:
            for row in bronze.read_rows(table, ref, "local_validation"):
                if table == "zu_scholars_oai_raw" and row["metadata_prefix"] != "oai_dc":
                    continue
                entity = row[silver.BRONZE_KEYS[table]]
                observed = row.get("fetched_at", row.get("discovered_at"))
                ordering = (observed, row.get("source_updated_at") or silver.datetime.min)
                old = latest.get(entity)
                if old is None or ordering > old[0] or (ordering == old[0] and row["record_key"] < old[1]["record_key"]):
                    latest[entity] = (ordering, row)
        # Same stable OAI order as the notebook, for metadata fallback precedence.
        for entity in sorted(latest):
            yield latest.pop(entity)[1]

    reader, totals = silver.bounded_read(read)
    return silver.build_graph(reader, decisions), totals


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--landing-path", default=str(ROOT / "data/landing"))
    parser.add_argument("--batch-folders", default="pilot-20260905,openalex-20260905")
    parser.add_argument("--identity-decisions-json", type=Path,
                        help="Optional local decision list for simulation only; never uploads/records approvals")
    args = parser.parse_args()
    decisions = []
    if args.identity_decisions_json:
        decisions = json.loads(args.identity_decisions_json.read_text())
        if not isinstance(decisions, list):
            raise ValueError("Local decision simulation requires a JSON list")
        for row in decisions:
            row["reviewed_at"] = datetime.fromisoformat(row["reviewed_at"])
    graph, totals = replay(args.landing_path, args.batch_folders.split(","), decisions)
    works = graph.rows("works")
    candidates = graph.rows("entity_resolution_review")
    researchers = {r["researcher_id"]: r["display_name"] for r in graph.rows("researchers")}
    researcher_counts = []
    for rid, name in sorted(researchers.items(), key=lambda pair: pair[1]):
        links = [r for r in graph.rows("researcher_works") if r["researcher_id"] == rid]
        contributions = [r for r in graph.rows("researcher_contributions") if r["researcher_id"] == rid]
        if links:
            researcher_counts.append({"name": name, "source_links": len(links),
                "contributions": len(contributions),
                "eligible_contributions": sum(r["eligible_for_expertise"] for r in contributions)})
    report = {"local_decision_simulation": bool(args.identity_decisions_json),
              "input": totals, "counts": {t: len(rows) for t, rows in graph.tables.items()},
              "identity_statuses": {status: sum(c["status"] == status for c in candidates)
                                    for status in ("pending", "verified", "rejected")},
              "researcher_counts": researcher_counts,
              "output_estimated_bytes": graph.byte_count,
              "identity_methods": {method: sum(c["match_method"] == method for c in candidates)
                                   for method in ("profile_orcid", "exact_normalized_name", "name_tokens")},
              "works_with_abstract": sum(bool(w["abstract"]) for w in works),
              "openalex_works_enriched_with_oai_description": sum(w["work_id"].startswith("oa:") and w["abstract_source"] == "oai_description" for w in works),
              "oai_sources_linked_by_doi": sum(r["match_method"] == "unique_doi" for r in graph.rows("work_sources")),
              "researchers_with_biography": sum(bool(r["biography"]) for r in graph.rows("researchers")),
              "researchers_with_research_activity": sum(bool(r["research_activity_text"]) for r in graph.rows("researchers")),
              "retracted": sum(w["is_retracted"] for w in works),
              "unresolved_award_ids": len({r["award_id"] for r in graph.rows("work_awards") if r["resolution_status"] == "unresolved"}),
              "quality_issue_codes": sorted({r["issue_code"] for r in graph.rows("quality_issues")})}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
