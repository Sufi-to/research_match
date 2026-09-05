# Local data collection for the ResearchMatch pilot

The first milestone is 50 candidate researchers across Business (CB) and
Technological Innovation (CTI), with publications and source evidence ready for
Bronze processing. Profiles are candidates until their identities and current roles
have been reviewed. The source snapshot includes the complete ZU Scholars works set
and targeted ZU OpenAlex works so a small pilot does not lose publication coverage.

## 1. Set up the local collector

Run from the project root, using Python 3.10 or newer:

```sh
python3 -m pip install -r requirements-collection.txt
```

Create a local `.env` file beside `requirements-collection.txt`:

```text
OPENALEX_API_KEY=your_private_key
```

Get a free key from [OpenAlex API settings](https://openalex.org/settings/api).
The collector also accepts the `OPENALEX_API_KEY` environment variable, which takes
precedence over `.env`. It sends the key in an Authorization header and excludes it
from the manifest. Never upload `.env` to a Volume or commit it. `.env` and the data
directory are excluded from Git.

As checked on September 5, 2026, the current [OpenAlex authentication documentation](https://help.openalex.org/api/authentication/)
allows basic anonymous requests and gives free-key users a larger daily budget.
This supersedes the earlier chat statement that every request needs a key. Use a key
for the full collection. The collector stops on authentication or rate-limit errors
and records the source as partial.

## 2. Download source data

```sh
python3 scripts/collect_sources.py --source all --batch data/landing/pilot-20260905
```

Alternatively run the stages separately in this order:

```sh
python3 scripts/collect_sources.py --source faculty --batch data/landing/pilot-20260905
python3 scripts/collect_sources.py --source oai --batch data/landing/pilot-20260905
python3 scripts/collect_sources.py --source openalex --batch data/landing/pilot-20260905
```

The default ceiling is 300 HTTP requests per invocation, including retries. If that
ceiling is reached, rerun the identical command to continue from cached files.
The collector uses serial requests, timeouts, and bounded retries for temporary
network/server failures. It preserves successful response bytes and records source
status in `manifest.json`; a partial run exits with code 2.

Reusing a batch resumes the same snapshot. Cached files are checksum-verified and
are not refreshed. Use a new directory for a later source refresh. Do not run two
collectors against the same batch simultaneously. If an OAI cursor expires, retain
the incomplete batch for diagnosis and start a new batch for a complete harvest.

## 3. What is downloaded

| Source | Files | Scope and purpose |
|---|---|---|
| ZU directories | HTML | Both college directories plus linked departmental listings; retains staff and adjunct categories for later filtering |
| ZU profiles | HTML | Up to 25 professor-ranked faculty/academic administrator candidates per college, selected alphabetically; a reproducible engineering sample |
| ZU Scholars | XML | Protocol responses and all pages of `publication:works` in `oai_dc`; no publication files |
| OpenAlex institution | JSON | Verified Zayed University institution `I91044093` |
| OpenAlex works | JSON | All ZU-affiliated works, missing repository DOIs, and career works for profile-linked ORCID candidates |
| OpenAlex authors | JSON | Profile ORCID or exact name/affiliation candidates; unresolved names receive a bounded top-five search for review; no automatic identity approval |
| OpenAlex awards/funders | JSON | Institution and publication-linked award/funder evidence; does not establish an individual's PI status |
| ZU institutional awards | No automatic download | Optional, pending an approved institutional export |

OpenAlex topic and coauthor data remain nested inside the original works JSON.
Separate topic API downloads are not required for this first collection.
Institution works are collected from both OpenAlex's core and expansion corpus;
DOI and career queries use `corpus=all`. Preserve `is_xpac` so Silver can flag the
often thinner expansion records rather than silently excluding datasets.
Funding mentions in profile HTML are preserved for later review; they are not
turned into confirmed grants during collection.

The directory layout is:

```text
data/landing/pilot-20260905/
  manifest.json
  raw/
    zu_faculty_protocol/
    zu_faculty_pages_raw/
    zu_scholars_protocol/
    zu_scholars_oai_raw/
    openalex_institutions_raw/
    openalex_works_raw/
    openalex_authors_raw/
    openalex_awards_raw/
    openalex_funders_raw/
  derived/
    faculty_roster.json
    pilot_researchers.json
    faculty_issues.json
    oai_dois.json
    oai_identifiers.json
    openalex_work_ids.json
    author_identity_candidates.json
    unresolved_dois.json
    unresolved_award_ids.json
    unresolved_funder_ids.json
```

Only successfully reached datasets appear. Derived files are collection aids,
not canonical Silver tables. Raw files and their manifest are the replay inputs.
The manifest records request parameters, fetch time, payload checksum, bytes,
record count where applicable, and paging cursors. API response pages can contain
multiple records, which notebook 03 splits into individual Bronze rows.

## 4. Verify and upload

```sh
python3 scripts/collect_sources.py --source verify --batch data/landing/pilot-20260905
```

Verify all required public sources report `complete`, then inspect the pilot roster
and author review candidates. `complete` means the configured requests finished;
it does not mean every university researcher or all of their historical work has
been identified. An empty award result is valid and is not proof of no funding.
Career publication expansion for name-only author candidates waits for identity
review. The first batch can therefore omit pre-ZU works for researchers without a
verified identifier; record that gap instead of claiming complete career coverage.

Upload the batch folder, preserving its structure, to a managed Unity Catalog Volume,
for example under `/Volumes/workspace/researchmatch_bronze/landing/pilot-20260905/`.
The `landing` Volume must first be created; the current Bronze DDL does not create it.
Upload only the batch folder, never the repository root or `.env`.

To produce a checksum-verified archive for transfer:

```sh
python3 scripts/package_collection.py --batch data/landing/pilot-20260905 --output data/exports/researchmatch-pilot-20260905.tar.gz
```

Extract the archive before uploading its folders, or later extract it into the
Volume using a controlled loader. An archive is not automatically a readable Delta
source. The package includes only manifested raw files, derived JSON, and manifests.

For independent collection processes, OpenAlex can use a separate output batch:

```sh
python3 scripts/collect_sources.py --source openalex --batch data/landing/openalex-20260905 --inputs-batch data/landing/pilot-20260905
python3 scripts/package_collection.py --batch data/landing/pilot-20260905 --batch data/landing/openalex-20260905 --output data/exports/researchmatch-pilot-20260905.tar.gz
```

This allows institution-work downloads while OAI is running without two processes
writing the same manifest. OpenAlex pauses after those downloads if its faculty/OAI
inputs are incomplete; rerun it when they finish. Its manifest records the upstream
batch and checksum. The packager requires all three public sources to be complete
across the supplied batches; optional institutional awards do not block packaging.

Next follow the [Volume-to-Bronze runbook](volume_to_bronze_runbook.md) using notebook
03. It verifies the manifest, splits source pages into records, preserves lineage,
and performs insert-only version merges. The two batches are now uploaded by the
user under `/Volumes/workspace/researchmatch_bronze/landing/`; a live Delta load is
still to be verified. The direct OAI notebook remains an alternative and does not
read these landed files. The local collector itself never writes Databricks tables.

## 5. Institutional data, when available

An official roster can supply institutional researcher IDs, names, college,
department, current role, status, and public identifiers. It is optional for the
public-data pilot but improves identity and current-employment verification.

For awards, request an export containing award ID, project title, funder, investigator
IDs/roles, dates, and disclosure classification. Amount/currency and project abstract
are optional. See `institutional_data_contract.md`. Missing institutional funding
data does not block collaborator matching and must not be treated as zero awards.
