# Identity review batch 07 — 2026-10-29 (affiliation rule)

Status: **approved by the user on 2026-10-29** (plan "batches 06–07 with the recommended
options"), including the new rule below. Accept records are written with SQL 19's
guarded INSERT; see *Databricks steps*.

## The affiliation rule (new, weaker than batches 02–06)

These five profiles list no ORCID, and their ORCID records list **no employment**, so
the registry rule cannot confirm them. The rule accepted here is:

1. the OpenAlex author is a **name match** for the ZU profile and carries an ORCID
   (pinned in SQL 19 as `expected_orcid`);
2. its **last known institution is Zayed University**;
3. it has **at least 10 ZU-affiliated authorships** in the collected data;
4. plus all batch 05 safety checks: no other author shares the ORCID, no conflicting
   authorship ORCIDs, no other profile claims the author, no directory duplicate, no
   profile ORCID, no quality flags.

Why it is weaker: the ZU link rests on publishers' affiliation metadata and OpenAlex's
name and ORCID attribution, not on the person's own ORCID employment record. Ten or
more ZU-affiliated papers under one ORCID-bearing author makes a wrong person unlikely,
but it is not the same evidence.

| Researcher | College | ORCID (on OpenAlex author) | OpenAlex author | Works (ZU-affiliated) | Sample paper |
|---|---|---|---|---:|---|
| Abiot Tessema | CB | 0000-0001-8423-8232 | A5005423855 | 32 (27) | *Driving sustainable success: the power of executive pay to navigate ESG challenges…* (2026; `10.1108/cg-05-2024-0294`) |
| Anwer Al-Dulaimi | CTI | 0000-0001-9936-0038 | A5020575793 | 145 (27) | *Foundation Model Empowered Real-Time Video Conference With Semantic Communications* (2026; `10.1109/tip.2026.3659719`) |
| Catherine Nickerson | CB | 0000-0002-3379-1465 | A5040939492 | 144 (59) | *English-Language Advertising in Europe* (2025; `10.1002/9781119518297.eowe00034`) |
| Maytha Al Ali | CB | 0000-0001-5606-1695 | A5089919765 | 30 (25) | *Integrating machine learning and explainable AI for employee attrition prediction in HR analytics* (2026; `10.1038/s41598-026-36424-2`) |
| Mohammed Sualihu | CB | 0000-0002-0766-4569 | A5007637668 | 24 (14) | *The monitoring ability of subordinate executives: Evidence from corporate labor violations* (2026; `10.2139/ssrn.6810041`) |

Each biography fits the papers (accounting, communications engineering, business
communication, HR analytics, accounting). Al-Dulaimi and Al Ali also have small
name-matched OpenAlex records **without** an ORCID; those stay pending and are not
approved here.

## Checks

A read-only preview against live Silver `ce476f15…` on 2026-10-29 returned five rows
with every safety column true and `all_checks_pass = true`. Tests:
`IdentityBatch07Tests` (fewer than 10 ZU authorships, non-ZU affiliations, or a non-ZU
last institution each block the batch).

## Databricks steps

Record with [`sql/19_review_identity_batch_07.sql`](../sql/19_review_identity_batch_07.sql)
together with batch 06, then the single rebuild described in
[batch 06](identity_review_batch_06.md).
