"""No model/source HTML or arbitrary external links are rendered by the UI."""
from urllib.parse import urlsplit


def safe_url(value):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.hostname not in {"doi.org", "openalex.org", "www.zu.ac.ae", "zu.ac.ae"}
                or any(ord(c) < 33 for c in value)):
            return None
        return value
    except (ValueError, TypeError):
        return None


def show_candidates(st, rows, partial=False):
    for row in rows:
        with st.container(border=True):
            st.text(row["display_name"])
            st.caption("Partial evidence — not a full-query recommendation" if partial else
                       "Direct query-term evidence — human review still required")
            st.text("Missing terms: " + (", ".join(row["best_evidence_missing_terms"]) or "None among interpreted terms"))
            st.caption(f"{row['support_group_count_for_ranking']} support groups used for ranking · "
                       f"{row['qualifying_source_contribution_count']} qualifying source contributions")
            link = safe_url(row["profile_url"])
            if link:
                st.link_button("ZU profile", link)
            st.caption("Current employment and collaboration availability unconfirmed. Funding history not established.")
            show_evidence(st, row["evidence"])


def show_evidence(st, items):
    for n, evidence in enumerate(items, 1):
        with st.expander(f"Supporting publication {n}"):
            st.text(evidence["title"])
            st.text(f"{evidence['publication_year'] or 'Year unknown'} · {evidence['publication_type']}")
            if evidence["is_preprint"]:
                st.warning("Preprint — do not assume peer review.")
            st.text("Matched in title: " + ", ".join(evidence["matched_in_title"]))
            st.text("Matched in abstract: " + ", ".join(evidence["matched_in_abstract"]))
            if evidence["abstract_excerpt"]:
                st.text(evidence["abstract_excerpt"])
            st.text("Missing terms: " + (", ".join(evidence["missing_query_terms"]) or "None"))
            if evidence["topic_only_query_terms"]:
                st.text("Topic-label-only terms (not used as direct evidence): " + ", ".join(evidence["topic_only_query_terms"]))
            url = safe_url(evidence["url"])
            if url:
                st.link_button("Open publication source", url)
            st.caption("Evidence ID: " + evidence["publication_evidence_id"])
            if evidence["possible_versions"]:
                st.warning("Same-title records: relationship unverified; only one ranking/display slot is used.")
                for version in evidence["possible_versions"]:
                    st.text(version["title"] + " · " + version["publication_type"])
                    ref = safe_url(version["url"])
                    if ref:
                        st.link_button("Possible version source", ref)


def show_team(st, team):
    if team["status"] == "none":
        st.caption("No researcher's displayed publications cover any interpreted term; no team is suggested.")
        return
    if team["status"] == "full" and len(team["members"]) == 1:
        st.caption("One researcher's displayed publications cover every interpreted term; no complement is needed.")
    if team["uncovered_terms"]:
        st.warning("No displayed publication covers: " + ", ".join(team["uncovered_terms"]))
    for member in team["members"]:
        row = member["candidate"]
        with st.container(border=True):
            st.text(row["display_name"])
            st.caption(f"{member['role']} — " + ("covers " if member["role"] == "Lead" else "adds ")
                       + ", ".join(member["terms_added"]))
            st.caption("Suggested by evidence coverage, not a confirmed collaboration. "
                       "Current employment and availability unconfirmed. Funding history not established.")
            link = safe_url(row["profile_url"])
            if link:
                st.link_button("ZU profile", link)
            show_evidence(st, row["evidence"])


def show_profile(st, profile):
    st.subheader(profile["display_name"])
    st.caption(f"College: {profile['college_codes_text'] or 'not recorded'} · "
               f"{profile['eligible_contribution_count']} eligible publications · "
               f"{profile['eligible_preprint_count']} preprints · "
               f"{profile['possible_version_group_count']} same-title groups (not merged)")
    st.caption("Identity verified against the ZU directory and ORCID. Current employment, availability "
               "and funding history are not established.")
    link = safe_url(profile["profile_url"])
    if link:
        st.link_button("ZU profile", link)
    st.markdown("**Most frequent publication topics**")
    st.caption("Number of eligible publications classified under each topic. Topic labels are "
               "classification context, not a claim of expertise.")
    for topic in profile["top_topics"]:
        st.text(f"{topic['topic_name']} · {topic['contribution_count']}")
    st.markdown(f"**Publications** ({len(profile['publications'])} shown, newest first)")
    for pub in profile["publications"]:
        with st.expander(f"{pub['publication_year'] or 'Year unknown'} · {pub['title']}"):
            st.text(pub["publication_type"])
            if pub["is_preprint"]:
                st.warning("Preprint — do not assume peer review.")
            if pub["possible_version_count"]:
                st.warning(f"{pub['possible_version_count']} same-title records; relationship unverified, shown once.")
            url = safe_url(pub["url"])
            if url:
                st.link_button("Open publication source", url)
            st.caption("Evidence ID: " + pub["publication_evidence_id"])


def show_result(st, result, team=None):
    status = result["status"]
    if status == "validation_passed":
        st.success("Connection and evidence validation passed.")
        st.text(f"{result['researchers']} researchers · {result['eligible_publications']} eligible publications")
        st.text(f"{result['possible_version_group_count']} same-title groups need review; none were merged.")
    else:
        st.text("Research topic: " + result["query"])
        if status in ("needs_refinement", "insufficient_evidence", "partial_evidence_only"):
            st.warning(result["message"])
        else:
            st.info("Candidates for review, not confirmed collaboration recommendations.")
        if team is not None:
            st.subheader(f"Suggested team (up to {team['team_size']})")
            show_team(st, team)
        st.subheader("Direct query-term matches")
        if not result["candidates"]:
            st.caption("No full-query candidate passed the current rules.")
        show_candidates(st, result["candidates"])
        st.subheader("Partial evidence")
        if not result["partial_candidates"]:
            st.caption("No partial candidates to show.")
        show_candidates(st, result["partial_candidates"], partial=True)
    with st.expander("Data provenance and limitations"):
        st.text("Policy: " + result["policy_version"])
        st.text("Gold build: " + result["gold_build_id"])
        st.text("Silver build: " + result["silver_build_id"])
        st.text("Checked at: " + result["checked_at"])
        st.caption("Results are a point-in-time snapshot. Re-submit to check current data. "
                   "Term matching can miss synonyms and admit irrelevant context. "
                   "No accuracy claim or grant-recipient inference is made. Team suggestions "
                   "only combine displayed evidence coverage; they are not validated team fit.")
