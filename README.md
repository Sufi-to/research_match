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
Git. The current scripts collect locally and do not upload or modify Databricks.

## Project files

- [Product plan](docs/product_plan.md): scope, architecture, and delivery milestones.
- [Collection runbook](docs/data_collection_runbook.md): download, resume, verify, and package data.
- [First collection report](docs/collection_report_2026-09-05.md): actual counts, coverage gaps, and local artifact locations.
- [Institutional data contract](docs/institutional_data_contract.md): optional roster/award exports.
- [Bronze DDL](sql/01_create_bronze_tables.sql): creates the 11 planned Bronze tables.
- [Direct OAI notebook](notebooks/02_ingest_zu_scholars_oai.py): alternative direct API-to-Delta route.

Next: review the collected roster/author candidates, create a managed landing Volume,
and implement the manifest-driven Volume-to-Bronze loader.

## Test the collector

```sh
python3 -m unittest discover -s tests -v
```
