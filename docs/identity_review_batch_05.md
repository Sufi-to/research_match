# Identity review batch 05 — 2026-10-28 (College of Business, ORCID registry)

Status: **awaiting the user's review.** No decisions have been recorded. The user
chose the ORCID registry method on 2026-10-28; approving these ten identities is a
separate decision.

## Method: ORCID registry lookup

None of the 25 College of Business profiles lists an ORCID, so the profile-ORCID
method of batches 02–04 cannot verify them. The user chose this replacement, which
keeps the same rule (an ORCID must link the person to the OpenAlex author) but takes
the ORCID from the public ORCID registry instead of the ZU profile:

1. Search the public registry (`pub.orcid.org/v3.0/expanded-search`) for
   `given-and-family-names:"<profile name>" AND affiliation-org-name:"Zayed University"`.
   Accept only a **single** result.
2. Read that record's employments (`/v3.0/<orcid>/employments`). It must list
   **current** Zayed University employment.
3. The ORCID must sit on exactly one collected OpenAlex author record, which must
   already be a Silver candidate for that profile, with no other author record
   sharing the ORCID and no conflicting authorship ORCIDs.

The registry ORCID is pinned in SQL 17 (`expected_orcid`), together with the
candidate's evidence hash. A name match alone can never pass: the OpenAlex author
must carry exactly the pinned ORCID.

**Control case:** Graham Manville (Business, verified in batch 02 through his profile
ORCID) returns exactly that ORCID from the registry search.

**Limits:** ORCID employment entries are entered by the researcher. That is the
standard source for ORCID, but it is self-asserted, not an HR record. The lookup was a
read-only check on 2026-10-28; the responses are summarised below, not stored in
Bronze. If this method is used again or refreshed, it should become a collector
source with stored raw responses.

## Screening results (all 25 Business profiles)

| Outcome | Count | People |
|---|---:|---|
| Already verified (control) | 1 | Graham Manville |
| One registry ORCID, ZU employment, on a collected OpenAlex author | 11 | below |
| …but that author has **0 collected works** (excluded: no evidence) | 1 | Mahmoud Agha |
| One registry ORCID, but not on any collected OpenAlex author | 2 | Ian Michael, Moo Sung Kim |
| No registry match | 11 | Abiot Tessema, Alejandro Sposato, Amr Abdelaal, Balqees Al Messabi, Catherine Nickerson, Ibrahim Bostan, Konstantinos Syriopoulos, Maqsood Memon, Maytha Al Ali, Mohammed Sualihu, Muhammad Hassan |

## The ten candidates

| Researcher | ORCID (registry) | ZU employment in ORCID | OpenAlex author | Works (ZU-affiliated) |
|---|---|---|---|---:|
| Ahmed Abdel-Maksoud | 0000-0002-6932-8501 | Professor, 2020– | A5068747618 | 8 (8) |
| Alexandra Henderson | 0000-0002-3361-168X | current (no title) | A5015822964 | 19 (19) |
| Anestis Fotiadis | 0000-0001-8806-7987 | Professor, 2019– (Assoc. Prof. 2016–2019) | A5057670674 | 70 (67) |
| Brendan Galbraith | 0000-0001-9416-5540 | Associate Professor, 2017– | A5030826659 | 5 (5) |
| Fathima Saleem | 0000-0001-5456-1442 | Associate Professor, 2020– | A5005961739 | 2 (2) |
| Ismail Badraoui | 0000-0002-6332-8383 | Assistant Professor, 2024– | A5091232321 | 7 (7) |
| Jamel Khenfer | 0000-0002-0489-1272 | Associate Professor, 2025– (Asst. Prof. 2017–2022) | A5056804412 | 12 (12) |
| Karim Hegazy | 0000-0003-3576-3238 | Assistant Professor, 2019– | A5026444896 | 7 (7) |
| Khadija Abdulrahman | 0000-0001-7875-8709 | Assistant Professor, 2017– | A5064532212 | 1 (1) |
| Mariem Khalifa | 0000-0003-1751-0579 | Associate Professor, 2025– | A5057231192 | 11 (11) |

Counts are collected OpenAlex work records. Several profiles are thin: Abdulrahman (1)
and Saleem (2) will rank on very little evidence, and the app shows that evidence
openly. Approval verifies the identity link; it does not validate every paper.

### Evidence that the papers fit the profile

| Researcher | ZU biography | Sample paper |
|---|---|---|
| Ahmed Abdel-Maksoud | Professor of Accounting | *fMRI study of brain activation associated with earnings management techniques…* (2025; `10.1186/s43093-025-00520-y`) |
| Alexandra Henderson | Associate Professor of Management | *A test of the work stressor-vulnerability model of alcohol consumption* (2026; `10.1080/15555240.2026.2623623`) |
| Anestis Fotiadis | Professor, Management | *Reputation Management and Hotel Booking Intention…* (2026; `10.1108/s1745-354220260000021004`) |
| Brendan Galbraith | Professor in Entrepreneurship | *Guest Editorial: Incubators and Accelerators…* (2022; `10.1109/tem.2022.3167953`) |
| Fathima Saleem | Marketing & Entrepreneurship | *Influencer advertising: facilitating poor-fitting influencer posts* (2024; `10.1108/md-02-2023-0261`) |
| Ismail Badraoui | Operations Management | *Expert's Opinion on the UAE's Progress Toward SDG 13, Climate Action* (2026; `10.4018/979-8-3373-3346-5.ch014`) |
| Jamel Khenfer | Associate Professor of Marketing | *Justifying advertisements in paid streaming subscriptions…* (2026; `10.1080/02650487.2026.2694294`) |
| Karim Hegazy | Associate Professor in Accounting | *Global Professional Service Networks and their Members…* (2025; `10.1142/s1094406025500015`) |
| Khadija Abdulrahman | (no biography collected) | *The inflation hedging capacity of Islamic and conventional equities* (2020; `10.1108/jes-04-2019-0183`) |
| Mariem Khalifa | Associate Professor of Accounting | *Diversified firms and corporate labor policy…* (2025; `10.1016/j.iref.2025.103893`) |

## What SQL 17 checks

Same guards as SQL 15, with two changes:

1. `match_method` must be a name match (`exact_normalized_name` or `name_tokens`), and
   the profile must list **no** ORCID. A profile ORCID would belong to the
   profile-ORCID method instead.
2. The OpenAlex author's ORCID must equal the pinned registry ORCID (the same
   `a.orcid = expected_orcid` predicate, now carrying all of the identity evidence).

A read-only preview against live Silver returned ten rows with every safety column
true and `all_checks_pass = true`. A local replay with batches 02–05 accepted gave 0
stale decisions and a Gold build of **27 profiles, 1,529 publications, 1,712 topics
and 4,196 topic-evidence links**.

## Databricks steps

Same as batch 04, using [`sql/17_review_identity_batch_05.sql`](../sql/17_review_identity_batch_05.sql):
record the ten accepts, then rebuild Silver (expect 27 verified researchers, 34
verified and 89 pending candidates), run SQL 08, build Gold with
`expected_verified_profiles=27`, run SQL 11, sync the index once, then run `status`
and `test`, the app check and `health_check.sh`.

## Held out

- **Mahmoud Agha:** registry match, but no collected works.
- **Ian Michael, Moo Sung Kim:** their registry ORCIDs are not on any collected
  OpenAlex author; a source refresh that collects authors by ORCID would be needed.
- **Eleven with no registry match:** no ORCID evidence is available.
