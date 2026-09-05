# Bronze implementation runbook

> **Collection update (2026-09-05):** The primary route now collects source files outside
> Databricks and uploads them to a Volume. Follow
> [`data_collection_runbook.md`](data_collection_runbook.md) first. The direct OAI
> notebook steps below are an alternative, not a loader for the new landed files.

> **Scope notice:** See [`product_plan.md`](product_plan.md) for the current product
> direction. The Bronze DDL is award-focused: it includes institutional ZU awards and
> OpenAlex awards/funders.

## Artifacts

- `sql/01_create_bronze_tables.sql` creates the ResearchMatch Bronze Delta schema and tables.
- `notebooks/02_ingest_zu_scholars_oai.py` harvests ZU Scholars OAI-PMH records.
- `docs/bronze_source_assessment.md` explains the source and field decisions.

The default target is `workspace.researchmatch_bronze`. If the Free Edition workspace uses a different writable catalog, change the `USE CATALOG` statement in the SQL script and the notebook's `catalog` widget together.

## 1. Create the tables

Import or open `sql/01_create_bronze_tables.sql` as a Databricks SQL notebook and run all cells. The script is idempotent: existing tables are not replaced.

It creates:

- two ingestion-control tables;
- two ZU faculty crawl tables, including the optional discovered-profile manifest;
- one ZU Scholars OAI table;
- one approved institutional ZU award table;
- five OpenAlex raw tables: institutions, authors, works, awards, and funders.

The raw source tables use a SHA-256 `record_key` as their logical version key. Delta Lake does not enforce this key, so each ingestion notebook must perform an idempotent insert or merge.

## 2. Run a bounded ZU Scholars test

Import `notebooks/02_ingest_zu_scholars_oai.py` as a Python notebook. Use these parameters for the first test:

| Widget | Value |
|---|---|
| `catalog` | `workspace` |
| `bronze_schema` | `researchmatch_bronze` |
| `base_url` | `https://zuscholars.zu.ac.ae/do/oai/` |
| `metadata_prefix` | `oai_dc` |
| `set_spec` | `publication:works` |
| `harvest_from` | blank |
| `harvest_until` | blank |
| `max_pages` | `2` |
| `write_batch_size` | `1000` |
| `request_timeout_seconds` | `60` |
| `request_delay_seconds` | `0.25` |

`max_pages=2` intentionally produces a `partial` run when more pages exist. It is a safe connectivity and schema test, not an incremental checkpoint.

Validate the run:

```sql
SELECT *
FROM workspace.researchmatch_bronze.ingestion_runs
ORDER BY started_at DESC;
```

```sql
SELECT
  count(*) AS rows_loaded,
  count(DISTINCT record_key) AS distinct_versions,
  count(DISTINCT oai_identifier) AS distinct_oai_records,
  min(oai_datestamp) AS earliest_datestamp,
  max(oai_datestamp) AS latest_datestamp
FROM workspace.researchmatch_bronze.zu_scholars_oai_raw;
```

```sql
SELECT oai_identifier, oai_datestamp, set_specs, left(raw_record_xml, 500) AS xml_sample
FROM workspace.researchmatch_bronze.zu_scholars_oai_raw
ORDER BY fetched_at DESC
LIMIT 10;
```

## 3. Prove idempotency

Run the same two-page test again. The second run should receive records but insert zero new versions when the repository payload and datestamps are unchanged. Those rows should be counted as `records_unchanged`.

Check for duplicate logical versions:

```sql
SELECT record_key, count(*) AS copies
FROM workspace.researchmatch_bronze.zu_scholars_oai_raw
GROUP BY record_key
HAVING count(*) > 1;
```

The query should return no rows. Run only one copy of the OAI notebook at a time; the current anti-join append is idempotent for serial runs, not concurrent writers.

## 4. Run the complete initial harvest

Set `max_pages=0`, leave both date widgets blank, and run the notebook. It follows resumption tokens until the repository returns no next token.

The notebook stores only metadata XML. It does not retrieve URLs found in `dc:identifier`, so it complies with the repository's restriction on automated full-content harvesting.

## 5. Refresh strategy

The endpoint supports `from` and `until`, but testing returned some earlier record datestamps in a date-bounded identifier request. Until a complete refresh comparison proves that date filtering is reliable:

1. run periodic full `publication:works` harvests;
2. rely on `record_key` to avoid duplicate versions;
3. compare the complete set of current identifiers to detect records that disappeared, because the repository declares `deletedRecord=no`;
4. treat date-bounded runs as an optimization, not the sole completeness mechanism.

## 6. Failure handling

- HTTP 429 and 5xx responses are retried with capped exponential backoff.
- Successfully parsed records from earlier pages are flushed even if a later page fails.
- Request and record failures are written to `ingestion_errors`.
- A failed run retains its last resumption token for diagnosis, but the safest recovery is to rerun from the beginning; existing versions are skipped.
- API keys and publication files are not involved in this notebook.

Inspect failures with:

```sql
SELECT *
FROM workspace.researchmatch_bronze.ingestion_errors
ORDER BY occurred_at DESC;
```

## Next implementation

After the full OAI harvest succeeds, implement the remaining award-focused ingestion in this order:

1. define and validate the approved ZU award-delivery contract, including visibility classifications;
2. load each institutional delivery into `zu_grant_awards_raw` without exposing restricted fields;
3. fetch the verified OpenAlex institution `I91044093`;
4. extract and batch-resolve ZU Scholars DOIs;
5. cursor-page works affiliated with `I91044093` using selected fields;
6. resolve roster researchers to author IDs using ORCID and DOI-authorship evidence;
7. fetch author details only for resolved ZU researchers and selected external candidates;
8. fetch candidate OpenAlex awards and their linked funders, retaining them as evidence until Silver verifies researcher attribution.
