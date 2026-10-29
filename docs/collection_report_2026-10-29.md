# Targeted collection report — `targeted-20261029`

Status: **loaded and rebuilt (2026-10-29).** The batch was uploaded to
`/Volumes/workspace/researchmatch_bronze/landing/targeted-20261029/` (29 files,
checksums verified) and loaded by notebook 03 (10 authors, 566 works, 115 awards and
80 funders inserted). Silver `ce476f15…` validated with **34 verified decisions, 0
stale**, 90 pending; SQL 08 zero. Because the app requires Gold to match the current
Silver, Gold was rebuilt with the same 27 profiles (`378f4211…`, 1,530 publications;
SQL 11 zero), the index synced, and notebook 12 `status` and `test` succeeded. No
identities are approved by collection. The resulting candidates were then approved in batches 06 (4) and 07
(5); the pilot now has **36 profiles** (Gold `1f6f4254…`).

## Why

After batch 05 the pilot has 27 of 50 ZU profiles verified, and no further profile
could be verified from the 2026-09-05 data (see `identity_review_batch_05.md`). Many
gaps are missing **evidence**, not identity: candidate authors whose career works were
never collected, because the first collection only expanded profile-ORCID authors.

## What was collected

Ten people, from `config/targeted_collection.json`:

| Tier | Person | OpenAlex author | Authorships before → after |
|---|---|---|---|
| 1 registry | Mahmoud Agha | A5074445320 | 0 → 16 |
| 1 registry | Moo Sung Kim | A5063716848 | 1 → 3 |
| 1 registry | Feras Al Obeidat | A5037131693 | 123 → 152 |
| 1 registry | Butros Dahu | A5027453912 | 0 → 22 |
| 1 registry | Ian Michael | A5114970461 (new record) | 0 → 1 |
| 2 affiliation | Abiot Tessema | A5005423855 | 27 → 32 |
| 2 affiliation | Anwer Al-Dulaimi | A5020575793 | 21 → 145 |
| 2 affiliation | Catherine Nickerson | A5040939492 | 62 → 144 |
| 2 affiliation | Maytha Al Ali | A5089919765 | 16 → 30 |
| 2 affiliation | Mohammed Sualihu | A5007637668 | 13 → 24 |

The run collected 10 authors, 566 works, 115 linked awards and 80 funders, plus 16 raw
ORCID registry responses (searches and employments) for audit. All 34 verified authors
(and their ORCIDs, `config/verified_author_ids.json`) were protected and not re-fetched.
Feras Al Obeidat's second ORCID (`0009-0006-1706-0992`) resolves to no OpenAlex author.

## Local proof (replay of notebooks 05 and 10)

| | Before (2 batches) | After (+ targeted) |
|---|---|---|
| Accepted decisions applied | 34 | 34 |
| Stale or missing decisions | 0 | **0** |
| Verified researchers | 27 | 27 |
| Pending candidates | 89 | 90 (Ian Michael's new record) |
| Silver works | 13,557 | 13,836 |
| Gold profiles / publications / topic links | 27 / 1,529 / 4,196 | 27 / 1,530 / 4,203 |

The only change to an existing profile is Mariem Khalifa, 11 → 12 eligible
publications: a co-authored work now collected through a target author.

Some candidates' match method changes from `exact_normalized_name` to `name_tokens`
because OpenAlex now shows a fuller display name (e.g. "Feras N. Al-Obeidat"). Both
are name matches; identity still rests on the ORCID rules of the coming batches.

## Next steps (each needs the user's go-ahead)

1. Package and upload the batch; load it into Bronze with notebook 03; rebuild Silver
   (expect 0 stale decisions, 27 verified, 90 pending).
2. **Batch 06 (Tier 1):** registry rule, with documented clarifications for Al Obeidat
   (empty duplicate ORCID) and Dahu (family-name search; `unusable_profile_orcid` flag).
3. **Batch 07 (Tier 2):** only if the user accepts the affiliation rule (ORCID on the
   OpenAlex author, last known institution ZU, ZU-affiliated authorships; the ORCID
   record lists no employment).
