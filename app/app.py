"""Run with streamlit run app.py from this directory; no remote calls on page load."""
import uuid
import streamlit as st

from researchmatch.presentation import show_result
from researchmatch.service import execute

st.set_page_config(page_title="ResearchMatch · ZU pilot", page_icon="🔎", layout="wide")
st.title("ResearchMatch")
st.caption("Zayed University · Seven-researcher pilot · Evidence first")
st.write("Explore potential collaborators through their collected publications.")
st.info("Experimental matching: direct and partial evidence are shown separately. "
        "Identity verification does not confirm current employment or collaboration availability.")

with st.sidebar:
    st.header("Pilot controls")
    validate = st.button("Check connection and data")
    clear = st.button("Clear displayed results")
    st.caption("Uses the existing SQL warehouse and AI Search index. "
               "Each submitted request uses compute; typing does not run a search.")
    st.caption("No saved searches or feedback database. Avoid confidential proposal details. "
               "The platform may retain operational logs.")

with st.form("research_topic_form"):
    topic = st.text_area("Research topic", max_chars=500,
                         placeholder="For example: Machine learning for brain tumor detection")
    required = st.text_input("Required terms (optional)", max_chars=500,
                             help="Terms must already occur in your topic and must be supported directly.")
    top_k = st.selectbox("Maximum people per result list", [3, 2, 1])
    submit = st.form_submit_button("Find evidence-backed candidates")

if clear:
    st.session_state.pop("last_result", None)
    st.session_state.pop("last_error", None)
if submit or validate:
    # Clear earlier success before a request, so an error never displays stale success.
    st.session_state.pop("last_result", None)
    st.session_state.pop("last_error", None)
    if submit and (not 3 <= len(topic.strip()) <= 500 or not topic.strip()):
        st.session_state["last_error"] = "Enter a research topic of 3–500 characters."
    else:
        with st.spinner("Checking current data and evidence…"):
            try:
                st.session_state["last_result"] = execute(None if validate else topic, required, top_k)
            except Exception as error:
                ref = uuid.uuid4().hex[:10]
                # Type and reference only: never log query text, token, raw response or SQL.
                print(f"ResearchMatch request failed [{ref}] ({type(error).__name__})")
                st.session_state["last_error"] = (
                    f"Request could not be completed (reference {ref}). Check the topic/required terms, "
                    "app permissions, warehouse availability and notebook 14 validation. "
                    "No previous result is being shown. This is not a finding of insufficient expertise.")
if st.session_state.get("last_error"):
    st.error(st.session_state["last_error"])
if "last_result" in st.session_state:
    show_result(st, st.session_state["last_result"])
