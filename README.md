# ResearchMatch AI

ResearchMatch builds evidence-based Zayed University researcher profiles and matches
a research topic to suitable collaborators. The first pilot covers 50 candidates
across Business and Technological Innovation.

Research data will live in Bronze/Silver/Gold Delta tables, with one AI Search
endpoint. Lakebase is reserved for optional application state.

## Collect the source data

1. Install Python dependencies from `requirements-collection.txt`.
2. Put your OpenAlex key in a local `.env` file as `OPENALEX_API_KEY=...`.
3. Run the collector from the project root:

```sh
python3 scripts/collect_sources.py --source all --batch data/landing/pilot-20260905
```

Downloads preserve original JSON, XML, and HTML. Reuse the same batch path to resume
an interrupted run; choose a new path for a refresh. Data and secrets are ignored by
Git. The collection scripts run locally and do not upload or modify Databricks.

## Load the uploaded collection

The two batches are uploaded under `/Volumes/workspace/researchmatch_bronze/landing/`.
Follow the [Volume-to-Bronze runbook](docs/volume_to_bronze_runbook.md): validate two
OAI files, run the Bronze DDL if needed, load and repeat the sample, then load the
complete collection. The loader defaults to validation only and needs no API key.

## Project files

- [Product plan](docs/product_plan.md): scope, architecture, and delivery milestones.
- [Collection runbook](docs/data_collection_runbook.md): download, resume, verify, and package data.
- [First collection report](docs/collection_report_2026-09-05.md): actual counts, coverage gaps, and local artifact locations.
- [Institutional data contract](docs/institutional_data_contract.md): optional roster/award exports.
- [Bronze DDL](sql/01_create_bronze_tables.sql): creates the 11 planned Bronze tables.
- [Volume-to-Bronze notebook](notebooks/03_load_volume_to_bronze.py): validates manifested files and merges raw versions into Delta.
- [Volume-to-Bronze runbook](docs/volume_to_bronze_runbook.md): exact settings, expected counts, validation, and recovery.
- [Direct OAI notebook](notebooks/02_ingest_zu_scholars_oai.py): alternative direct API-to-Delta route.
- [Silver DDL](sql/04_create_silver_tables.sql): build snapshots, current views, and human identity decisions.
- [Silver builder](notebooks/05_build_silver.py): default validate-only transformation of existing Bronze data.
- [Silver review SQL](sql/06_review_silver.sql): read-only exploration and identity evidence.
- [Silver runbook](docs/silver_runbook.md): run order, expected counts, review decisions, and limits.
- [Identity batch 02](docs/identity_review_batch_02.md): evidence for five more proposed matches and approval instructions.
- [Guarded identity review SQL](sql/07_review_identity_batch_02.sql): previews by default; records only deliberately enabled, checked approvals.
- [Publication-version upgrade](docs/publication_versions.md): evidence-backed Silver v2 grouping and exact Databricks upgrade steps.
- [Publication-version checks](sql/08_check_publication_versions.sql): read-only checks of contribution counts, provenance and eligibility.
- [Gold DDL](sql/09_create_gold_tables.sql): profile/evidence snapshots and a CDF-enabled search-source table.
- [Gold builder](notebooks/10_build_gold.py): validate or publish the seven verified profiles from one frozen Silver build.
- [Gold review SQL](sql/11_review_gold.sql): counts, evidence, freshness, search previews and integrity checks.
- [Gold runbook](docs/gold_runbook.md): exact Databricks steps, expected results, resource bounds and recovery.

The user-confirmed Silver v2 build has seven verified identities, 440 researcher–work
links, 439 contribution groups (437 eligible), and 534 topic-score rows. SQL 08's
version checks passed. Gold is now implemented and tested locally: seven profiles,
437 publication-evidence rows, 534 topics, 1,170 topic-evidence links and seven
search documents. Next: SQL 09 → notebook 10 validate/build → SQL 11, following the
Gold runbook. No new identity approvals or Bronze uploads are needed. Gold has not
yet been run in Databricks; the AI Search endpoint/index and application are deferred.

## Run offline tests

```sh
python3 -m unittest discover -s tests -v
```
