-- Databricks notebook source
-- Five reviewed identity candidates ONLY. Default Run All records no decisions.
-- First read docs/identity_review_batch_02.md and inspect the preview below.
-- Do not run concurrently with another reviewer, a Silver build, or source refresh.
USE CATALOG workspace;
USE SCHEMA researchmatch_silver;

-- COMMAND ----------
-- Keep false for the first run. Change to true ONLY if you approve all five
-- identities after review. Restore false immediately after recording decisions.
CREATE OR REPLACE TEMP VIEW rm_batch02_permission AS
SELECT false AS approve_reviewed_batch;

-- COMMAND ----------
-- Pinned to the evidence reviewed on 2026-09-09. Never replace these hashes with
-- new hashes just to pass a check: changed evidence requires another review.
CREATE OR REPLACE TEMP VIEW rm_batch02_targets
  (reviewed_name, candidate_id, expected_author_id, expected_orcid, expected_evidence_hash)
AS VALUES
  ('Ahmed Shuhaiber',
   '86050149bfa067a3d7f061043511e21b3542e59f1f828c9c0cef2617addd3802',
   'https://openalex.org/A5014266735', 'https://orcid.org/0000-0001-9827-2028',
   'f32582381cfcd9268ca2e89622f32380a9fcd7e58298d349c3d1bb83cd0a26d8'),
  ('Andrew Marrington',
   'd34c761d225e3eae4eff05a31b89e0be599c4ed6edbb914c4165ff4c00ed7b65',
   'https://openalex.org/A5062611532', 'https://orcid.org/0000-0002-3839-6675',
   '6e47355b93436a0a983d9f8c3a4584b6d64bced0971202f96b0aabd6a2d0784c'),
  ('Dima Rabadi',
   'f2ee4dd76b837b653c61804d9fe93e4a61d0c804ead5295b73461e44196f0e6d',
   'https://openalex.org/A5022721209', 'https://orcid.org/0000-0001-8067-1995',
   'c2d0cf9ee22791f0dde7fb82197ce805b8653470680c82cbcfa6db1fa48476cf'),
  ('Fatma Outay',
   '8f074a9069a87ebf9188ea2f6bc4a0ff80b9624c849ccc74301cd41b54b9b559',
   'https://openalex.org/A5075567278', 'https://orcid.org/0000-0002-9300-6270',
   'dbcbd26061cf631149950ecde3bb7a16c8765e6119d176f16335f788fc68d539'),
  ('Graham Manville',
   'a1eb62811da203298063bdf8f3b9bd0ba0ed0d4a526039801bd8e5d892c999d5',
   'https://openalex.org/A5064738105', 'https://orcid.org/0000-0003-4341-1363',
   '7835e65a7b1544bfbea00ed0cb382232c30060bea0b5aecb2ff1b782acef68eb');

-- COMMAND ----------
-- Consult decisions directly: *_current status stays unchanged until a rebuild.
CREATE OR REPLACE TEMP VIEW rm_batch02_latest_decisions AS
SELECT decision_id, candidate_id, evidence_hash, decision
FROM (
  SELECT decision_id, candidate_id, evidence_hash, decision,
         ROW_NUMBER() OVER (
           PARTITION BY candidate_id ORDER BY reviewed_at DESC, decision_id DESC
         ) AS position
  FROM identity_decisions
) ranked
WHERE position = 1;

-- COMMAND ----------
CREATE OR REPLACE TEMP VIEW rm_batch02_checks AS
SELECT t.*, c.researcher_id, c.status, r.profile_url,
       COALESCE(
         c.openalex_author_id = t.expected_author_id
         AND c.evidence_hash = t.expected_evidence_hash
         AND c.match_method = 'profile_orcid'
         AND c.status IN ('pending', 'verified')
         AND size(c.quality_flags) = 0
         AND size(r.quality_flags) = 0
         AND size(r.profile_orcids) = 1
         AND array_contains(r.profile_orcids, t.expected_orcid)
         AND a.orcid = t.expected_orcid, false
       ) AS evidence_matches,
       NOT EXISTS (
         SELECT 1 FROM entity_resolution_review_current other
         WHERE other.openalex_author_id = t.expected_author_id
           AND other.researcher_id <> c.researcher_id
           AND other.status <> 'rejected'
       ) AND NOT EXISTS (
         SELECT 1 FROM quality_issues_current q
         WHERE q.entity_type = 'researcher' AND q.entity_id = c.researcher_id
           AND q.issue_code = 'other_directory_urls_same_name'
       ) AS no_cross_profile_ambiguity,
       NOT EXISTS (
         SELECT 1 FROM authors_current other
         WHERE other.orcid = t.expected_orcid
           AND other.openalex_author_id <> t.expected_author_id
       ) AS no_split_orcid,
       NOT EXISTS (
         SELECT 1 FROM work_authorships_current wa
         WHERE wa.openalex_author_id = t.expected_author_id
           AND wa.author_orcid IS NOT NULL AND wa.author_orcid <> t.expected_orcid
       ) AS no_work_orcid_conflict,
       (d.decision_id IS NULL OR
         (d.decision = 'accept' AND d.evidence_hash = t.expected_evidence_hash)
       ) AS decision_allows_accept,
       d.decision_id IS NOT NULL AS already_recorded
FROM rm_batch02_targets t
LEFT JOIN entity_resolution_review_current c ON c.candidate_id = t.candidate_id
LEFT JOIN researchers_current r ON r.researcher_id = c.researcher_id
LEFT JOIN authors_current a ON a.openalex_author_id = c.openalex_author_id
LEFT JOIN rm_batch02_latest_decisions d ON d.candidate_id = t.candidate_id;

-- COMMAND ----------
-- Expect exactly five rows, all five safety columns true. already_recorded is
-- false on the first run, true after approval (even before rebuilding Silver).
SELECT reviewed_name, profile_url, expected_author_id, expected_orcid, status,
       evidence_matches, no_cross_profile_ambiguity, no_split_orcid,
       no_work_orcid_conflict, decision_allows_accept, already_recorded
FROM rm_batch02_checks
ORDER BY reviewed_name;

-- COMMAND ----------
-- Counts are collected work records, not necessarily distinct research studies.
SELECT t.reviewed_name,
       COUNT(DISTINCT w.work_id) AS collected_work_records,
       COUNT(DISTINCT CASE WHEN w.eligible_for_expertise THEN w.work_id END)
         AS potentially_eligible_work_records,
       COUNT(DISTINCT CASE WHEN w.is_retracted THEN w.work_id END) AS retracted_records
FROM rm_batch02_targets t
LEFT JOIN work_authorships_current wa ON wa.openalex_author_id = t.expected_author_id
LEFT JOIN works_current w ON w.work_id = wa.work_id
GROUP BY t.reviewed_name
ORDER BY t.reviewed_name;

-- COMMAND ----------
-- Publication evidence for review. This deliberately does not require a ZU
-- affiliation: earlier career work belongs in a verified researcher's profile.
SELECT DISTINCT t.reviewed_name, w.work_id, w.title, w.doi, w.publication_year,
       wa.author_orcid, wa.institution_ids, w.eligible_for_expertise, w.quality_flags
FROM rm_batch02_targets t
JOIN work_authorships_current wa ON wa.openalex_author_id = t.expected_author_id
JOIN works_current w ON w.work_id = wa.work_id
ORDER BY t.reviewed_name, w.publication_year DESC, w.work_id;

-- COMMAND ----------
-- The ENTIRE batch is blocked if any check fails or a join duplicates a target.
CREATE OR REPLACE TEMP VIEW rm_batch02_gate AS
SELECT COUNT(*) = 5 AND COUNT(DISTINCT candidate_id) = 5
       AND SUM(CASE WHEN evidence_matches AND no_cross_profile_ambiguity
                          AND no_split_orcid AND no_work_orcid_conflict
                          AND decision_allows_accept THEN 1 ELSE 0 END) = 5
       AS all_checks_pass
FROM rm_batch02_checks;

SELECT p.approve_reviewed_batch, g.all_checks_pass,
       CASE WHEN p.approve_reviewed_batch AND g.all_checks_pass
            THEN 'Enabled: only previously unrecorded accepts will be inserted'
            ELSE 'Disabled: no decisions will be inserted' END AS action
FROM rm_batch02_permission p CROSS JOIN rm_batch02_gate g;

-- COMMAND ----------
-- ONLY persistent write in this notebook. Default permission=false inserts zero.
-- Explicit column names; exact whitelist; no updates/deletes or blanket approvals.
INSERT INTO identity_decisions
  (decision_id, candidate_id, evidence_hash, decision, rationale, reviewed_by, reviewed_at)
SELECT uuid(), c.candidate_id, c.expected_evidence_hash, 'accept',
       'Approved batch 02 after reviewing docs/identity_review_batch_02.md and current SQL evidence: ZU profile-linked ORCID matches OpenAlex; publication evidence checked.',
       current_user(), current_timestamp()
FROM rm_batch02_checks c
CROSS JOIN rm_batch02_permission p
CROSS JOIN rm_batch02_gate g
WHERE p.approve_reviewed_batch AND g.all_checks_pass AND NOT c.already_recorded;

-- COMMAND ----------
-- Expect zero records in preview mode; five after the first deliberate approval.
-- Re-running does not add duplicate accepts. Any rejection/stale decision blocks
-- this batch and must be reviewed separately, not overwritten here.
SELECT t.reviewed_name, d.decision, d.evidence_hash, d.reviewed_by, d.reviewed_at
FROM rm_batch02_targets t
JOIN identity_decisions d ON d.candidate_id = t.candidate_id
ORDER BY t.reviewed_name, d.reviewed_at DESC;

-- Restore approve_reviewed_batch=false. Then Run All in notebook 05 with
-- mode=validate; inspect counts, then mode=build. Run SQL 06 again afterwards.
