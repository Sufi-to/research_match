# ResearchMatch Silver v1

Silver v1 structures the existing Bronze collection and introduces governed identity
review. It is a bounded, on-demand Free Edition pilot, not a continuously running
pipeline. There are no API calls, new downloads, packages to install, AI Search
resources, or Lakebase dependencies at this stage.

## 1. Run order in Databricks

1. Finish the full Bronze load. The latest ingestion run for each required source
   must be `succeeded`, not `started`, `failed`, or a bounded `partial` test.
2. Import `sql/04_create_silver_tables.sql` into Workspace as a SQL notebook.
   Select serverless compute and run all cells. It creates a new
   `workspace.researchmatch_silver` schema, 22 Delta tables, and 21 views.
   It does not replace existing tables or touch Bronze.
3. Import `notebooks/05_build_silver.py` as a Python notebook and select serverless.
4. Run all cells with the defaults below. The first run creates the widgets and
   performs validation only. Before changing widgets, open Widget Panel Settings
   and set execution behavior to **Do Nothing**, then make changes and run manually.

| Widget | Initial value |
|---|---|
| `catalog` | `workspace` |
| `bronze_schema` | `researchmatch_bronze` |
| `silver_schema` | `researchmatch_silver` |
| `mode` | `validate` |
| `expected_pilot_profiles` | `50` |
| `as_of_year` | `2026` |

5. Check for `Silver validation passed` and inspect the printed counts. Validation
   reads Bronze and identity decisions but writes no Delta data or run logs.
   DDL 04 must already exist even for validation.
6. Change only `mode` to `build` and run all cells. Wait for a success message.
7. Import `sql/06_review_silver.sql` and run its read-only exploration queries.
   Use the `*_current` views when exploring results, not the underlying history tables.
8. Repeat the build without changing inputs. It should report that an identical
   successful build already exists and perform no writes.

If your catalog/schema names differ, change SQL 04 and SQL 06 as well as notebook
widgets. Do not run two builders or a Bronze refresh concurrently. Do not schedule
the notebook yet. A fresh Silver session sees no `*_current` rows until its first
build succeeds.

## 2. What is being created

| Tables | Purpose |
|---|---|
| `researchers`, `researcher_aliases`, `researcher_affiliations`, `researcher_identifiers` | Downloaded pilot profile records, names, observed directory roles, and identifiers |
| `authors`, `entity_resolution_review` | Latest OpenAlex author snapshots and unapproved/approved/rejected identity candidates |
| `works`, `work_sources`, `work_authorships` | Provisionally consolidated publications, source crosswalk, and source-reported authorships |
| `researcher_works` | Authorships attached to a pilot researcher only through an accepted identity decision |
| `topics`, `work_topics`, `researcher_topic_scores`, `coauthor_edges` | Topic hierarchy, publication evidence, provisional scoring, and collaboration between verified pilot researchers |
| `funders`, `awards`, `work_awards`, `award_evidence`, `researcher_awards` | External funding evidence; no automatic attribution of funding to researchers |
| `quality_issues` | Reviewable layout, identity, DOI, and evidence issues |
| `identity_decisions` | Append-only human review input; never overwritten by the builder |
| `build_runs` | Build status, frozen source versions, input fingerprint, output counts, and failures during writing |

There are 20 derived tables and two control/input tables. Each derived table has
`build_id` and `row_key`. The `current_build` view selects the last fully successful
build; the 20 `*_current` views select only rows belonging to that build.

The 50 initial `researchers` rows are **profile-anchored pilot candidates**, not a
claim that we have verified 50 distinct people. We consolidate roles/names at the
same URL. Different campus URLs sharing a name are flagged and not auto-merged.
The wider directory stays available in Bronze, without turning staff or every
directory entry into a searchable researcher.

## 3. Identity review: required before researcher–work links appear

**Current checkpoint (user-confirmed, recorded 2026-09-09):** the build and first
two approvals succeeded: 2 verified, 121 pending, 151 researcher–work links and
232 topic-score rows. Continue with [identity batch 02](identity_review_batch_02.md)
and its default-disabled SQL 07. The initial zero-link description below explains
first-build behaviour, not the current result. SQL 06 remains read-only.

The first build intentionally has:

- pending identity candidates;
- zero rows in `researcher_works`, `researcher_topic_scores`, and `coauthor_edges`;
- zero rows in `researcher_awards` regardless of publication funding links.

Candidate generation uses profile-linked checksum-valid ORCIDs, normalized names,
and conservative name-token containment. A known ZU affiliation is displayed as
supporting evidence, not proof. Some broad collector search results do not meet
these candidate rules, and some profiles have no candidate. No publication-count
threshold or name match automatically approves a person.

Review one candidate at a time using SQL 06:

1. Compare the ZU profile and source-linked ORCID against the candidate author.
2. Inspect that author's collected publications and their source affiliations.
3. Check for conflicting ORCIDs, split OpenAlex IDs, or alternate campus profiles.
4. Do not approve an identity based on its name or high publication count alone.

After you have checked a specific candidate, run this in a **separate SQL cell**.
Replace both placeholders. Choose `accept` or `reject` intentionally.

```sql
INSERT INTO workspace.researchmatch_silver.identity_decisions
  (decision_id, candidate_id, evidence_hash, decision, rationale, reviewed_by, reviewed_at)
SELECT
  uuid(), candidate_id, evidence_hash,
  'accept',
  'REPLACE WITH THE EVIDENCE YOU CHECKED',
  current_user(), current_timestamp()
FROM workspace.researchmatch_silver.entity_resolution_review_current
WHERE candidate_id = 'PASTE_ONE_CANDIDATE_ID';
```

Expect exactly one inserted row. Never remove the `WHERE` clause or bulk-approve
all ORCID/name matches. To reject, use `'reject'`. Do not paste ORCID/API secrets;
the pipeline needs no API key. Review notes are project data and should not contain
unnecessary sensitive information.

Then run notebook 05 in `validate`, inspect the new verified-link counts, and run
`build`. The identity-decision table version changes the build fingerprint.

Rules enforced by the builder:

- An author ID cannot be accepted for two pilot researcher IDs.
- Multiple OpenAlex author IDs can be accepted for one researcher after review.
- Conflicting ORCIDs or multiple ORCIDs on the ZU profile block acceptance. Reject
  the candidate or resolve the upstream evidence and refresh it; do not edit Bronze.
- Changed identity evidence makes an earlier decision stale. It is not applied;
  a new decision against current evidence is required.
- Later accept/reject decisions supersede earlier decisions for that candidate.
  A rejection removes its derived links in the next current build.
- Work-level ORCID contradictions are flagged and excluded from researcher links
  even if the author ID was accepted.

There is no manual cross-campus profile merge or forced conflicting-ORCID override
in v1. Those cases remain unresolved rather than silently combined. An administrative
role does not by itself exclude a researcher. `directory_observed` affiliation
does not establish current employment or appointment dates.

## 4. Publication normalization and enrichment

- Select the latest fetched Bronze version per OpenAlex entity, profile URL, or
  OAI identifier. Ties use source update time where available and the version key.
- Preserve original author names and their positions in `work_authorships`.
  OAI `Surname, Given name` values are not automatically turned into person links.
- Normalize DOI URLs, `info:doi/`, plain DOIs, and the observed malformed
  `https://10.xxxx/...` format. Keep the source title and identifier in `work_sources`.
- Reconstruct OpenAlex abstract indexes only if word positions are valid and
  complete. Otherwise flag the index and leave it unavailable for source fallback.
- Enrich an OpenAlex work from OAI only when the normalized DOI matches exactly
  one OpenAlex work. Retain OAI subject terms and field-level abstract-source labels.
- Repository descriptions can fill missing abstract text, but are labeled
  `oai_description`: they are **not guaranteed to be research abstracts**. Review
  these before constructing Gold search text.
- Do not merge on similar titles. Multiple OpenAlex work IDs sharing a DOI are
  retained and flagged; their expertise eligibility is disabled pending resolution.
- Keep unmatched OAI publications with OAI-based IDs. Multiple unresolved records
  sharing a DOI are flagged, not forcibly merged. Work IDs can change across builds
  when a previously OAI-only record gains a unique OpenAlex match; `work_sources`
  is the source-ID crosswalk. Downstream consumers must use a consistent build.
- Keep retracted works for traceability, but exclude them from positive expertise
  evidence. OAI-only records have unknown retraction status and unverified
  authorship; they are not used for expertise in v1.
- Missing titles, abstracts, DOIs, and author IDs remain visible as quality flags.

`research_activity_text` is the source profile's research section, which can include
publications, activities, and grant statements. It is not an AI-generated list of
interests or a verified award record. Biographies and research sections are extracted
from both the Business heading layout and CTI tabbed layout.

## 5. Topics and funding

OpenAlex topic scores remain source assertions. The provisional researcher-topic
score is the sum, over eligible verified publications, of:

```text
source_topic_score × 0.5^(publication_age / 5) ÷ source_author_count
```

Unknown publication years use a recency weight of 0.5; future dates within the
accepted year range use age zero. The scoring year and scoring version are stored.
This downweights large collaborations but is not a calibrated relevance score.
OAI subjects and profile research text are retained separately for later Gold use.
Coauthor edges connect only verified pilot researchers on eligible shared works;
they are not the full global coauthor network.

Publication–award links retain unresolved IDs. Award metadata retains institution,
investigator, and provenance assertions without promoting them into personal grant
receipts. The independently retrieved award with no collected publication link is
flagged. Amounts retain their currencies; no currency conversion or cross-currency
total is produced.

`researcher_awards` is deliberately empty. Approved institutional award ingestion
and researcher-award review remain future work; no institutional award export is
currently available. Profile grant statements remain source text, not verified
structured awards.

## 6. Local validation results (2026-09-07)

These are expected results from the complete local landed copies, **not a claim of
a completed Databricks Silver build**. They assume an empty identity-decision table.

| Output | Expected rows |
|---|---:|
| Pilot profile records | 50 |
| Researcher aliases / observed affiliations | 51 / 55 |
| Checksum-valid profile-linked ORCID identifiers | 22 |
| OpenAlex authors | 129 |
| Identity candidates | 123 |
| Works | 13,557 |
| Work source crosswalk rows | 20,727 |
| Source authorships | 89,391 |
| Topics / work-topic links | 3,215 / 31,980 |
| Funders / awards | 1,564 / 2,283 |
| Publication–award links / award evidence rows | 3,566 / 5,849 |
| Quality issues | 24 |

Additional checks:

- 46 profiles have biographies; 46 have research activity text.
- 26 candidates use profile ORCID evidence, 79 exact normalized names, 18 name tokens.
- 7,170 OAI records link by unique DOI; 836 remain OAI-only.
- OAI descriptions fill 1,898 OpenAlex abstract gaps; 12,016 total works contain
  an abstract or description after enrichment.
- All 18 retracted works are retained and excluded from expertise.
- All 119 unresolved award IDs are retained.
- The graph has zero researcher-work, topic-score, coauthor, or researcher-award
  links before human identity decisions.

The earlier collector's DOI extraction did not recognize `info:doi/` on its own.
Silver therefore recognizes more OAI DOI evidence than the original collection
report (which counted 4,994 OAI records with extracted DOIs). Existing downloaded
OpenAlex institution works already resolve many of these additional references;
this build makes no new API requests. Historical collection files are unchanged.

## 7. Resource limits, failure recovery, and validation scope

This pilot uses Spark for version selection and partitioned reads, then Python for
the bounded structured graph. It is **not a fully distributed transformation**.
It reads about 251 MiB of string payload and retains about 95 MiB of serialized
structured output in the tested batch; Python objects and Spark partitions add
memory overhead beyond those sizes.

Guards stop input over 60,000 latest source rows / 512 MiB of string payload or output
over 500,000 rows / 256 MiB serialized. Graph writes use batches up to 10,000 rows /
4 MiB. This avoids source API waits, streaming jobs, RDDs, caches, and per-record
writes. A larger collection needs distributed transforms rather than larger guards.
Runtime support follows [Databricks serverless limitations](https://docs.databricks.com/aws/en/compute/serverless/limitations)
and Spark's [Connect-compatible local iterator](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrame.toLocalIterator.html).

The builder freezes Delta versions for inputs. Every table in a build is appended
under a new UUID, then its persisted row count and unique key count are checked.
Only the final successful `build_runs` update makes the new build visible through
current views. This is a publication gate, not a multi-table Delta transaction.

- Failed writes leave unpublished rows with the failed build ID; the previous
  current build remains available. Do not truncate or overwrite tables.
- Pre-write validation errors appear in notebook output and create no run log.
- An interrupted writer can leave a `started` run; it does not become current.
- Rerun after fixing a failure. An identical successful input fingerprint skips
  writing; changes in research inputs, decisions, scoring year, or transformation
  version create a new build.
- Snapshot history grows after each changed build. Cleanup/retention is not automated
  in v1; inspect storage before frequent refreshes. Do not delete source versions
  needed for audit/replay without a separate retention decision.

Offline tests exercise contracts, identity gates, DOI ambiguity, OAI enrichment,
abstract reconstruction, retraction filtering, coauthor/scoring behavior, stable
keys, resource guards, and failed-publication behavior. Local full-data replay is
available with:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/validate_silver_local.py
```

No live Spark/Delta Silver execution has been performed from this environment.
The first Databricks validate/build/repeat sequence is still required. Gold and
AI Search should wait until a reviewed subset has credible researcher–work links.
