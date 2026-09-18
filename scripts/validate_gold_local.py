"""Replay landed Bronze -> Silver -> Gold with the confirmed pilot identity fixture.

Read-only, no network/Spark/Delta writes. Simulated decisions are NEVER uploaded.
Prints counts and one bounded search-document preview for inspection.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path

from validate_silver_local import ROOT, module, replay


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--landing-path", default=str(ROOT / "data/landing"))
    parser.add_argument("--batch-folders", default="pilot-20260905,openalex-20260905")
    parser.add_argument("--identity-fixture", type=Path, default=ROOT / "tests/fixtures/confirmed_pilot_identities.json")
    args = parser.parse_args()
    identities = json.loads(args.identity_fixture.read_text())["identities"]
    decisions = [{"decision_id": "offline-" + i["candidate_id"], "candidate_id": i["candidate_id"],
                  "evidence_hash": i["evidence_hash"], "decision": "accept",
                  "rationale": "Offline replay of user-confirmed identity; not a new approval",
                  "reviewed_by": "offline simulation", "reviewed_at": datetime(2026, 9, 18)} for i in identities]
    silver, bronze_totals = replay(args.landing_path, args.batch_folders.split(","), decisions)
    gold = module("gold", ROOT / "notebooks/10_build_gold.py")
    graph, totals = gold.build_gold(lambda table: iter(silver.rows(table)), silver.build_id,
                                   build_id="local-gold-validation", expected_profiles=len(identities))
    profiles = sorted(graph.rows("researcher_profiles"), key=lambda p: p["display_name"])
    documents = graph.rows("researcher_search_document_history")
    dima = next((d for d in documents if d["display_name"] == "Dima Rabadi"), documents[0])
    print(json.dumps({"offline_simulation": True, "bronze_input": bronze_totals,
        "gold_input": totals, "gold_counts": {t: len(graph.tables[t]) for t in gold.CONTRACTS},
        "serving_documents": len(documents), "gold_output_bytes": graph.byte_count,
        "max_search_characters": max(len(d["search_text"]) for d in documents),
        "profiles": [{k: p[k] for k in ("display_name", "linked_source_record_count", "contribution_count",
                      "eligible_contribution_count", "eligible_preprint_count", "funding_status")} for p in profiles],
        "sample_search_document": dima["search_text"]}, indent=2))


if __name__ == "__main__":
    main()
