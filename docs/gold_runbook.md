# ResearchMatch Gold pilot

Gold turns the seven verified Silver profiles into publication-backed profiles,
topic evidence and one bounded search document per researcher. It uses only existing
Silver Delta data. There are no API calls, API keys, new downloads, additional
packages, LLM calls, embeddings, AI Search endpoints or Lakebase resources in this step.

Implemented and replayed locally on **2026-09-18**. The user subsequently confirmed
successful Databricks execution, expected counts, fresh lineage, zero integrity
violations and CDF enabled. Next: [AI Search setup and tests](ai_search_runbook.md).
The instructions below remain the Gold run/recovery reference, not a request to
repeat a completed build. The user's confirmed Silver v2 build is
`3230bb0f-28c7-4830-90b5-5a21034794cb`. Its publication-version checks passed.
Do not repeat Bronze ingestion, identity approvals or the Silver upgrade for this
checkpoint.

## 1. Run in Databricks Free Edition

Local Git changes do not automatically update existing Workspace notebooks.
Upload/import these new files from the project into Workspace.

1. Wait for any Silver build or identity review to finish. Use one builder at a
   time; do not refresh Silver or change identity decisions during a Gold build.
2. Import [SQL 09](../sql/09_create_gold_tables.sql) as a SQL notebook, select
   serverless compute and **Run All**. It creates
   `workspace.researchmatch_gold`, eight Delta tables and seven views. Existing
   Bronze and Silver data are untouched. It enables Change Data Feed on the
   generated search-source table.
3. Import [notebook 10](../notebooks/10_build_gold.py) as a **fresh Python notebook**.
   Select serverless notebook compute. Run all cells, including the definitions
   cell, with the defaults below. Do not run just the final cell in a fresh session.

   | Widget | Value |
   |---|---|
   | `catalog` | `workspace` |
   | `silver_schema` | `researchmatch_silver` |
   | `gold_schema` | `researchmatch_gold` |
   | `mode` | `validate` |
   | `expected_verified_profiles` | `7` |

   The scoring year is inherited from the pinned Silver build, currently 2026.
   There is no separate Gold year widget. If using other schemas, also change
   SQL 09 and SQL 11 consistently.
4. Check for **`Gold validation passed`** and the counts below. This mode reads
   Silver and checks contracts, evidence and bounds but writes no rows or run logs.
   SQL 09 must already exist. Stop and share any error rather than disabling checks.
5. Set widget execution behavior to **Do Nothing** before changing values. Change
   only `mode` to `build`, then **Run All**. Wait for **`Gold build published`**.
6. Import [SQL 11](../sql/11_review_gold.sql) as a SQL notebook and run its read-only
   checks. Expect one current build with `source_is_current=true`, the counts below,
   all seven integrity checks at zero, and Change Data Feed set to `true`.
7. Optionally rerun notebook 10 in `build` without changes. It should report an
   identical published build and perform no writes.

Please share SQL 11's build/freshness row, dataset counts, profile counts and
zero-violation checks before we create the search index. Review the search-text
previews and quality warnings as well.

## 2. Expected results

| Gold dataset | Rows |
|---|---:|
| `researcher_profiles_current` | 7 |
| `researcher_profile_publications_current` | 437 |
| `researcher_profile_topics_current` | 534 |
| `researcher_topic_evidence_current` | 1,170 |
| `profile_quality_metrics_current` | 7 |
| `researcher_search_document_history_current` | 7 |
| `researcher_search_documents` | 7 |

| Researcher | Source links | Contribution groups | Eligible groups | Eligible preprints |
|---|---:|---:|---:|---:|
| Abdallah Tubaishat | 72 | 72 | 72 | 0 |
| Ahed Abugabah | 79 | 79 | 77 | 0 |
| Ahmed Shuhaiber | 88 | 88 | 88 | 3 |
| Andrew Marrington | 53 | 53 | 53 | 0 |
| Dima Rabadi | 11 | 10 | 10 | 2 |
| Fatma Outay | 65 | 65 | 65 | 3 |
| Graham Manville | 72 | 72 | 72 | 0 |

The 439 researcher-contribution groups include two excluded retraction-related
records for Ahed. Only 437 eligible groups become Gold publication evidence; eight
are labelled preprints (a subset, not eight additional rows). Dima's reviewed
Zenodo concept/version pair contributes once, using the version DOI as evidence.

These are **researcher-contribution groups**, not certified counts of distinct,
independent or peer-reviewed studies. Unreviewed versions can remain. Equal titles
are flagged for review, not automatically merged. The preview surfaced a malware
preprint/conference-paper pair for Dima that needs separate version verification.

## 3. What Gold stores

Six immutable snapshot tables have `build_id`, `row_key` and `silver_build_id`:

- `researcher_profiles`: identity, directory-observed colleges, identifiers,
  contribution counts and deterministic summaries.
- `researcher_profile_publications`: eligible representative publications, source
  work IDs, evidence links, provenance and type-labelled excerpts.
- `researcher_profile_topics`: ranked Silver topic scores and support counts.
- `researcher_topic_evidence`: explicit topic-to-publication evidence links.
- `profile_quality_metrics`: coverage, pending candidates and review warnings.
- `researcher_search_document_history`: bounded search documents and the exact
  publication/topic IDs represented in each document.

`build_runs` records input versions, fingerprints, counts and write failures.
`researcher_search_documents` is a separate, stable-key **current generated
projection**, intended as the future AI Search source, not an append-only history.
`document_id` is `researcher:<researcher_id>`.

Only verified identities with accepted decision evidence enter Gold. A verified
identity does not prove current employment, availability or complete author-profile
coverage. Other pending candidates remain pending; this notebook approves nobody.

Funding is `not_established` and `verified_award_count` is **NULL**, not zero.
Publication funding acknowledgements, biography claims and OpenAlex award links
are not converted into grants personally received. The pilot intentionally has no
Gold award table or award-ranking feature. If Silver researcher-award links become
nonempty later, the builder stops for an explicit funding-policy update.

## 4. Search document policy

Documents contain the name, directory-observed college codes, up to ten topics and
up to twelve selected eligible publications. Selection covers top topics first and
then fills with recent work; it is not query-specific ranking or scientific-quality
assessment. All 437 eligible publications remain separately available in Gold.

- Maximum 6,000 characters per document, with complete publication blocks and
  exact supporting IDs. This is a character bound, not an embedding-token guarantee.
- Only source-labelled OpenAlex abstract excerpts enter search text. Repository
  descriptions are kept separately because they can contain non-abstract material.
- Placeholder abstracts such as “No abstract available” are treated as missing.
- Preprints explicitly state that peer review is not established.
- Retractions, concept aliases, raw biographies and unverified grant claims do not
  enter search text.
- Topic support and Silver scores are evidence measures, not calibrated confidence
  or proof of expertise. Single-contribution topics are labelled limited evidence.
- Citation counts and h-index are not used as researcher-quality measures.

Source text is untrusted content. A future explanation model must treat it as
evidence, not instructions, and cite the actual publication rows. The current
builder makes templated summaries only; it does not call an LLM.

## 5. Publication consistency, refresh and recovery

The builder freezes one successful Silver v2 build and the Delta versions read. It
checks that current Silver and its identity-decision version have not changed,
including before publishing. New identity decisions require a Silver rebuild first.

It appends and checks all six Gold snapshots, then marks the run `validated`.
Finally, **one Delta MERGE** replaces the serving projection, deleting obsolete
generated documents there only. All history snapshots remain. This is not a
cross-table transaction.

`current_build` follows the single validated build actually represented in the
serving table, with matching document count. The six `*_current` views follow that
same build. A newer `validated` log alone does not mean a build was published.

- Failure before the serving MERGE leaves the previous published Gold visible.
  On the first build, current views remain empty until publication.
- If the MERGE commits but its response is lost, the serving table/current view
  remains the source of truth. Inspect SQL 11 and `build_runs`; do not delete data.
- After resolving a failure, rerun notebook 10. Partial history is retained for
  diagnosis. An identical already-published fingerprint skips writes.
- Schema errors: confirm that the latest SQL 09 and notebook 10 were imported.
  Do not drop/recreate tables to bypass a contract mismatch.
- Expected-profile errors: review Silver status first; do not blindly raise the
  count guard to include unreviewed researchers.

Run only one producer at a time. Gold does not automatically refresh when Silver
changes. Before future indexing or serving, check that Gold's `silver_build_id`
matches current Silver and that identity decisions have been rebuilt. The app/index
integration must enforce freshness and use matching build IDs when loading evidence.

## 6. Free Edition resource boundaries and next stage

The local replay processed 63,949 projected Silver rows (~26.9 MB serialized),
produced ~1.86 MB of serialized Gold snapshot rows, and a maximum search document
of 5,976 characters. These are application payload estimates, **not Delta disk
usage or measured Databricks compute costs**. The notebook has input/output bounds
and performs a small batch build; it is not an unbounded production pipeline.

After Databricks validation, the next task is one standard Delta Sync AI Search
index using `researcher_search_documents`, key `document_id`, text `search_text`,
managed embeddings and triggered sync. Confirm the available embedding endpoint
and token limit then. Free Edition permits one AI Search endpoint with one unit
and does not support Direct Vector Access.
[Free Edition limits](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations)

The source is already CDF-enabled, as required for standard Delta Sync. No endpoint,
index or embedding model is provisioned by SQL 09 or notebook 10.
[AI Search creation requirements](https://docs.databricks.com/aws/en/ai-search/create-ai-search)

## 7. Local verification

```sh
python3 -m unittest discover -s tests -v
python3 scripts/validate_gold_local.py
```

All 107 tests passed, including Gold contracts, evidence exclusions, topic counts,
stable document IDs, SQL relational checks and simulated publication failures.
The full local replay matched the expected seven-profile counts above. It uses
the existing landed files and an explicitly offline fixture matching the seven
user-confirmed identities; the fixture is **not a decision import or approval tool**.
No Databricks data is changed by either command.

Local tests do not replace live Spark/Delta parsing, permission and integration
checks. Steps 1–7 have now been confirmed by the user for this deployment.
