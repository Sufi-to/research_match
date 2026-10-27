"""Run with streamlit run app.py from this directory; no remote calls on page load."""
import uuid
import streamlit as st

from researchmatch import errors, team as teams
from researchmatch.presentation import show_profile, show_result
from researchmatch.service import execute, profiles

st.set_page_config(page_title="ResearchMatch · ZU pilot", page_icon="🔎", layout="wide")
st.title("ResearchMatch")
st.caption("Zayed University · Verified-researcher pilot · Evidence first")


def failure(error):
    ref = uuid.uuid4().hex[:10]
    stage = getattr(error, "researchmatch_stage", "unknown")
    # Stage, type and reference only: never log query text, token, raw response or SQL.
    print(f"ResearchMatch request failed [{ref}] ({stage}, {type(error).__name__})")
    return (f"{errors.describe(error)} (reference {ref}) "
            "No previous result is being shown. This is not a finding of insufficient expertise.")


with st.sidebar:
    st.header("Pilot controls")
    view = st.radio("View", ["Find collaborators", "Researcher profiles"])
    validate = view == "Find collaborators" and st.button("Check connection and data")
    clear = st.button("Clear displayed results")
    st.caption("Uses the existing SQL warehouse and AI Search index. "
               "Each submitted request uses compute; typing does not run a search.")
    st.caption("No saved searches or feedback database. Avoid confidential proposal details. "
               "The platform may retain operational logs.")

if clear:
    for key in ("last_result", "last_error", "last_team", "profiles", "profiles_error"):
        st.session_state.pop(key, None)

if view == "Researcher profiles":
    st.write("Browse each verified researcher's collected publications and topics.")
    if st.button("Load researcher profiles"):
        st.session_state.pop("profiles", None)
        st.session_state.pop("profiles_error", None)
        with st.spinner("Checking current data…"):
            try:
                st.session_state["profiles"] = profiles()
            except Exception as error:
                st.session_state["profiles_error"] = failure(error)
    if st.session_state.get("profiles_error"):
        st.error(st.session_state["profiles_error"])
    loaded = st.session_state.get("profiles")
    if loaded:
        people = loaded["profiles"]
        names = [p["display_name"] for p in people]
        chosen = st.selectbox(f"Researcher ({len(people)} verified)", range(len(people)), format_func=names.__getitem__)
        show_profile(st, people[chosen])
        st.caption(f"Gold build {loaded['gold_build_id']} · checked at {loaded['checked_at']}")
    st.stop()

st.write("Explore potential collaborators through their collected publications.")
st.info("Experimental matching: direct and partial evidence are shown separately. "
        "Identity verification does not confirm current employment or collaboration availability.")

with st.form("research_topic_form"):
    topic = st.text_area("Research topic", max_chars=500,
                         placeholder="For example: Machine learning for brain tumor detection")
    required = st.text_input("Required terms (optional)", max_chars=500,
                             help="Terms must already occur in your topic and must be supported directly.")
    top_k = st.selectbox("Maximum people per result list", [3, 2, 1])
    mode = st.radio("Results", ["Individuals", "Suggested team"], horizontal=True,
                    help="A team combines researchers whose displayed publications cover different "
                         "parts of the topic. It uses the same single search.")
    team_size = st.selectbox("Team size", list(teams.TEAM_SIZES), index=1)
    submit = st.form_submit_button("Find evidence-backed candidates")

if submit or validate:
    # Clear earlier success before a request, so an error never displays stale success.
    for key in ("last_result", "last_error", "last_team"):
        st.session_state.pop(key, None)
    if submit and (not 3 <= len(topic.strip()) <= 500 or not topic.strip()):
        st.session_state["last_error"] = "Enter a research topic of 3–500 characters."
    else:
        with st.spinner("Checking current data and evidence…"):
            try:
                team_mode = submit and mode == "Suggested team"
                # Team mode pools the largest lists; team size, not top_k, limits the team.
                result = execute(None if validate else topic, required, 3 if team_mode else top_k)
                if team_mode:
                    st.session_state["last_team"] = teams.assemble(result, team_size)
                st.session_state["last_result"] = result
            except Exception as error:
                st.session_state["last_error"] = failure(error)
if st.session_state.get("last_error"):
    st.error(st.session_state["last_error"])
if "last_result" in st.session_state:
    show_result(st, st.session_state["last_result"], st.session_state.get("last_team"))
