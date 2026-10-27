# Identity review batch 03 — 2026-10-27

Status: **approved by the user on 2026-10-27** (six accepts recorded under their
identity via SQL 15's guarded INSERT). Silver `b239f898…`: 13 verified, 110 pending;
SQL 08 checks zero. Gold `6fcc7472…`: 13 profiles, 1,024 publications; SQL 11 checks
zero. Index recreated (see the AI Search runbook) and all 16 searches passed. This reviews the September 5 collection as loaded in
the current Silver build, not a fresh check of live websites.

## How these six were selected

Every pending candidate in live Silver was screened read-only with the batch 02
checks: profile-linked ORCID, exactly one profile ORCID, matching OpenAlex ORCID, no
candidate or researcher quality flags, no cross-profile or directory-duplicate
ambiguity, no split ORCID across author records, no conflicting authorship ORCIDs and
no earlier decision. Of 116 pending candidates, 97 are name-only matches and cannot
pass by design. Of the 19 ORCID-linked candidates, these six pass every check. The
whole-batch gate in SQL 15 returned `all_checks_pass = true` against live Silver.

| Researcher | OpenAlex author ID | Profile-linked ORCID | Collected work records | Eligible | Records listing ZU |
|---|---|---|---:|---:|---:|
| Ahmad Wazan | A5106467067 | 0000-0003-1385-4462 | 29 | 29 | 8 |
| Alena Connolly | A5036801930 | 0000-0002-7110-9594 | 28 | 28 | 12 |
| Asad Khattak | A5063432011 | 0000-0002-0630-1264 | 155 | 155 | 110 |
| Babar Shah | A5065504551 | 0000-0002-5090-4695 | 155 | 154 | 118 |
| Basel Almourad | A5038578892 | 0000-0003-2193-1472 | 57 | 57 | 44 |
| Fatma Taher | A5015357458 | 0000-0001-8358-9081 | 164 | 164 | 104 |

All six are College of Technological Innovation profiles, inside the pilot scope.
Counts are unique collected OpenAlex work records, not lifetime totals or distinct
studies. Approval verifies the identity link; it does not validate every paper.

### Evidence supporting the recommendations

All examples below carry the matching ORCID and a ZU affiliation on the authorship.

- **Ahmad Wazan:** the ZU biography names trust management, PKIs, access control
  and security requirements. Evidence: *Enhancing the ACME protocol to automate the
  management of all X.509 web certificates* (2025; DOI `10.1016/j.comcom.2025.108106`).
- **Alena Connolly** (profile name Lena Yuryna Connolly): the biography names
  cybercrime and information security management. Evidence: *Ransomware crime
  through the lens of neutralisation theory* (2025; DOI `10.1177/14773708251320464`).
  Two dataset records have consecutive Zenodo DOIs (`10.5281/zenodo.20757351`,
  `…352`), the same concept/version pattern resolved for Dima Rabadi. Check them in
  SQL 08 after the rebuild.
- **Asad Khattak:** evidence spans medical AI and NLP, e.g. *Assessing the
  adversarial robustness of multimodal medical AI systems* (2025; DOI
  `10.3389/fmed.2025.1606238`).
- **Babar Shah:** the biography names network communication, health informatics,
  IoT and AI. Evidence: *Hybrid 3D modelling framework for indoor navigation using
  federated learning and IoT-enabled edge devices* (2026; DOI `10.7717/peerj-cs.3503`).
  One retracted record (`oa:W4214873408`) is already excluded by Silver.
- **Basel Almourad:** evidence on human factors and AI acceptance, e.g. *Designing
  AI to foster acceptance* (2025; DOI `10.1080/0144929x.2025.2477053`).
- **Fatma Taher:** the biography names signal and image processing, pattern
  recognition and medical image analysis. Her record also includes antenna-design
  papers with matching ORCID and ZU affiliation; these broaden, not contradict, the
  identity link.

Khattak and Shah share 8 co-authored records; they are distinct authors.

## Databricks steps

Same procedure as batch 02, using [`sql/15_review_identity_batch_03.sql`](../sql/15_review_identity_batch_03.sql):

1. Import SQL 15 as a **SQL notebook**. Leave `SELECT false AS approve_reviewed_batch`
   and **Run All**. This previews six identities and records **zero decisions**.
2. Check the six-row table: all five safety columns and `all_checks_pass` must be
   `true`. If any check fails, stop and share the output; do not edit hashes.
3. If you agree with **all six**, change only the permission cell to `true` and
   **Run All**. Confirm six `accept` records, then restore `false`.
4. Notebook 05: `mode=validate`, check counts, then `mode=build`. Run SQL 06 and
   SQL 08. Expect **13 verified and 110 pending**. Measure work/topic counts from the
   rebuild; shared papers make adding raw counts unreliable.

Do not run the approval concurrently with another reviewer, Silver build or refresh.

## Held out

- **Split ORCID** (one ORCID on several OpenAlex author records): Ahed Abugabah's
  extra records, Ahmed Seffah, Belal AlSinglawi, Edmund Evangelista and Farkhund
  Iqbal. Approving one record per person would drop their other records' papers, so
  these need a separate multi-record review.
- **Ashraf Khalil:** alternate campus profile anchors are still unresolved.
- **Name-only matches** (97 candidates, up to 41 people): no ORCID evidence; not reviewable
  with this method.

## After approval: downstream changes

Notebooks 12 and 14 and the app no longer hard-code seven profiles. They require the
number of Gold search documents to equal the published Gold build's `document_count`,
with an upper bound of 50 (Gold's own limit). Evidence limits were raised to 5,000
publications, 7,500 topic rows and 25,000 topic-evidence rows, with the app's SQL
row bound raised to match. These changes also work with the current seven-profile
Gold build. **Notebook 13 is unchanged** and will refuse a Gold build that doesn't
have seven profiles; it remains a record of the seven-profile pilot.

Order after approval and the Silver rebuild:

1. Notebook 10 (Gold): set `expected_verified_profiles=13`, `validate`, then `build`.
   Run SQL 11.
2. Re-import notebooks 12 and 14 (updated). Notebook 12: `sync` once, wait, `status`,
   then `test`. Its topic hypotheses still name the original seven people.
3. Upload the updated `app/researchmatch/` files and redeploy the app. Then run the
   connection check: expect 13 researchers.
