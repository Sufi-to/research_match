# ResearchMatch pilot collection — 2026-09-05

The configured public-source collection completed locally. No files have been
uploaded to Databricks, and no Delta tables have been populated by this collection.

The verified package contains 453 raw source files (about 266.4 MiB), plus manifests
and derived collection aids. The compressed transfer archive is about 40.7 MiB.
Its SHA-256 is `e179f2db6303b6b9b14e6f5b76477f9800ae5101d18acd2ee305cc17d286d76c`.

## Collected data

| Dataset | Observed result |
|---|---|
| Business and Technological Innovation directories | 165 deduplicated directory entries, including personnel categories outside the pilot |
| Pilot profile pages | 50 downloaded: 25 from each college |
| Profile ORCID coverage | 23 of 50 profiles contain ORCID links |
| ZU Scholars | 8,006 unique OAI records across 81 publication pages |
| Repository DOI extraction | 4,994 records contain a DOI; 4,986 unique DOIs |
| OpenAlex institutions | Zayed University institution record `I91044093` |
| OpenAlex works | 12,721 unique works across institution, repository DOI, and profile-ORCID career queries |
| OpenAlex author candidates | 129 distinct candidate author IDs across 49 of 50 pilot profiles |
| OpenAlex awards | 2,283 historical award records retained as evidence |
| OpenAlex funders | 1,564 funder records |
| Official ZU roster/award export | Not supplied; optional for this public-data pilot |

OpenAlex work totals include 11,610 works from the combined core/expansion institution
queries. The downloaded institution object reports `works_count=11,602`; preserve
this eight-record discrepancy for reconciliation rather than assuming its aggregate
is an exact snapshot count. Across all work queries, 1,491 records are marked
`is_xpac` and should retain that quality/context flag in Silver.

Career works were collected for 25 author IDs found through profile ORCID queries.
This does not establish 25 uniquely verified researcher identities: multiple source
author candidates can exist for one person.

## Initial quality checks

- Every repository record has a title and creator.
- 7,306 repository records contain descriptions; 8,004 contain subjects.
- No duplicate OAI identifiers were observed in the completed harvest.
- Faculty profile samples contain the expected named profile pages.
- All author identity relationships remain `needs_review`; name matching alone does
  not establish identity or current employment.
- `Balqees Al Messabi` has no OpenAlex candidate from the configured collection and
  needs manual identifier/publication review.
- 22 extracted repository DOIs remain unresolved in OpenAlex.
- 119 award IDs referenced by works were not returned by full-ID batch queries.
  Two examples, `G1229298752` and `G1242329236`, also returned HTTP 404 on singleton
  spot checks. Retain all unresolved references for later reconciliation.
- All 1,564 requested funder IDs were returned.
- The institution-award query returned one award with a lead affiliation that does
  not clearly match ZU. It remains unverified evidence.

The award/funder totals describe evidence attached to the collected publications,
which include coauthors and large collaborations. They are **not counts of grants
received by ZU or the pilot researchers**. Amounts and investigator roles must not
be shown as confirmed until Silver establishes the attribution.

The alphabetical, professor-ranked pilot is a reproducible engineering sample,
not a representative evaluation sample of all ZU researchers. Full university
coverage and career expansion for name-only candidates remain later work.

## Files and reproducibility

- `data/landing/pilot-20260905/`: faculty HTML, OAI XML, manifest, roster, and DOI lists.
- `data/landing/openalex-20260905/`: OpenAlex JSON, manifest, candidate identities,
  unresolved DOI/award/funder lists, and work IDs.
- `data/exports/researchmatch-pilot-20260905.tar.gz`: transfer archive of both batches.
- `data/exports/researchmatch-pilot-20260905.tar.inventory.json`: archive checksum,
  file/byte inventory, and source completion states.

Each raw file has a SHA-256 checksum and request provenance. The packager verifies
the manifested files and requires all public-source stages to complete. It includes
only manifested raw files, derived JSON, and the batch manifests. Diagnostic empty
responses from earlier queries remain part of the raw request history.

The collector has seven passing automated tests covering OAI parsing, DOI extraction,
faculty categories and URL scope, resume/checksum behavior, credential exclusion,
rate-limit handling, archive allowlisting, and the award API's full-ID filter contract.
Live collection also exercised all configured public endpoints.

See [collection runbook](data_collection_runbook.md) for resume and packaging commands.
Data and `.env` are excluded from Git; only code, configuration, tests, and documentation
are versioned. Preserve the data archive separately when moving to another machine.

## Next implementation

Create a managed landing Volume and implement a manifest-driven loader into the
existing Bronze tables. It must split OAI/API pages into individual records, retain
source lineage, hash/version records, and avoid duplicates when reloading either
batch. Then review pilot identities before building canonical Silver profiles.
