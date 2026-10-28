# Identity review batch 04 — 2026-10-28

Status: **awaiting the user's review.** No decisions have been recorded. This reviews
the September 5 collection as loaded in Silver `b239f898…`, not a fresh check of live
websites.

## Why this batch is split-ORCID records, not Business researchers

Batch 04 was meant to start Business coverage. Screening live Silver (read-only) made
that impossible with the current method:

- **College of Business:** 25 profiles, none listing an ORCID. Their 22 candidate
  links are name-only matches, which the profile-ORCID method cannot verify by
  design. Covering Business needs a new identity evidence source (see *Next* below).
- **Remaining ORCID-linked candidates (13):** all CTI. Eleven are the *split ORCID*
  records held out of batch 03, and two belong to Ashraf Khalil, whose alternate
  campus profile is still unresolved.

Split ORCID means OpenAlex has divided one person's papers across several author
records that all carry the same ORCID. Silver already supports several accepted
author records per researcher (it only refuses one author record shared by two
researchers), and Gold stores `verified_openalex_author_ids` as an array, so **no
pipeline change is needed**. Approving every record for a person together keeps all
of their papers.

## The eleven records

All five people are College of Technological Innovation profiles with exactly one
profile-linked ORCID and no researcher quality flags. **Every work on every record
carries the matching ORCID**, and no record has a conflicting authorship ORCID.

| Researcher | OpenAlex author ID | Work records | Eligible | Years |
|---|---|---:|---:|---|
| Ahed Abugabah *(already verified via A5068661949)* | A5136766665 | 1 | 1 | 2026 |
| | A5135111126 | 1 | 1 | 2026 |
| Ahmed Seffah | A5073929162 | 81 | 81 | 1991–2026 |
| | A5144282536 | 1 | 1 | 2026 |
| | A5137791213 | 1 | 1 | 2026 |
| Belal AlSinglawi | A5018299454 | 61 | 61 | 2016–2026 |
| | A5135604592 | 2 | 2 | 2026 |
| Edmund Evangelista | A5003856994 | 13 | 13 | 2021–2026 |
| | A5136695590 | 1 | 1 | 2026 |
| Farkhund Iqbal | A5068447696 | 200 | 198 | 2005–2026 |
| | A5135285921 | 3 | 3 | 2026 |

ORCIDs: Abugabah `0000-0002-3181-5822`, Seffah `0000-0001-7166-8567`, AlSinglawi
`0000-0003-0316-3641`, Evangelista `0000-0002-8050-1360`, Iqbal `0000-0001-9081-3598`.
Counts are unique collected OpenAlex work records, not lifetime totals or distinct
studies. Approval verifies the identity link; it does not validate every paper.

Across all records, works with a ZU affiliation: Iqbal 169 of 203, Abugabah 57 of 81
(2 retracted records, already excluded by Silver), Evangelista 14 of 14, Seffah 9 of
83, AlSinglawi 6 of 63. Seffah's and AlSinglawi's low shares reflect long careers
before joining ZU; the profile-linked ORCID is the identity evidence.

### Evidence supporting the identity links

- **Ahmed Seffah:** the ZU biography names human-centred software engineering and
  HCI. Evidence: *GaLaPaGoS: A Design Pattern for Sustainability of ICT Interactive
  Software and Services* (2024; DOI `10.1007/978-3-031-64576-1_4`). The small records
  hold an XAI-in-agriculture case study and olive-leaf disease detection (2026).
- **Belal AlSinglawi:** the biography names an IT and computing career. Evidence:
  *A Smart Contract-Based Blockchain Solution for Key Revocation in IoT/WoT
  Environments* (2025; DOI `10.1007/978-3-032-07671-7_4`). The small record holds two
  quantum homomorphic encryption papers (2026), both with ZU affiliation.
- **Edmund Evangelista:** CTI Abu Dhabi. Evidence: *From ethical principles to
  executable governance: A policy-as-code framework for trustworthy AI in higher
  education* (2026; DOI `10.1016/j.caeai.2026.100594`).
- **Farkhund Iqbal:** the biography names AI for healthcare, cybersecurity and
  cybercrime investigation. Evidence: *From Plots to Plain Language: Leveraging LLMs
  for Human-Centric Explanations of XAI Outputs in Digital Forensics* (2026; DOI
  `10.1007/978-3-032-35579-9_32`). The small record holds AI-agent safety benchmarks
  (an arXiv preprint plus a record without DOI; check same-title handling after the
  rebuild) and an LLM comparison study.
- **Ahed Abugabah:** already verified. The two extra records add an MRI-based
  Alzheimer's diagnosis system and a post-quantum secure medical cyber-physical
  systems paper (2026), matching his existing medical-AI evidence.

## What SQL 16 checks

Same guards as SQL 15, with two changes for split records:

1. The candidate's only quality flag must be `shared_orcid_multiple_author_ids`
   (batch 03 required no flags).
2. `all_orcid_records_reviewed` replaces `no_split_orcid`: every author record with
   the ORCID must be in this batch or already verified **for the same researcher**,
   and all of one ORCID's records must belong to one researcher.

A read-only preview against live Silver on 2026-10-28 returned eleven rows with every
safety column true and `all_checks_pass = true`. Offline tests cover the new checks
(`tests/test_identity_batch.py`).

**Local simulation (no Databricks writes).** Replaying the September 5 collection
through notebooks 05 and 10 with the 13 live accepts plus these eleven gave: 0 stale
decisions (local evidence hashes match live), 24 verified and 99 pending candidates,
and a Gold build of **17 profiles, 1,387 publications, 1,504 topics and 3,786
topic-evidence links**, all within the app's limits (50 / 5,000 / 7,500 / 25,000).
Expected eligible publications per new person: Seffah 83, AlSinglawi 63,
Evangelista 14, Iqbal 201; Abugabah rises from 77 to 79.

## Databricks steps

Same procedure as batches 02 and 03, using
[`sql/16_review_identity_batch_04.sql`](../sql/16_review_identity_batch_04.sql):

1. Import SQL 16 as a **SQL notebook**. Leave `SELECT false AS approve_reviewed_batch`
   and **Run All**. This previews eleven records and records **zero decisions**.
2. Check the eleven-row table: all five safety columns and `all_checks_pass` must be
   `true`. If any check fails, stop and share the output; do not edit hashes.
3. If you agree with **all eleven**, change only the permission cell to `true` and
   **Run All**. Confirm eleven `accept` records, then restore `false`.
4. Notebook 05: `validate`, then `build`. Expect **17 verified researchers**, 24
   verified and 99 pending candidates. Run SQL 06 and SQL 08.
5. Notebook 10 with `expected_verified_profiles=17`: `validate`, then `build`. Run SQL 11.
6. Notebook 12 `sync`, wait for 17 indexed rows, then `status` and `test`.
7. App connection check: expect 17 researchers. Then run `health_check.sh`.

Do not run the approval concurrently with another reviewer, Silver build or refresh.

## Held out

- **Ashraf Khalil:** alternate campus profile anchors are still unresolved.
- **Name-only matches** (Business and the rest of CTI): no ORCID evidence; not
  reviewable with this method.

## Next: an identity method for Business

None of the 25 Business profiles lists an ORCID. Options, each needing the user's
decision before any collection:

- Look up each name in the public ORCID registry with a Zayed University employment
  record, then require that ORCID on the OpenAlex author (strong, same rule as now).
- Accept OpenAlex records whose last known institution is ZU and whose name matches
  exactly, with manual paper review (weaker; needs explicit review criteria).
