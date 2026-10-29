# Identity review batch 06 — 2026-10-29 (ORCID registry, targeted batch)

Status: **approved by the user on 2026-10-29** (plan "batches 06–07 with the recommended
options"). Accept records are written with SQL 18's guarded INSERT; see *Databricks
steps* for what has been recorded.

**Recorded and rebuilt 2026-10-29:** batches 06 and 07 together, 9 accepts (the user
ran the guarded INSERTs; Claude Code's auto-mode check blocked them for Claude). Silver
`b372be7f…`: 36 verified researchers, 43 verified decisions, 0 stale; SQL 08 zero. Gold
`1f6f4254…`: 36 profiles, 2,097 publications, 5,850 topic-evidence links; SQL 11 zero.
One index sync; `health_check.sh` all PASS (index synced to source version 8);
notebook 12 `status` and `test` succeeded. The user's in-app check is still to come.

## Method

Same as batch 05 (public ORCID registry: single record with current Zayed University
employment; that ORCID must be on the OpenAlex author; all batch 05 safety checks).
The evidence comes from the targeted collection `targeted-20261029`
([report](collection_report_2026-10-29.md)), whose raw ORCID responses are stored in
the landing batch as `orcid_registry_raw`.

| Researcher | College | ORCID | OpenAlex author | Works (ZU-affiliated) | Sample paper |
|---|---|---|---|---:|---|
| Mahmoud Agha | CB | 0000-0002-4237-0056 | A5074445320 | 16 (1) | *Green financing policy and bank performance and risk…* (2026; `10.1108/ijmf-01-2026-0069`) |
| Moo Sung Kim | CB | 0000-0002-5073-9984 | A5063716848 | 3 (2) | *The Effect of Family Ownership on… Corporate Transparency* (2026; `10.3390/jrfm19020127`) |
| Feras Al Obeidat | CTI | 0000-0001-6941-6555 | A5037131693 | 152 (127) | *Optimizing PROAFTN classifier with ant colony algorithm…* (2026; `10.5281/zenodo.18174732`) |
| Butros Dahu | CTI | 0000-0001-5880-2450 | A5027453912 | 22 (2) | *A Collaborative Multiagent Framework for Enhancing Learning in OOP* (2026; `10.1109/src70627.2026.11550700`) |

Agha joined ZU in August 2025 (his biography says so), which explains one ZU-affiliated
paper out of 16. Kim's profile is thin (3 papers) but ZU-backed.

## Approved clarifications

- **`unusable_profile_orcid` exception (Dahu, Al Obeidat).** Their ZU pages link to
  orcid.org without a valid ORCID, which Silver flags. The registry evidence is
  independent of that link, so SQL 18 accepts this one flag for these two candidates
  only (`rm_batch06_flag_exceptions`). Any other flag, or this flag on anyone else,
  blocks the batch.
- **Al Obeidat's second ORCID** (`0009-0006-1706-0992`) also lists ZU employment but has
  no works and no OpenAlex author: an empty duplicate account, not a conflicting identity.
- **Dahu's registry match** came from a family-name search ("Butros M. Dahu"); the
  full-name search found nothing because of the middle initial.

## Excluded

- **Ian Michael.** His registry ORCID is on a separate one-paper OpenAlex record with no
  ZU affiliation; his 92-work record carries no ORCID. Too thin to verify.

## Checks

A read-only preview against live Silver `ce476f15…` on 2026-10-29 returned four rows
with every safety column true and `all_checks_pass = true`. Tests:
`IdentityBatch06Tests` in `tests/test_identity_batch.py`.

## Databricks steps

Record with [`sql/18_review_identity_batch_06.sql`](../sql/18_review_identity_batch_06.sql)
(Run All once as a preview, then set the permission cell to `true`), together with
batch 07, then rebuild once for both: Silver (expect 43 verified decisions, 0 stale,
36 researchers), SQL 08, Gold with `expected_verified_profiles=36`, SQL 11, one index
sync, notebook 12 `status` and `test`, `health_check.sh`.
