# Load the uploaded files into Bronze

The next task is to validate the uploaded collection, load a small sample into
Delta, and then load the complete batch. Do not build Silver or AI Search yet.

## 1. Files and prerequisites

The user-provided landing location is already the notebook default:

```text
/Volumes/workspace/researchmatch_bronze/landing/
  pilot-20260905/
    manifest.json
    raw/...
    derived/...
  openalex-20260905/
    manifest.json
    raw/...
    derived/...
```

Use a normal underscore in `researchmatch_bronze`, with no backslash. The two
folders must be extracted, not just uploaded as a `.tar.gz` archive.

Import [`03_load_volume_to_bronze.py`](../notebooks/03_load_volume_to_bronze.py) as
a Python notebook in Databricks and use notebook serverless compute. This is one
self-contained notebook: no dependency installation, API calls, or OpenAlex key.
The user running it needs access to read the Volume and write the Bronze tables.

Before the first **load**, run
[`01_create_bronze_tables.sql`](../sql/01_create_bronze_tables.sql) as a SQL notebook.
It creates the 11 tables if absent; it does not replace tables or upgrade existing
schemas. The loader checks that the selected tables match the expected Delta schema.

## 2. Validate a small sample first

Run the Python notebook with its defaults:

| Widget | Value |
|---|---|
| `landing_path` | `/Volumes/workspace/researchmatch_bronze/landing/` |
| `batch_folders` | `pilot-20260905,openalex-20260905` |
| `catalog` | `workspace` |
| `bronze_schema` | `researchmatch_bronze` |
| `datasets` | `zu_scholars_oai_raw` |
| `mode` | `validate` |
| `max_files_per_dataset` | `2` |
| `write_batch_rows` | `1000` |
| `write_batch_mib` | `8` |

This reads two manifested OAI files, checks file sizes and SHA-256 checksums,
parses individual records, and validates types and required fields. It writes
**nothing**, including no ingestion logs. Check that `errors` is empty and the
notebook reports validation passed. It does not need the tables to exist yet.

## 3. Load that sample and prove reruns are safe

After running the DDL, change only `mode` to `load` and run again. Validation
still happens before any business-table writes. The selected records are merged
into `workspace.researchmatch_bronze.zu_scholars_oai_raw`.

The run should be `partial` because the two-file limit intentionally excludes
the rest of the collection. Repeat the same load: on a healthy single-writer run,
`records_inserted` should be zero on the repeat, and the table row count should
remain unchanged. A new ingestion-run log is expected each time.

For the current batch, these first two files contain 200 distinct OAI records.

```sql
SELECT dataset_name, status, records_received, records_inserted,
       records_unchanged, error_message, started_at
FROM workspace.researchmatch_bronze.ingestion_runs
ORDER BY started_at DESC;

SELECT COUNT(*) AS rows_loaded,
       COUNT(DISTINCT record_key) AS distinct_versions,
       COUNT(DISTINCT oai_identifier) AS distinct_source_records
FROM workspace.researchmatch_bronze.zu_scholars_oai_raw;
```

Stop here if the sample or repeat test fails. Do not start a full load until these
checks pass in the actual Free Edition workspace.

## 4. Load the complete collection

Set `datasets=all`, `max_files_per_dataset=0`, and `mode=validate`. Run and inspect
the summary. If it passes, change `mode=load` and run again. Retain the default
write-buffer settings initially.

Alternatively, process one dataset at a time using its table name in `datasets`,
with the file limit set to zero. This gives shorter, separately observable runs.
Do not run overlapping loaders concurrently: Delta does not enforce uniqueness of
`record_key`, and this workflow assumes one writer.

Expected counts from full **local** validation of the collected batches:

| Bronze business table | Source files read | Record occurrences | Distinct versions |
|---|---:|---:|---:|
| `zu_faculty_pages_raw` | 56 | 56 | 56 |
| `zu_faculty_discovered_profiles` | 6 listing files reused | 167 | 167 |
| `zu_scholars_oai_raw` | 81 | 8,006 | 8,006 |
| `openalex_institutions_raw` | 1 | 1 | 1 |
| `openalex_authors_raw` | 36 | 189 | 164 |
| `openalex_works_raw` | 144 | 13,625 | 12,721 |
| `openalex_awards_raw` | 99 | 2,283 | 2,283 |
| `openalex_funders_raw` | 32 | 1,564 | 1,564 |

These are expected final counts in initially empty tables after loading only this
collection, not guaranteed insert counts on a rerun. Existing versions may already
be present. The 164 author versions represent 129 distinct author IDs; different
source payloads for an author are intentionally preserved. Directory discoveries
are listing appearances, not a count of distinct researchers or the 50-person
pilot. Missing-link cards remain in raw HTML but cannot enter the non-null URL table.

`ingestion_runs` and `ingestion_errors` receive operational logs.
`zu_grant_awards_raw` stays empty: there is no approved institutional award export.
OpenAlex awards are external funding evidence, not verified grants received by ZU
or by an individual researcher.

## 5. What the loader does

- Reads manifests rather than guessing formats from every file in the Volume.
- Verifies the selected files against their recorded byte lengths and checksums.
- Splits OAI XML and OpenAlex response pages into individual raw records.
- Preserves HTML, per-record XML, and complete canonical JSON, including nested fields.
- Adds version hashes, fetched timestamps, sanitized request URLs, and ingestion-run IDs.
- Rebuilds directory discoveries from verified listing HTML; does not treat derived
  identity candidates as approved researcher identities.
- Merges only missing version keys into business tables. A changed payload creates
  a new version; exact repeats do not overwrite existing rows or their first-run lineage.
- Records source file paths, checksums, and manifest checksums in each run's context.

Protocol responses, robots files, and `derived/` collection aids remain in the
Volume; this loader does not create separate Delta tables for them. Source files
are never modified or deleted. No Silver/Gold tables, search endpoints, or Lakebase
resources are created at this stage.

## 6. Free Edition execution and recovery

This is an on-demand, sequential batch workflow. It avoids continuous streaming,
Spark caching, RDDs, and per-record writes. Write buffers are capped by row count
and approximate payload bytes. Python parses one source file at a time; the local
adapters reject files over 32 MiB or individual rows over 4 MiB. The current files
fit these limits. Larger future collections need a distributed file-based adapter.

Using pre-downloaded files removes API waiting/retries from Databricks; parsing,
validation, and Delta merges still consume compute. A load validates the selected
files first and reads them again during writing, trading extra I/O for early
failure detection. No claim of a measured compute saving is made.

The design follows Databricks' documented
[Volume file access](https://docs.databricks.com/aws/en/volumes/volume-files),
[Delta merge behavior](https://docs.databricks.com/aws/en/delta/merge), and
[serverless limitations](https://docs.databricks.com/aws/en/compute/serverless/limitations).

If validation detects corrupt/missing payloads in `load` mode, it records failures
before writing any business table. Invalid widget settings, manifests, or target
schemas fail earlier and are reported by the notebook itself. A failure during
loading may leave earlier chunks or tables committed; fix the cause and rerun the
same selection. Do not truncate or overwrite tables to recover. An interrupted
process can leave a run marked `started`; inspect its output before retrying.

```sql
SELECT dataset_name, error_type, error_message, request_context_sanitized, occurred_at
FROM workspace.researchmatch_bronze.ingestion_errors
ORDER BY occurred_at DESC;

-- Expect no duplicate logical version keys. Repeat for the other business tables.
SELECT record_key, COUNT(*) AS copies
FROM workspace.researchmatch_bronze.openalex_works_raw
GROUP BY record_key
HAVING COUNT(*) > 1;
```

`pages_requested=0` means no network requests. Successful runs count duplicate
source occurrences and existing target versions as `records_unchanged`.
`records_updated=0` because business rows are insert-only. File-level or write-level
failures do not provide a reliable failed-record count, so `records_failed` remains
zero; use `status`, `error_message`, and `ingestion_errors` to detect failures.

## 7. Validation status and next milestone

The adapters have passed local validation against both complete downloaded batches.
Offline tests cover DDL contracts, adapters, integrity checks, version keys, buffer
deduplication, and generated merge statements. Spark/Delta execution has **not**
been tested from this environment; the bounded load and repeat above are the
required live smoke test.

Once all eight business datasets have successful full-load logs, counts are
reconciled, and repeat-load behavior is confirmed, proceed to Silver: review
researcher identity matches, deduplicate works by stable IDs/DOIs, and build
researcher–publication links. Keep unverified funding attribution separate.
