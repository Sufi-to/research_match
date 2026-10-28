"""Curated user guidance for failed requests. Raw exception text is never shown.

service.py tags each exception with the request stage it failed in. Guidance is
chosen from that stage, the exception type name and a few fixed messages raised
by our own code, so SQL, URLs, credentials and query text cannot leak.
"""

GENERIC = ("Request could not be completed. Check the topic/required terms, app permissions, "
           "warehouse availability and notebook 14 validation.")

# Fixed ValueError messages raised by matching_policy/service that are safe to show as-is.
SAFE_INPUT_MESSAGES = (
    "Enter a research topic of 3–500 characters (at most 2,000 UTF-8 bytes)",
    "Required terms must be meaningful terms already present in the topic",
    "Choose one to three candidates per list",
)


def type_names(error):
    return {cls.__name__ for cls in type(error).__mro__}


def describe(error):
    stage = getattr(error, "researchmatch_stage", None)
    names = type_names(error)
    denied = "PermissionDenied" in names or getattr(error, "category", None) == "permission"
    missing = names & {"NotFound", "ResourceDoesNotExist"} or getattr(error, "category", None) == "not_found"
    text = str(error) if "ValueError" in names else ""
    if stage == "input" and text in SAFE_INPUT_MESSAGES:
        return text + "."
    if stage == "endpoint":
        if missing:
            return ("The AI Search endpoint no longer exists. Free Edition can remove idle endpoints: recreate it "
                    "with notebook 12 (mode=create), then grant the app CAN_USE on it (AI Search and app runbooks).")
        if denied:
            return "The app lacks CAN_USE on the AI Search endpoint. Grant it as described in the app runbook, step 3."
        if "not online" in text:
            return "The AI Search endpoint exists but is not online yet. Wait a few minutes, then retry."
    if stage == "index":
        if missing:
            return ("The AI Search index is missing. Recreate it with notebook 12 (mode=create), then re-grant "
                    "the app SELECT on the index.")
        if denied:
            return "The app lacks SELECT on the AI Search index. Re-grant it (needed after any index recreation)."
        if "not ready" in text:
            return "The AI Search index is still syncing. Wait a few minutes, then retry."
        if text:
            return ("The AI Search index does not match the current Gold build. Run notebook 12 in sync, then "
                    "status mode (AI Search runbook).")
    if stage in ("data", "probe"):
        if denied:
            return "The app lacks read access to a Gold or Silver table. Check the grants in the app runbook, step 3."
        if missing:
            return "A Gold or Silver table or view is missing. Check the Gold/Silver builds (SQL 06 and SQL 11)."
        if getattr(error, "category", None) == "timeout":
            return "The SQL warehouse did not answer in time; it may be starting. Wait a minute, then retry once."
        if text:
            return ("Gold/Silver data is inconsistent or mid-rebuild. Finish the rebuild (notebooks 05 and 10) "
                    "and check SQL 11, then retry.")
    if stage == "search" and denied:
        return "The app was denied the search query. Check its CAN_USE on the endpoint and SELECT on the index."
    if stage == "freshness":
        return "The data changed while your request ran, so results were withheld. Retry."
    return GENERIC
