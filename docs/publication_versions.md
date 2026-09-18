# Silver v2: reviewed publication versions

## Confirmed problem and evidence

The user confirmed build `a5f7a124-429b-431c-b4b4-1f913b1e4e7f`:
7 verified identities, 116 pending, 440 researcher–work links, 438 eligible links,
534 researcher-topic rows, 24 quality issues, and no coauthor edges. A direct query
also returned no shared eligible work IDs among the seven verified profiles.

Dima Rabadi's 11 linked records include two OpenAlex IDs for one Zenodo preprint:

| Role | DOI | Preserved Silver work ID |
|---|---|---|
| Concept DOI | `10.5281/zenodo.18509541` | `oa:W7128068710` |
| Specific version DOI | `10.5281/zenodo.18509542` | `oa:W7128031808` |

DataCite's [concept metadata](https://api.datacite.org/dois/10.5281/zenodo.18509541)
explicitly has a `HasVersion` relationship to the second DOI. The
[version metadata](https://api.datacite.org/dois/10.5281/zenodo.18509542) supplies the
reciprocal `IsVersionOf` relationship. These were checked on 2026-09-09. Both
describe a preprint; the version metadata identifies Dima's ORCID as
`0000-0001-8067-1995`. Her identity approval is unchanged.

Exact DOI matching missed this because the identifiers differ. Similar titles
alone are not sufficient evidence to merge arbitrary records.

## What changed

Notebook 05 is now `silver_v2_concept_versions`. A small embedded
`REVIEWED_WORK_VERSIONS` reference list keeps it self-contained in Free Edition.
This is **curated transformation reference data**, not a new raw ingestion feed.
It contains selected DataCite fields, source URLs and check date, not full raw API
responses. The reference list participates in the build fingerprint.

Only this confirmed concept/version pair is covered. Adding another pair requires
source-backed review and a code/reference update. Automatic version discovery,
newest-version selection, journal/preprint matching, DOI redirects and title-based
deduplication are not implemented. Overlapping pairs/chains are rejected.

SQL 04 adds three build-snapshot tables and corresponding `*_current` views:

| Table | Grain / purpose |
|---|---|
| `work_version_relationships` | One applied relationship with evidence JSON, SHA-256, source URLs and check date |
| `work_contributions` | One row per preserved work ID, with contribution ID and representative work ID |
| `researcher_contributions` | One verified researcher/contribution, with source work IDs, eligibility and publication type |

For this pair, the contribution ID is `doi:10.5281/zenodo.18509541`, represented by
`oa:W7128031808`. Every other work maps to itself with status `source_record_only`.
That status does **not** certify global uniqueness; unresolved duplicates may exist.

Rules enforced:

- Preserve both works and their source, authorship, topic and award links. Bronze
  is untouched. No destructive merge, update or delete is performed.
- Mark the concept record `concept_doi_alias` and exclude it from expertise. Do not
  change `is_retracted`: an alias is not a retraction.
- Require a verified researcher link to the representative before using its
  expertise. Never transfer authorship, topics or funding from the other record.
- Score only the representative's topics, year and source author count. The
  recency/fractional-authorship formula is unchanged. `supporting_work_count` now
  counts contribution IDs; scoring version is
  `v2_contribution_half_life5_fractional_authorship`.
- Coauthor counts use eligible representatives, so the alias cannot create a
  second shared-work count. Existing edge `work_ids` refer to representatives.
- Retraction on either member blocks positive expertise for the family. Missing
  or ambiguous members, changed work IDs/DOIs/title/type or invalid reciprocal
  evidence fail validation before a new build can be published.
- Keep preprints explicitly labeled. Eligibility is not proof of peer review.
- Leave researcher-award attribution empty; grouping does not establish grant receipt.

The evidence digest covers the stored projection, not an original raw response.
The checked reference is a snapshot, not a live registry monitor.

## Databricks Free Edition upgrade

1. Wait for any ingestion/build/review run to finish. Do not run old and new
   notebook 05 concurrently or alternate versions after upgrading.
2. Import updated [`sql/04_create_silver_tables.sql`](../sql/04_create_silver_tables.sql)
   as a SQL notebook and **Run All**. `IF NOT EXISTS` preserves existing tables,
   builds and identity decisions. This adds three tables/views without changing
   existing schemas or migrating old rows. Total: 25 tables and 24 views.
3. Import updated [`notebooks/05_build_silver.py`](../notebooks/05_build_silver.py)
   as a **fresh Python notebook**, e.g. `05_build_silver_v2`. Keep the old notebook
   for reference only. Fresh import avoids inheriting its old `mode=build` widget.
4. Select serverless notebook compute and **Run All** with the default
   `mode=validate`. Other settings stay `workspace`, `researchmatch_bronze`,
   `researchmatch_silver`, `expected_pilot_profiles=50`, `as_of_year=2026`.
5. Check for `Silver validation passed` and compare the counts below. If it fails,
   stop and share the error; do not edit the pinned evidence to bypass checks.
6. In Widget Panel Settings choose **Do Nothing** before changing values. Change
   only `mode` to `build`, then **Run All**, including the definitions cell.
7. Wait for `Silver build succeeded`. Until then, current views retain the last
   successful build; newly added tables' current views are initially empty.
8. Import [`sql/08_check_publication_versions.sql`](../sql/08_check_publication_versions.sql)
   as a SQL notebook and **Run All**. It is read-only. Share its contribution
   counts and zero-violation checks.
9. Optionally repeat v2 `build` once without changes: it should skip all writes.

No API key, packages, Volume upload, Bronze re-ingestion, AI Search endpoint or
Lakebase resource is required. Do not rerun SQL 07 to approve identities again.
Local Git commits do not automatically update Workspace notebooks.

## Full local replay results — 2026-09-10

The manifested collection was replayed using locally simulated decisions matching
the user's seven confirmed approvals. This did not create approvals or Delta writes.

| Metric | Result |
|---|---:|
| Source-normalized works (unchanged) | 13,557 |
| Reviewed version relationships | 1 |
| Work/contribution mappings | 13,557 |
| Researcher–work source links (unchanged) | 440 |
| Researcher contribution groups | 439 |
| Expertise-eligible contribution groups | 437 |
| Researcher-topic rows (scores recomputed) | 534 |
| Quality issues (one new informational alias issue) | 25 |
| Verified / pending identities | 7 / 116 |
| Coauthor edges / researcher-award links | 0 / 0 |

Dima has 11 source links, 10 groups and 10 eligible groups. The other six
researchers' counts are unchanged. Output estimated size was 104,635,997 bytes,
within the existing 256 MiB output guard; input remained 24,926 latest rows.

All-contribution totals include excluded retraction records/notices and varied
publication types. Do not label 439 as “439 independent peer-reviewed studies.”
Gold must use contribution-level counts with eligibility/type labels, not blindly
count `researcher_works`. Do not mix topic rows from different build snapshots.

At that implementation checkpoint, all 77 offline tests passed. Tests cover preservation, single counting, retractions, missing/ambiguous
pairs, unsupported chains, unapproved identities, conflicting authorship and
coauthor deduplication.

## Confirmed Databricks checkpoint and next step

The user subsequently completed the upgrade: Silver build
`3230bb0f-28c7-4830-90b5-5a21034794cb` matches these counts. SQL 08 confirmed the
concept alias is excluded, the version is the eligible representative, Dima has
ten contribution groups, and all three integrity checks report zero violations.
The 25 quality issues include the expected informational concept-alias flag.

As of 2026-09-18 the [Gold pilot](gold_runbook.md) is implemented and locally tested
for these seven verified researchers. Run SQL 09, notebook 10 validate/build and
SQL 11 next. Do not repeat the Silver upgrade merely to begin Gold.
