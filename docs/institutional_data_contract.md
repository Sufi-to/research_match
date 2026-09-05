# Optional institutional source deliveries

No official ZU roster or award export has been supplied. Public profiles and
publications support the pilot while these sources remain optional.

## Researcher roster

Preferred delivery: UTF-8 CSV or JSON, with one row per researcher and a delivery
date. Request these fields when available:

- `researcher_id`: stable institutional identifier;
- `full_name`, `known_aliases`;
- `college`, `department`, `campus`, `rank`, `role`;
- `active_status` and its effective date;
- `orcid`, `profile_url`;
- `institutional_email`, only when approved for this use.

## Historical research awards

Preferred delivery: JSON for repeated investigators, or CSV with a separately
documented investigator relation. Request:

- stable internal award ID and external funder award ID when available;
- project title, funder, program;
- investigator institutional IDs and PI/co-investigator roles;
- start date, end date, status;
- source update timestamp;
- visibility/disclosure classification;
- project abstract and award amount, currency, ZU share only if approved.

Accompany each delivery with its source office, delivery ID/date, field definitions,
coverage period, and confirmation of which fields may be used in this prototype.
Do not infer public permission from possession of a file. Begin this Free Edition
pilot with public or explicitly publishable data; restricted institutional delivery
requires a suitable university-approved environment.

Preserve the original export. Normalize identifiers and relationships in Silver.
Award attribution must distinguish an investigator's role from a publication that
merely acknowledges a funder. Do not fill missing amounts, dates, or award records
with fabricated values.
