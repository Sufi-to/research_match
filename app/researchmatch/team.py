"""Evidence-coverage team suggestions built only from an existing match result.

No search, model or data access. A member's coverage is the set of interpreted
query terms matched in the publications already displayed for them, so every
claim on screen is backed by a visible publication. Coauthorship, funding and
availability are neither required nor inferred.
"""

TEAM_SIZES = (2, 3, 4)


def coverage(candidate, terms):
    covered = set()
    for item in candidate["evidence"]:
        covered.update(item["matched_in_title"], item["matched_in_abstract"])
    return covered & terms


def assemble(result, team_size):
    if team_size not in TEAM_SIZES:
        raise ValueError("Choose a team of two to four people")
    terms = set(result.get("interpreted_terms") or [])
    # Direct candidates first, then partial; within each list keep the policy's order.
    pool = [(c, coverage(c, terms)) for c in result["candidates"] + result["partial_candidates"]]
    pool = [(c, cov) for c, cov in pool if cov]
    members, covered = [], set()
    while pool and len(members) < team_size and covered != terms:
        # max() keeps the first of equal candidates, so ties follow the pool order.
        best, cov = max(pool, key=lambda pair: len(pair[1] - covered))
        added = cov - covered
        if not added:
            break  # Nobody left adds a new term: no near-duplicate expertise.
        members.append({"candidate": best, "role": "Lead" if not members else "Complement",
                        "terms_added": sorted(added), "terms_covered": sorted(cov)})
        covered |= added
        pool = [(c, v) for c, v in pool if c is not best]
    return {"members": members, "uncovered_terms": sorted(terms - covered),
            "status": "none" if not members else "full" if covered == terms else "partial",
            "team_size": team_size}
