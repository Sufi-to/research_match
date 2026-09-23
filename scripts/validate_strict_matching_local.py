"""Offline evidence-policy replay. No live search, network, Delta writes or approvals.

All seven profiles are supplied as synthetic candidates in stable ID order.
This validates evidence gating only; it does NOT simulate embedding relevance.
"""
from datetime import datetime
import json

from validate_silver_local import ROOT, module, replay


# From the previously reserved human-review template. These have no automatic
# ground-truth labels; do not adjust policy weights based on their output.
HELD_OUT_CASES = [
    ("H01", "Detecting phishing messages in email"),
    ("H02", "Recovering digital evidence from mobile applications"),
    ("H03", "Classifying malicious software from runtime behavior"),
    ("H04", "RFID implementation in hospitals"),
    ("H05", "Trust and willingness to adopt connected home devices"),
    ("H06", "Road congestion forecasting with connected vehicles"),
    ("H07", "Electronic portfolios for graduate career readiness"),
    ("H08", "Reflective assessment in computing courses"),
    ("H09", "Coral reef restoration and marine biodiversity"),
    ("H10", "Gene editing in drought-resistant wheat"),
    ("H11", "Machine learning for stellar spectroscopy"),
    ("H12", "Artificial intelligence"),
]


def main():
    identities = json.loads((ROOT / "tests/fixtures/confirmed_pilot_identities.json").read_text())["identities"]
    decisions = [{"decision_id": "offline-" + i["candidate_id"], "candidate_id": i["candidate_id"],
                  "evidence_hash": i["evidence_hash"], "decision": "accept",
                  "rationale": "Offline fixture only; not a new approval", "reviewed_by": "offline simulation",
                  "reviewed_at": datetime(2026, 9, 18)} for i in identities]
    silver, _ = replay(str(ROOT / "data/landing"), ["pilot-20260905", "openalex-20260905"], decisions)
    gold = module("gold_matching_replay", ROOT / "notebooks/10_build_gold.py")
    graph, _ = gold.build_gold(lambda t: iter(silver.rows(t)), silver.build_id,
                               build_id="offline-matching-validation", expected_profiles=7)
    matcher = module("matching_replay", ROOT / "notebooks/14_match_researchers_strict.py")
    docs = sorted(gold.serving_rows(graph), key=lambda d: d["document_id"])
    data = {t: graph.rows(t) for t in matcher.EVIDENCE_COLUMNS}
    evidence = matcher.prepare_evidence(docs, data, graph.build_id, silver.build_id)
    snapshot = {"documents": docs, "gold_build_id": graph.build_id, "silver_build_id": silver.build_id}
    candidates = [{k: d[k] for k in matcher.RESULT_COLUMNS} for d in docs]
    print(json.dumps({"possible_version_group_count": len(evidence["possible_version_groups"]),
                      "possible_version_groups": [{"name": g["display_name"], "title": g["records"][0]["title"],
                                                   "contribution_ids": [p["contribution_id"] for p in g["records"]]}
                                                  for g in evidence["possible_version_groups"]]}, ensure_ascii=False))
    for test_id, query in matcher.SMOKE_CASES + HELD_OUT_CASES:
        result = matcher.match_candidates(matcher.query_plan(query), candidates, evidence, snapshot)
        print(json.dumps({"offline_evidence_policy_only": True, "test_id": test_id,
                          "status": result["status"], "message": result["message"],
                          "candidates_and_partial": [{"name": c["display_name"], "score": c["relevance_score_heuristic"],
                                          "support": c["support_group_count_for_ranking"], "scope": c["evidence_scope"],
                                          "missing_terms": c["best_evidence_missing_terms"],
                                          "first_evidence_title": c["evidence"][0]["title"]}
                                         for c in result["candidates"] + result["partial_candidates"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
