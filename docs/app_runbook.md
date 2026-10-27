# ResearchMatch read-only Databricks App pilot

## Checkpoint and scope

The user confirmed notebook 14's validate/match/evaluate runs and accepted moving
on to the interface. No completed independent relevance labels were supplied, so
the app remains an experimental, seven-researcher evidence browser. It is not a
validated expert recommender. Notebook 14's strict rules are unchanged.

The source folder is [`app/`](../app). This is a **Databricks App, not another
notebook**. It displays direct-term candidates separately from partial evidence,
publication links, missing terms, preprints, possible versions and build provenance.
It does not infer funding receipt, save searches or capture feedback.

**Suggested team** (results option, sizes 2–4) uses the same single search. It picks
a lead whose displayed publications cover the most interpreted terms, then adds
people who cover terms nobody else covers yet; anyone adding nothing new is
skipped. If one person already covers every term, the team is just that lead.
Terms no displayed publication covers are listed, never filled in. This is
evidence coverage (`app/researchmatch/team.py`), not validated team fit;
coauthorship, funding and availability are not used.
No Lakebase instance is needed for this read-only stage; future persistent app state
belongs there, not in the research tables.

The app reads the existing Gold and Silver control tables through the existing SQL
warehouse and queries the existing AI Search index. There are no Delta writes,
resource creation/sync calls, new embedding models or source downloads in the app.
It authenticates with its Databricks-managed service principal; do not copy your
OpenAlex key, `.env`, personal token or any password into its source folder.

## Free Edition constraints

The current official documentation permits up to three apps, stops each app after
up to 24 hours of running, and limits the workspace to one 2X-Small SQL warehouse
and one AI Search endpoint. Reuse the existing warehouse and `vs_endpoint_1`; do
not provision replacements. Availability depends on workspace quota. This is a
non-commercial experiment, not always-on production hosting.
[Free Edition limits](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations)

## 1. Upload only the app source

1. In Workspace create a folder named `researchmatch-app` under your own user folder.
2. Upload the **contents of the local `app` folder**, preserving the `researchmatch`
   subfolder. Upload Python files as workspace **files**, not notebooks.
3. Confirm the deployment folder has this structure:

   ```text
   researchmatch-app/
     app.py
     app.yaml
     requirements.txt
     researchmatch/
       __init__.py
       matching_policy.py
       presentation.py
       service.py
       warehouse.py
   ```

Do not upload the repository root, `.env`, `.venv`, raw data, tests or caches.
`matching_policy.py` mirrors the needed notebook 14 functions and constants; an
automated parity test prevents policy drift. No earlier notebook needs executing.

## 2. Create one custom app and attach existing resources

1. Open **Databricks Apps** from the app switcher (navigation labels can vary),
   choose **Create app → Custom app**, and name it `researchmatch-pilot`.
2. Under App resources add your **existing SQL warehouse** with **Can use**.
   Keep its resource key exactly `sql-warehouse`: `app.yaml` uses that key to supply
   `DATABRICKS_WAREHOUSE_ID`. Do not paste a JDBC URL or HTTP path there.
3. Add the existing AI Search / Vector search index
   `workspace.researchmatch_gold.researcher_profiles_index` with **Can select**.
   Do not create a new index. The app validates its exact name and model.
4. Find the app's service principal in its authorization/settings page. This is a
   separate identity from your own notebook user, so notebook success does not
   prove app permissions are ready.

Resource setup references: [SQL warehouse](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/sql-warehouse),
[AI Search index](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/vector-search).

## 3. Grant only the read access the app needs

In Catalog Explorer's Permissions controls, grant the app service principal:

- `USE CATALOG` on `workspace`.
- `USE SCHEMA` on `workspace.researchmatch_gold` and `workspace.researchmatch_silver`.
- `SELECT` on these existing objects only:

  | Schema | Objects |
  |---|---|
  | `researchmatch_gold` | `current_build`, `researcher_search_documents`, `researcher_profile_publications`, `researcher_profile_topics`, `researcher_topic_evidence`, `researcher_profiles_index` |
  | `researchmatch_silver` | `current_build`, `identity_decisions` |

Adding the index resource may have supplied the Gold catalog/schema/index grants
already. The app uses the base evidence tables with Delta version pinning, not just
their `_current` views. It checks identity-decision history but does not approve or
write identities. `SELECT` on an entire schema, ownership, `MODIFY` and `ALL PRIVILEGES`
are not needed. Restrict access to the source-code folder appropriately as well.

Also give the service principal **CAN_USE** on the AI Search endpoint `vs_endpoint_1`
(Compute → AI Search → endpoint → Permissions). Table grants do not cover the
endpoint, and without it the connection check fails with `PermissionDenied`. A
recreated endpoint starts without this entry. CLI equivalent (additive `update`,
not `set`):

```sh
databricks permissions update vector-search-endpoints <endpoint-id> --json \
  '{"access_control_list":[{"service_principal_name":"<app-sp-application-id>","permission_level":"CAN_USE"}]}'
```

App `.py` files must not contain notebook markers (`# Databricks notebook source`,
`# COMMAND`, `# MAGIC`). Databricks converts such files to notebooks in the
deployment snapshot, and the import then fails. `tests/test_app.py` checks this.

If endpoint metadata/scan access is denied, inspect permissions on the **existing**
endpoint/index and share the access error category. Do not grant ownership or
disable content checks as a shortcut. If a live search specifically reports an
embedding-serving permission failure, inspect access to the existing
`databricks-gte-large-en`; do not create another serving endpoint.

This pilot uses **app authorization**: everyone allowed to use the app sees the
same service-principal-accessible seven-researcher data. Restrict the app to yourself
and approved pilot reviewers. It does not implement per-user research permissions.
No user token is read from request headers or displayed.
[Databricks app authorization](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth)

## 4. Deploy and check the app

1. Click **Deploy**, select your `researchmatch-app` workspace folder, and deploy.
   That folder must directly contain `app.yaml`, not an extra nested `app` folder.
   Databricks installs the pinned dependencies and starts Streamlit.
2. Open the app. Loading the page and typing in the form perform **no data queries**.
3. Click **Check connection and data**. With unchanged data, expect seven researchers,
   437 eligible publications and 19 unverified same-title groups. This runs SQL reads
   and an index-content scan, but no embedding/search inference.
4. Enter `RFID implementation in hospitals` and submit. The local evidence replay
   produced a direct-term Ahed candidate; live results still need checking.
5. Try the previous medical-imaging and transport queries. Expect the partial-evidence
   section to be useful; empty direct matches are not an app error.
6. Try `Exoplanet atmospheric spectroscopy and stellar nucleosynthesis`. Expect
   insufficient evidence, not invented astronomy recommendations.
7. Expand a publication. Check its title, source link, abstract excerpt, missing
   terms, preprint type and possible-version warning. Compare with notebook 14.
8. Share the connection check and any error reference/category, without credentials.

[Workspace-folder deployment](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/deploy)
and [app.yaml configuration](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/app-runtime).

## Compute, freshness and privacy

Each explicit check/search revalidates the current Gold/Silver/identity state,
index contents and pinned evidence versions. A search uses at most one AI Search
query. The SQL adapter only accepts the bounded read statements generated by our
helpers, cancels a statement after a 50-second wait and rejects truncated results.
It does not follow returned external URLs or retry resource mutations.

There is intentionally no shared cache or automatic refresh in this first version.
The repeated integrity reads can make requests slower; measure actual warehouse
usage before considering a cache. Do not repeatedly submit while the warehouse is
starting. On a timeout, check/start the existing warehouse and retry manually.

Displayed results are point-in-time snapshots held only in the current Streamlit
session. They are cleared on a new request or failure. Click Clear displayed results
to remove them; closing/restarting the app can also discard them. There are no saved
searches or durable user sessions. This is not a promise that the platform keeps no
logs: do not submit confidential proposals.

Source text is rendered as plain text; only HTTPS links to the known DOI, OpenAlex
and ZU hosts are enabled. Raw search scores are not shown as confidence. Error logs
contain a reference and exception type, not raw SQL/API messages or user queries.

## Local verification and limits

Checkpoint: **232 tests passed with no skips** in the app-enabled local environment,
including four Streamlit interaction tests, the SQL-adapter/service composition
test and the no-notebook-markers check. Deployed 2026-10-27 as `researchmatch-pilot`
from `/Workspace/Users/<you>/researchmatch-app`. The user confirmed the connection
check and reported good results from their own live searches.

```sh
python3 -m venv .venv
.venv/bin/pip install -r app/requirements.txt
.venv/bin/pip install -r requirements-collection.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/streamlit run app/app.py --server.address=127.0.0.1
```

Local page rendering needs no credentials. Live buttons require configured SDK
authentication and `DATABRICKS_WAREHOUSE_ID`; use the managed app route above for
the user-run deployment. Tests mock live APIs and exercise SQL decoding, pagination,
lineage failures, policy parity, safe links, form submission and stale-result clearing.
They cannot prove your app service principal's permissions or warehouse compatibility.

Keep the deployment private to you and approved pilot reviewers. Independent
relevance labelling remains outstanding, despite the user's acceptance of the
pilot. Free Edition stops apps after up to 24 hours and can remove an idle AI Search
endpoint. If checks fail later, confirm `vs_endpoint_1` still exists before
anything else.
