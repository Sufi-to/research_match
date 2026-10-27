---
name: rm-app-deploy
description: Deploy the ResearchMatch Databricks App (researchmatch-pilot) after app code changes. Uploads app/ files to the workspace source folder, verifies they arrived as plain files identical to the repo, redeploys, and checks the app's permissions. Use whenever app/ code changed and the user wants it live, says "deploy", "redeploy", "push to the app" or "update the app", or when the live app shows an error after a rebuild.
---

# Deploying the ResearchMatch app

The app runs from `/Workspace/Users/<current user>/researchmatch-app`. Deploying copies
that folder into a snapshot and restarts Streamlit. Deploying is an outward-facing
change, so confirm the user wants it live unless they already asked.

## Before deploying

- Run the full test suite: `.venv/bin/python -m unittest discover -s tests`.
  `test_app_sources_are_not_notebooks` matters here: a `.py` file containing
  `# Databricks notebook source`, `# COMMAND` or `# MAGIC` becomes a notebook in the
  snapshot, and imports fail with `ImportError` at runtime.
- `app/researchmatch/matching_policy.py` must stay AST-identical to notebook 14 (parity
  test). Put new app logic in new modules.

## Deploy

```bash
.claude/skills/rm-app-deploy/scripts/deploy_app.sh            # upload everything in app/, verify, deploy
.claude/skills/rm-app-deploy/scripts/deploy_app.sh --check    # verify only, no upload or deploy
```

The script uploads every tracked file under `app/`, re-exports each one and compares
it byte-for-byte with the repo. It fails if any file differs or isn't a FILE, and
deploys only when everything matches. It prints the deployment state.

## After deploying

Ask the user to click **Check connection and data**. If it fails, the error now names
the cause. Read the logs with `databricks apps logs researchmatch-pilot -p tom`; they
hold only `[reference] (stage, ErrorType)`.

The app's service principal needs, and the user grants:
- **USE CATALOG** `workspace`, **USE SCHEMA** on the Gold and Silver schemas, and
  **SELECT** on the tables listed in `docs/app_runbook.md` step 3.
- **SELECT on the index.** This is lost whenever the index is recreated.
- **CAN_USE on the AI Search endpoint.** This is lost whenever the endpoint is recreated.

Check current grants read-only with `databricks grants get table <name> -p tom` and
`databricks permissions get vector-search-endpoints <endpoint id> -p tom`.
