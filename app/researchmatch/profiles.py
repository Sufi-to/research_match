"""Read-only researcher profiles built from the validated matching evidence.

Topic counts are distinct eligible contributions linked to each topic in Gold.
Topic labels describe classified publications; they are not claims of expertise.
Same-title records share one display slot, as in matching.
"""
from collections import Counter

MAX_TOPICS = 8


def build(evidence):
    profiles = []
    for rid, doc in evidence["documents"].items():
        own = evidence["by_person"][rid]
        topics = Counter(t["topic_name"] for item in own for t in item["topics"])
        groups = {}
        for item in own:
            # Keep one representative per same-title group: the lowest evidence ID.
            key = item["display_group_id"]
            if key not in groups or item["publication"]["publication_evidence_id"] < groups[key]["publication"]["publication_evidence_id"]:
                groups[key] = item
        publications = sorted(groups.values(), key=lambda i: (-(i["publication"]["publication_year"] or 0),
                                                              i["publication"]["title"].lower()))
        profiles.append({
            "researcher_id": rid, "display_name": doc["display_name"], "profile_url": doc["profile_url"],
            "college_codes_text": doc["college_codes_text"],
            "eligible_contribution_count": doc["eligible_contribution_count"],
            "eligible_preprint_count": doc["eligible_preprint_count"],
            "possible_version_group_count": sum(1 for g in evidence["possible_version_groups"]
                                                if g["researcher_id"] == rid),
            "top_topics": [{"topic_name": name, "contribution_count": n}
                           for name, n in sorted(topics.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_TOPICS]],
            "publications": [{"title": i["publication"]["title"], "publication_year": i["publication"]["publication_year"],
                              "publication_type": i["publication"]["publication_type"],
                              "is_preprint": i["publication"]["is_preprint"], "url": i["publication"]["evidence_url"],
                              "publication_evidence_id": i["publication"]["publication_evidence_id"],
                              "possible_version_count": len(i["possible_versions"])} for i in publications],
        })
    return sorted(profiles, key=lambda p: (p["display_name"].lower(), p["researcher_id"]))
