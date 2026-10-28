# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this project is

ResearchMatch AI builds evidence-based profiles of Zayed University (ZU) researchers
and matches a research topic to suitable ZU collaborators. It runs on **Databricks
Free Edition** using a Bronze → Silver → Gold Delta medallion in Unity Catalog
(`workspace` catalog), one AI Search (vector search) index, and a read-only
Streamlit Databricks App. Lakebase is reserved for future app state only.

`docs/product_plan.md` is the source of truth for scope and architecture. The
status paragraph near the end of `README.md` is the running checkpoint log of what
the user has confirmed in Databricks.

## Layout

- `scripts/collect_sources.py` — local collector (ZU faculty pages, ZU Scholars
  OAI-PMH, OpenAlex) writing immutable landing batches under `data/landing/`.
  `package_collection.py` packages a batch; `validate_*_local.py` replay notebook
  logic against local data.
- `sql/` — numbered DDL and read-only review SQL (`01` Bronze, `04` Silver,
  `09` Gold; `06`, `07`, `08`, `11` are review/check scripts).
- `notebooks/` — Databricks source-format notebooks (`# Databricks notebook source`,
  `# COMMAND ----------`), numbered in run order: `02`/`03` Bronze load, `05` Silver,
  `10` Gold, `12` AI Search setup, `13` matching v1, `14` strict matching
  (`matching_v2_direct_evidence`).
- `app/` — Databricks App (`app.py`, `app.yaml`, `researchmatch/` package). Only the
  *contents* of this folder are uploaded to the workspace.
- `docs/` — one runbook per stage plus review/evaluation records.
- `tests/` — offline `unittest` suite; fixtures in `tests/fixtures/`.

## Commands

```sh
# Full environment (needed for all tests to run, incl. collector and app UI tests)
.venv/bin/pip install -r requirements-collection.txt -r app/requirements.txt
.venv/bin/python -m unittest discover -s tests -v

# Single test module (run from repo root; tests import each other by module name)
.venv/bin/python -m unittest discover -s tests -p "test_strict_matching.py" -v

# Collect source data (needs OPENALEX_API_KEY in .env)
python3 scripts/collect_sources.py --source all --batch data/landing/<batch-name>

# Run the app locally (page renders without credentials; live calls need SDK auth)
.venv/bin/streamlit run app/app.py --server.address=127.0.0.1
```

`.venv` is a Python 3.12 environment; `.venv/bin/python3` must link to
`/usr/bin/python3.12`, not `/usr/bin/python3` (now 3.14 after an OS upgrade). If the
venv suddenly reports missing packages or no `pip`, check that link first.

App UI tests are skipped (not failed) when `app/requirements.txt` isn't installed;
`test_collect_sources` errors on import if `requests` isn't installed.

## Project skills (`.claude/skills/`)

- `rm-databricks-run`: run workspace notebooks as one-time jobs and read their output;
  read-only SQL checks; `scripts/health_check.sh` (endpoint, index rows vs Gold, app,
  retention: run it first when something breaks); rebuild order and Free Edition
  traps (`references/rebuild.md`).
- `rm-app-deploy`: upload `app/`, verify the workspace copy byte-for-byte, redeploy,
  and check the app's grants.
- `rm-pre-push`: scan unpushed commits for emails, workspace details and secrets
  before any push (the GitHub repo is public).

## Conventions and invariants

- **Notebooks default to validate-only.** Writes/mutations happen only through an
  explicit widget mode (e.g. notebook 14: `validate` → `match` → `evaluate`). Keep
  that default for any new notebook.
- **Notebooks are standalone.** Shared helpers are *copied*, not imported, so each
  notebook can be imported into a Workspace alone. AST-parity tests enforce that
  copies stay identical:
  - notebook 12 helpers ↔ notebook 13 (`tests/test_matching.py`)
  - notebook 13 shared functions ↔ notebook 14 (`tests/test_strict_matching.py`)
  - notebook 14 ↔ `app/researchmatch/matching_policy.py` (`tests/test_app.py`)

  If you change a mirrored function, change every copy, or the parity tests fail.
- **Do not modify notebook 13.** It is preserved unchanged for comparison with 14,
  and only works against a seven-profile Gold build. Notebooks 12/14 and the app take
  the profile count from Gold's `document_count` (upper bound `MAX_PROFILES = 50`).
  Matching notebooks and the app must not write to Delta (tests assert this).
- **Do not recreate Databricks resources.** Reuse the existing SQL warehouse,
  STANDARD endpoint `vs_endpoint_1`, and index
  `workspace.researchmatch_gold.researcher_profiles_index` (model
  `databricks-gte-large-en`). Free Edition allows one warehouse and one search
  endpoint. No new identity approvals, Bronze uploads or Gold rebuilds are needed
  unless the user asks.
- **Databricks actions need explicit approval.** Build and test locally first.
  Read-only CLI checks are fine. Creating, deploying or modifying resources needs
  the user's go-ahead, and the user runs deletions and permission grants
  themselves. Only record a step as "confirmed" in docs once the user has
  confirmed it.
- **Evidence over inference.** Recommendations must be backed by publication
  evidence; return "insufficient evidence" rather than guess. Missing funding data
  is never zero funding. Retrieval smoke checks are not accuracy claims — no
  independent relevance labels exist yet.
- **Secrets and data.** `.env`, `data/` and `.venv/` are git-ignored. Never commit
  them, put them in the `app/` folder, or upload them to a Volume.
- **Docs follow code.** When adding a stage, add its runbook in `docs/`, link it in
  the README "Project files" list, and update the README/product-plan status
  paragraphs.
- Commit messages use conventional prefixes (`feat:`, `fix:`, `docs:`).

## Current state (as of 2026-10-28)

Pilot of 27 verified researcher identities after batch 05 (target: ~50 across
Business and Technological Innovation). Bronze, Silver v2, Gold, AI Search index, notebooks
13/14 and the deployed app `researchmatch-pilot` are user-confirmed in Databricks.
The independent relevance review (`docs/matching_evaluation_review.md`) was **deferred by
the user on 2026-10-28** (labeller and format still to choose). Coverage without new
collection is exhausted (batch 06 lookup found nobody; see `docs/identity_review_batch_05.md`).
**Next coverage needs a source refresh**, and identity evidence hashes include source record
keys, so a refresh would make all accepted decisions stale. Design a re-attestation path
(carry forward unchanged ORCID/author links with one bulk approval) before refreshing.

**2026-10-27:** Free Edition had deleted endpoint `vs_endpoint_1`. It was recreated
(same name, STANDARD) via notebook 12 run as one-time jobs; the orphaned index
catalog entry was deleted and the index rebuilt from unchanged Gold
(`9c1571ed…`, source version 2). `status` content scan passed and all 16 `test`
searches matched the original results (14 hypothesis hits, 2 out-of-scope).
Idle endpoints can be removed again — re-check before relying on them. The user
deleted `day-2-app` to free an app slot; app `researchmatch-pilot` was created
(warehouse resource key `sql-warehouse`), deployed from
`/Workspace/Users/<you>/researchmatch-app`, and its service
principal was given the runbook's read-only USE/SELECT grants.
It also needed CAN_USE on the endpoint (now in the runbook), and
`matching_policy.py` had to lose its notebook markers. The user confirmed the in-app
connection check (7 researchers, 437 publications, 19 same-title groups) and
reported good results from their own live searches.

**2026-10-28:** Batch 03 (six ORCID-verified CTI researchers) approved and rebuilt:
Silver `b239f898…`, Gold `6fcc7472…`, 13 profiles, 1,024 publications, 37 same-title
groups. The index source table now keeps 365 days of Delta history (a sync had failed
on the 7-day default). The app caches validated state; query history confirmed one
cold validation (~40 s) and then one statement per search. Batch 04 (split-ORCID
records: Seffah, AlSinglawi, Evangelista, Iqbal, plus Ahed Abugabah's extra records)
approved and rebuilt: Silver `0886c97d…`, Gold `164549a3…`, **17 profiles**, 1,387
publications; index synced and tested, app redeployed, health check all PASS. Business
profiles list no ORCID, so batch 05 (`sql/17`) uses the public ORCID registry (single
match with current ZU employment, ORCID pinned and required on the OpenAlex author);
the user approved ten Business researchers and the rebuild gave Silver `a3d5ec1a…`,
Gold `7fae22e6…`, **27 profiles**, 1,529 publications; index synced (content scan
passed; 13/14 search hypotheses, the `adoption` ANN miss reflecting the larger pool),
health check all PASS.

Databricks access: CLI profile `tom` (OAuth). Use read-only commands by default;
create/delete/modify resources only with explicit user approval. Independent relevance labels, broader profile coverage, funding
evidence and validated team fit remain future milestones.
