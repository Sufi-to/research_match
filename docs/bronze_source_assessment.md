# ResearchMatch AI — Bronze Source Assessment

> **Scope notice:** The current product direction is defined in
> [`product_plan.md`](product_plan.md). This assessment covers the Bronze sources needed
> for historical researcher profiles, award evidence, and topic-to-collaborator matching.

Date assessed: 2026-08-20 (Asia/Dubai)

## Decision summary

The revised ResearchMatch MVP needs the following Bronze source families plus shared
ingestion-run and error tables:

1. ZU public faculty roster/profile HTML, unless an official institutional export is provided.
2. ZU Scholars OAI-PMH publication metadata in `oai_dc` format.
3. An approved ZU Research Office historical grant/award delivery.
4. OpenAlex institutions, authors, works, awards, and funders JSON for the targeted ZU subset.

Do not download publication PDFs or award attachments for the MVP. Store their approved
metadata and public URLs only. Do not use the OpenAlex institution-author query as the
authoritative faculty roster, and do not treat OpenAlex award associations as confirmed
until Silver validation succeeds.

## Confirmed identifiers and endpoints

| Source | Endpoint or identifier | Authentication | Use |
|---|---|---|---|
| ZU faculty website | College-specific `faculty_and_staff` HTML pages and linked profile pages | None | Authoritative fallback roster and profile descriptions |
| ZU Scholars | `https://zuscholars.zu.ac.ae/do/oai/` | None | ZU publication metadata |
| OpenAlex | `https://api.openalex.org` | Basic anonymous access; free key recommended for collection, read from local `.env` or environment | Publication, author, topic, affiliation, citation, and coauthor enrichment |
| Zayed University in OpenAlex | OpenAlex `I91044093`; ROR `03snqfa66` | N/A | Exact institution filtering and affiliation validation |

## 1. ZU faculty data

### Access method

No documented public faculty API was found. The public website has separate college pages containing Administrators, Faculty, Adjunct Faculty, and Staff sections. Each person can link to an individual HTML profile.

An official HR or Office of Research export should replace the website as the authoritative roster if one becomes available. Until then, ingest the website as a versioned HTML source and apply role filtering only in Silver.

### Data observed

Listing pages provide:

- name;
- section or personnel category;
- academic/administrative role;
- academic rank or teaching area;
- college implied by the page URL;
- profile URL;
- image URL.

Individual profiles can provide:

- rank;
- qualifications;
- biography;
- research interests and professional activities;
- teaching areas;
- selected publications;
- institutional email;
- campus/office and phone.

### What ResearchMatch needs

| Field | MVP decision | Reason |
|---|---|---|
| Name | Keep | Identity resolution and display |
| College and department/teaching area | Keep | Filters and expertise context |
| Personnel category | Keep | Separate faculty, administrators, adjuncts, and staff |
| Rank/role | Keep | Profile context |
| Biography | Keep | Research-profile text |
| Research and teaching areas | Keep | Matching features and semantic search |
| Institutional email | Keep, access-controlled | Strong identity signal and contact display |
| Qualifications | Keep | Expertise evidence |
| Profile URL | Keep | Provenance |
| Image URL | Optional | UI only; no matching value |
| Office and phone | Defer | Not needed for matching |
| Selected-publication text | Preserve in raw HTML; do not parse initially | ZU Scholars and OpenAlex are more structured |

### Risks

- Public pages are not consistently structured across colleges.
- Some pages show stale-looking or test records, including a visible `Test_123` staff entry.
- Listing pages may have old “last updated” dates.
- Academic administrators can also be researchers, so they must not be excluded solely because they appear under Administrators.
- Adjunct participation needs a product policy; keep it in Bronze and decide in Silver.

### Proposed Bronze tables

`bronze.zu_faculty_pages_raw`

- `page_url`
- `page_type` (`listing` or `profile`)
- `college_code`
- `http_status`
- `fetched_at`
- `content_hash`
- `raw_html`
- `ingestion_run_id`

Optionally create `bronze.zu_faculty_discovered_profiles` as a crawl manifest with profile URL, listing section, displayed name, and crawl status. This is operational metadata, not the canonical researcher table.

## 2. ZU Scholars OAI-PMH

### Confirmed repository behavior

`Identify` returned:

- repository name: `ZU Scholars`;
- protocol: OAI-PMH 2.0;
- earliest datestamp: `2000-01-19T00:00:00Z`;
- granularity: seconds in UTC;
- `deletedRecord=no`;
- metadata may be used by commercial and non-commercial service providers;
- full content may not be harvested by robots without prior written approval.

Supported formats include `oai_dc`, qualified Dublin Core aliases, ETD-MS, and OpenAIRE. The primary ingestion format should be `oai_dc` because a tested record contained its DOI and full-text URL there. The qualified Dublin Core sample contained a better human-readable type and journal field but omitted the DOI. OpenAlex can supply journal/type enrichment later.

Useful sets include:

- `publication:works` — all works;
- `publication:researcharticles`;
- `publication:bookchapters`;
- `publication:books`;
- `publication:conferenceproceedings`;
- `publication:workingpapers`;
- college sets such as `publication:cb`, `publication:cti`, and `publication:cnhs`;
- `publication:openaccess` and `publication:scopus-indexed`.

Harvest `publication:works` once rather than harvesting overlapping content-type and college sets separately. Preserve all returned `setSpec` values for classification.

### Data returned by `oai_dc`

- OAI identifier;
- OAI datestamp;
- one or more set specifications;
- title;
- repeated creators;
- abstract/description;
- publication date;
- broad type;
- repeated identifiers, including repository URL, DOI, and sometimes file URL;
- source;
- publisher;
- repeated subjects.

### What ResearchMatch needs

Keep all fields above. DOI is the preferred link to OpenAlex. Creator order must be preserved. Store file URLs but do not download files.

### Important limitations

- Because `deletedRecord=no`, the endpoint will not report deletions. A periodic full identifier reconciliation is required.
- A live `ListIdentifiers` request using `from=2024-01-01` returned some headers with earlier datestamps. Treat incremental date filtering as an optimization, not the only completeness control, until this behavior is tested across a complete harvest.
- OAI `resumptionToken` values are opaque and must be followed exactly.
- The OAI identifier and the repository page identifier can differ; retain both.

### Proposed Bronze table

`bronze.zu_scholars_oai_raw`

- `oai_identifier`
- `oai_datestamp`
- `set_specs` (`array<string>`)
- `metadata_prefix`
- `response_date`
- `resumption_token_used`
- `harvest_from`
- `harvest_until`
- `record_hash`
- `raw_record_xml`
- `fetched_at`
- `ingestion_run_id`

Store one complete `<record>` element per row rather than one large response per row. Store protocol errors and the final resumption token in the ingestion-run log.

## 3. OpenAlex

### Confirmed API behavior

The API is JSON over GET endpoints. Lists return `meta`, `results`, and `group_by`.
The supported maximum page size is 100; cursor paging is required beyond 10,000
results. The local collector reads its free API key from `.env` or the environment
and sends an Authorization header. Current authentication documentation, checked
2026-09-05, permits basic anonymous access with a smaller allowance.

The verified Zayed University institution record is:

- OpenAlex ID: `https://openalex.org/I91044093`;
- ROR: `https://ror.org/03snqfa66`;
- country: `AE`;
- type: `education`.

At assessment time, the institution record reported roughly 11,500 works. This is small enough for a targeted cursor-paged pull and is preferable to downloading the global OpenAlex snapshot.

### Correct acquisition strategy

1. Start with the active ZU roster.
2. Resolve exact ORCIDs when present.
3. Resolve ZU Scholars DOIs to OpenAlex works in batches of up to 100.
4. Use authorship plus ZU affiliation on those works to link researchers to OpenAlex author IDs.
5. Search by name only for unresolved faculty and retain match confidence for later review.
6. Pull all works linked to institution `I91044093` to discover ZU output missing from ZU Scholars.
7. Obtain coauthors from work authorships. Fetch full external author records only for useful collaborator candidates.

Do not treat `authors?filter=last_known_institutions.id:I91044093` as the faculty roster. A live test returned 2,156 authors and included people whose ZU affiliation was incidental or one of several recent affiliations.

### Required author fields

| Field | MVP decision | Use |
|---|---|---|
| `id` | Keep | Stable OpenAlex identifier |
| `orcid` | Keep | Strong identity resolution |
| `display_name` and alternatives | Keep | Name matching |
| `ids` | Keep | Crosswalks |
| `affiliations` | Keep | Affiliation history |
| `last_known_institutions` | Keep with caution | Context, not employment truth |
| `topics` | Keep | Expertise profile |
| `works_count`, `cited_by_count` | Keep | Profile metrics |
| `summary_stats`, `counts_by_year` | Keep | Activity and impact features |
| `updated_date` | Keep | Provenance and change comparison |

### Required work fields

| Field | MVP decision | Use |
|---|---|---|
| `id`, `ids`, `doi` | Keep | Deduplication and cross-source linking |
| `title` | Keep | Display and semantic text |
| `publication_year`, `publication_date` | Keep | Recency and timeline |
| `type`, `language` | Keep | Filtering |
| `authorships` | Keep | ZU author identification and collaborator graph |
| `primary_location` | Keep | Venue, landing page, source, and OA link |
| `open_access` | Keep | Access indicator |
| `topics` | Keep | Strong structured research signals |
| `keywords` | Keep | Additional text/features; lower trust than topics |
| `abstract_inverted_index` | Keep | Reconstruct abstract for semantic profiling |
| `cited_by_count` | Keep | Impact feature |
| `grants` | Keep when present | Historical funder/award evidence |
| `updated_date` | Keep | Change comparison |
| References and related works | Defer | Not required for MVP matching |

### Free-tier refresh implication

OpenAlex's `from_updated_date` and `from_created_date` sync filters are paid-plan features. For the free implementation:

- re-fetch the targeted ZU institution works on a schedule;
- use `per_page=100` and cursor paging;
- use `select` to limit payload fields;
- compare entity hashes and upsert changed rows;
- periodically re-fetch resolved author records;
- retain missing IDs for reconciliation rather than immediately deleting them.

### Proposed Bronze tables

`bronze.openalex_institutions_raw`

- `openalex_id`
- `raw_json`
- common ingestion metadata

`bronze.openalex_authors_raw`

- `openalex_author_id`
- `query_reason` (`orcid`, `doi_author`, `name_candidate`, `external_candidate`)
- `raw_json`
- common ingestion metadata

`bronze.openalex_works_raw`

- `openalex_work_id`
- `doi`
- `query_reason` (`zu_institution`, `zu_scholars_doi`, `author_refresh`)
- `raw_json`
- common ingestion metadata

`bronze.openalex_awards_raw`

- `openalex_award_id`
- `funder_award_id`
- `openalex_funder_id`
- `query_reason`
- `raw_json`
- common ingestion metadata

`bronze.openalex_funders_raw`

- `openalex_funder_id`
- `doi`
- `ror`
- `query_reason`
- `raw_json`
- common ingestion metadata

For all OpenAlex tables, include `fetched_at`, `source_updated_at`, `record_hash`, `request_url_without_api_key`, and `ingestion_run_id`. Never persist the API key in the request URL.

## 4. Shared ingestion metadata

`bronze.ingestion_runs`

- `ingestion_run_id`
- `source_system`
- `dataset_name`
- `started_at`
- `completed_at`
- `status`
- `request_parameters_sanitized`
- `watermark_or_cursor_start`
- `watermark_or_cursor_end`
- `pages_requested`
- `records_received`
- `records_inserted`
- `records_unchanged`
- `records_failed`
- `http_status_summary`
- `error_message`

`bronze.ingestion_errors`

- `ingestion_run_id`
- `source_record_id`
- `request_context_sanitized`
- `error_type`
- `error_message`
- `raw_error_payload`
- `occurred_at`

## 5. Minimum initial load order

1. Ingest ZU faculty listing and profile pages.
2. Harvest all ZU Scholars `publication:works` records using `oai_dc` and resumption tokens.
3. Load the approved institutional award delivery into `zu_grant_awards_raw` when available.
4. Extract DOIs and creator names from the OAI Bronze data.
5. Fetch the verified OpenAlex institution record.
6. Batch-resolve OAI DOIs to OpenAlex works.
7. Pull all OpenAlex works for institution `I91044093` with selected fields and cursor paging.
8. Resolve active roster members to OpenAlex authors using ORCID, DOI-authorship evidence, then name review.
9. Fetch candidate OpenAlex awards and linked funders for later Silver verification.
10. Re-fetch a sample of records and confirm that content hashes correctly distinguish unchanged and changed payloads.

## 6. Bronze readiness checks

The Bronze layer is ready for Silver design when:

- every source record can be traced to an ingestion run and request;
- the ZU roster categories are captured without prematurely excluding people;
- OAI resumption tokens complete a full harvest;
- DOI extraction successfully links a representative OAI record to OpenAlex;
- OpenAlex author resolution does not rely on institution membership alone;
- institutional award rows retain their approved visibility classification;
- OpenAlex award and funder records remain traceable evidence rather than confirmed claims;
- secrets and API response tokens are absent from stored payloads;
- raw HTML, XML, and JSON are retained for reprocessing;
- rerunning an unchanged extraction does not create uncontrolled duplicates.

## References

- [ZU Scholars OAI-PMH Identify](https://zuscholars.zu.ac.ae/do/oai/?verb=Identify)
- [OAI-PMH 2.0 specification](https://www.openarchives.org/OAI/openarchivesprotocol.htm)
- [OpenAlex API reference](https://help.openalex.org/api/)
- [OpenAlex authentication and limits](https://help.openalex.org/api/authentication/)
- [OpenAlex paging](https://help.openalex.org/api/paging/)
- [OpenAlex filtering and sync-filter availability](https://help.openalex.org/api/filtering/)
