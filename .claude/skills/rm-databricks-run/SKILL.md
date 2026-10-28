---
name: rm-databricks-run
description: Run ResearchMatch work in Databricks from the terminal. Covers a read-only health check of the endpoint, index, app and source retention, executing a workspace notebook (02, 03, 05, 10, 12 or 14) as a one-time serverless job and reading its printed output, plus read-only SQL checks against the warehouse (counts, SQL 06/08/11 checks, identity decisions, table properties). Use this whenever the user asks to rebuild Silver or Gold, sync, check or test the AI Search index, verify counts, inspect live tables, check whether things are still healthy, or run a notebook "for them", even if they don't mention jobs or the CLI.
---

# Running ResearchMatch notebooks and SQL in Databricks

The notebooks are written to be run by hand in the workspace UI. These scripts run
the same workspace copies as one-time jobs, so results match what the user would see.
Databricks access is via CLI profile `tom` (override with `RM_PROFILE`).

## Ground rules (why they matter)

- **Read-only by default.** `validate`/`discover`/`status`/`test` modes and SELECT
  queries are safe. `build`, `create` and `sync` modes, ALTER and INSERT change
  shared state; get the user's go-ahead for each. The user runs deletions and
  GRANTs themselves, and Claude Code's safety checks block them anyway.
- **Workspace copy = repo copy.** Before running, diff the workspace notebook against
  the repo (`databricks workspace export … --format SOURCE`). After changing a
  notebook locally, re-import it (`databricks workspace import … --format SOURCE
  --language PYTHON --overwrite`). Workspace copies may carry an extra
  serverless-environment header; that alone is harmless.
- **Notebook 13 is frozen** and only works against the seven-profile Gold build.

## Health check (start here when anything looks wrong)

```bash
.claude/skills/rm-databricks-run/scripts/health_check.sh
```

This takes about 10 seconds and is read-only. It prints PASS/FAIL for each of these:
- the endpoint `vs_endpoint_1` is ONLINE;
- the index is ready and uses `databricks-gte-large-en`;
- the indexed rows equal Gold `current_build.document_count`;
- the search source table keeps 365 days of history;
- the app is running.

It exits 1 on any failure. Each FAIL names the likely fix, and `references/rebuild.md`
has the details. It does not check the app's grants; the in-app connection check
covers those.

## Run a notebook

```bash
.claude/skills/rm-databricks-run/scripts/run_notebook.sh <label> <notebook> '<json widget params>'
# e.g. Silver validate, then build
.claude/skills/rm-databricks-run/scripts/run_notebook.sh silver-validate 05_build_silver '{"mode":"validate"}'
.claude/skills/rm-databricks-run/scripts/run_notebook.sh gold-build 10_build_gold '{"mode":"build","expected_verified_profiles":"13"}'
.claude/skills/rm-databricks-run/scripts/run_notebook.sh nb12-status 12_setup_and_test_ai_search \
  '{"mode":"status","endpoint_name":"vs_endpoint_1","embedding_model":"databricks-gte-large-en"}'
```

It prints `RUN <id> <SUCCESS|FAILED>` and the notebook's printed output. Notebooks
live in `/Users/<current user>/research_match/notebooks/`. Long runs can take
several minutes, so give the Bash call a long timeout. On failure,
`databricks jobs get-run-output <task run id>` shows the error trace.

## Read-only SQL

```bash
python3 -I .claude/skills/rm-databricks-run/scripts/sql_readonly.py query.sql [schema]
```

The script refuses anything but SELECT/WITH/SHOW/DESCRIBE, so it is safe for checks.
The default schema is `researchmatch_silver`; pass `researchmatch_gold` for Gold.
To run a check from `sql/06|08|11`, split the file on `-- COMMAND ----------`.
Cell 0 is the `USE` header, so take the cell index after that.

## Rebuild order and known traps

Read `references/rebuild.md` before a Silver → Gold → index → app rebuild. It holds
the step order, expected checks and the Free Edition failures seen so far
(deleted idle endpoint, change-history retention, lost grants after index
recreation).
