# Identity review batch 02 — 2026-09-09

Status: **five recommended matches; no approvals recorded by this work**.
The reviewer must deliberately approve the batch in Databricks. This is a review
of the September 5 collection, not a fresh check of live websites.

## Current checkpoint

The user confirmed successful Silver build
`5fb6b85e-4bde-42ae-b237-cb8a396f6a81`: 2 verified candidates, 121 pending,
151 researcher–work links and 232 researcher-topic rows.

- Abdallah Tubaishat: 72 linked records, all 72 eligible for expertise.
- Ahed Abugabah: 79 linked records, 77 eligible. The excluded records are a
  retracted article (`oa:W3087268261`) and its retraction notice
  (`oa:W4404934963`), not two independent research studies.
- Zero researcher-award rows is intentional; publication funding does not
  establish that a particular coauthor received a grant.

## Proposed next five

Local faculty HTML and OpenAlex work files were checked against manifest SHA-256
checksums. Each selected ZU profile links the same checksum-valid ORCID as its
OpenAlex candidate. The supplied candidate export contains no quality flags or
alternate profile candidate for these five author IDs. Current SQL checks must
still pass before approval, including the separate directory-duplicate issues.
Replaying the local profile and author files through the Bronze/Silver adapters
also reproduced all five pinned candidate IDs and evidence hashes, with no
researcher flags or directory-duplicate issues on these selected profiles.

| Researcher | OpenAlex author ID | Profile-linked ORCID | Collected work records | Records with ZU in this author's affiliations |
|---|---|---|---:|---:|
| Ahmed Shuhaiber | A5014266735 | 0000-0001-9827-2028 | 88 | 71 |
| Andrew Marrington | A5062611532 | 0000-0002-3839-6675 | 53 | 39 |
| Dima Rabadi | A5022721209 | 0000-0001-8067-1995 | 11 | 0 |
| Fatma Outay | A5075567278 | 0000-0002-9300-6270 | 65 | 52 |
| Graham Manville | A5064738105 | 0000-0003-4341-1363 | 72 | 8 |

Counts above are unique OpenAlex work IDs in the downloaded responses, not lifetime
totals, unique studies, or guaranteed expertise-eligible records. No retraction
flags or contradictory authorship ORCIDs were found for these five in the reviewed
collection. Silver eligibility may exclude other quality issues. Approval verifies
the identity link; it does not independently validate every paper or its findings.

### Evidence supporting the recommendations

- **Ahmed Shuhaiber:** the ZU biography describes information systems,
  human–computer interaction, smart homes, technology adoption and cyber behaviour.
  Collected evidence includes *Cyberbullying and stress, anxiety, and depression
  among university students: social support and self-esteem as mediators*
  (2025; DOI `10.1080/13614568.2025.2487269`). That authorship has the matching
  ORCID and ZU affiliation.
- **Andrew Marrington:** the ZU biography names digital forensics and information
  security. *Forensic Investigation of Humanoid Social Robot: A Case Study on
  Zenbo Robot* (2024; DOI `10.1145/3664476.3670906`) has matching ORCID and ZU
  affiliation. This is consistent subject evidence alongside the direct ORCID link.
- **Dima Rabadi:** the ZU biography says she joined in January 2026 and explicitly
  names her FETA malware-analysis work. The collection contains *FETA: A systematic
  and efficient approach for feature engineering on anti-static and anti-dynamic
  malware analysis* (2025; DOI `10.1016/j.jisa.2025.104104`), with the matching
  ORCID. No downloaded authorship lists ZU; this is not contradictory to earlier
  career publications. Two chatbot benchmark records have closely related Zenodo
  DOIs (`10.5281/zenodo.18509542`, `10.5281/zenodo.18509541`); inspect their
  version relationship and publication type before Gold presents study counts.
- **Fatma Outay:** the ZU profile describes wireless/mobile networks, IoT, smart
  cities and connected mobility. *Behavioural Modelling of Automated Driving Logic
  for Advanced Driver Assistance Systems at Signalised Intersections Using
  Microsimulation* (2026; DOI `10.1007/s13369-026-11330-x`) has matching ORCID and
  ZU affiliation, consistent with the profile's research areas.
- **Graham Manville:** the ZU profile describes management, performance management,
  entrepreneurship and employability. Its publication list explicitly includes
  *(Re)Framing sustainable careers: toward a conceptual model and future research
  agenda* (2024; DOI `10.1108/cdi-02-2024-0073`), present in the collection with
  matching ORCID and ZU affiliation. Earlier publications need not list ZU.

For the source profile URLs and collected publication evidence, run the preview
queries in SQL 07. Exact candidate IDs and evidence hashes are pinned in that file.
Local sources: `data/landing/pilot-20260905/manifest.json` and
`data/landing/openalex-20260905/manifest.json`, with their manifested raw files.

## Databricks steps

1. Import [`sql/07_review_identity_batch_02.sql`](../sql/07_review_identity_batch_02.sql)
   as a **SQL notebook**, alongside SQL 06. Use your existing compute. It needs
   the existing Silver tables, not another Bronze upload, API key or package.
2. Leave `SELECT false AS approve_reviewed_batch` unchanged and **Run All**.
   This previews five identities and records **zero decisions** by default.
3. Inspect the five-row checks table and publication evidence. All five safety
   columns must be `true`, and `all_checks_pass` must be `true`. If any check
   fails, stop and share that output; do not edit the whitelist or hashes to
   bypass it. `already_recorded` is normally false on the first run.
4. If you agree with **all five** recommendations, change just the permission
   cell to `SELECT true AS approve_reviewed_batch`, then **Run All**. This is
   your explicit approval; the INSERT records your user identity and timestamp.
   If you disagree with any recommendation, keep false and review separately.
5. Confirm five matching `accept` records in the last result. The first approval
   inserts five; an ordinary sequential repeat inserts zero. Immediately restore
   the permission cell to `false` and run that cell to reset the session flag.
6. In notebook 05, set `mode=validate` and **Run All** (not just its last cell).
   Keep widget execution behaviour **Do Nothing**. Check the counts, then set
   `mode=build` and **Run All** again.
7. Run SQL 06. With no other changes, expect **7 verified candidates and 116
   pending**. Work/topic counts must be measured from the rebuild; shared papers
   and exclusions make addition of raw counts an unreliable prediction.

Do not run the approval notebook concurrently with another reviewer, Silver build
or source refresh. Sequential retries are guarded; this is not a multi-user review
service. The `*_current` identity statuses update only after notebook 05 publishes
a successful rebuild. The decision history can show accepts before then.

The only persistent write in SQL 07 is the guarded append to `identity_decisions`.
It will not override a rejection or an earlier decision on different evidence.
All five must pass; missing or duplicated rows block the entire batch.

## Cases intentionally held out and next milestones

- Ahed's other OpenAlex IDs `A5135111126` and `A5136766665`: shared ORCID across
  author records needs publication-level review; neither is added here.
- Ashraf Khalil `A5074885987`: alternate campus profile anchors must be resolved
  before approval. Empty candidate flags alone do not clear this ambiguity.
- Conflicting ORCIDs and name-only candidates: retain pending until investigated.
- Source profiles contain potentially useful grant statements (including Ahmed
  and Dima), but those remain attributed source text, not verified grant rows.
  Any structured grant extraction needs separate award-evidence review.
- After this batch: inspect leading topic evidence and publication/version types,
  review more identities, then build a small Gold profile/search-document pilot.
  Gold must exclude retracted evidence and disclose incomplete profile coverage.
  No new AI Search endpoint or Lakebase resource is required for this step.

## Validation boundary

Offline tests exercise the SQL decision predicates and insertion guard through a
SQLite compatibility harness, plus the existing Silver tests. They do not test
Databricks SQL parsing, permissions or Delta execution. The first default-false
Databricks run is the runtime validation step; no remote run has been performed here.
