-- Databricks notebook source
-- Four reviewed identity candidates ONLY (ORCID registry evidence, targeted batch). Default Run All records no decisions.
-- First read docs/identity_review_batch_06.md and inspect the preview below.
-- Do not run concurrently with another reviewer, a Silver build, or source refresh.
USE CATALOG workspace;
USE SCHEMA researchmatch_silver;

-- COMMAND ----------
-- Keep false for the first run. Change to true ONLY if you approve all four
-- identities after review. Restore false immediately after recording decisions.
CREATE OR REPLACE TEMP VIEW rm_batch06_permission AS
SELECT false AS approve_reviewed_batch;

-- COMMAND ----------
-- Pinned to the evidence reviewed on 2026-10-28. Never replace these hashes with
-- new hashes just to pass a check: changed evidence requires another review.
-- These ZU profiles list no usable ORCID. expected_orcid comes from the public ORCID
-- registry: the only record found for the name with a current Zayed University
-- employment entry (raw responses in landing batch targeted-20261029,
-- orcid_registry_raw). The OpenAlex author must carry exactly that ORCID.
CREATE OR REPLACE TEMP VIEW rm_batch06_targets
  (reviewed_name, candidate_id, expected_author_id, expected_orcid, expected_evidence_hash)
AS VALUES
  ('Butros Dahu',
   '206df6e52a31a386f322fdf1b9c631f516a49dcd97cfaf9fb502973854d015c4',
   'https://openalex.org/A5027453912', 'https://orcid.org/0000-0001-5880-2450',
   '3a0770b63b65bbfc87c987d49977f0337a2e53e1f3d0534287dbf3ddae323cc5'),
  ('Feras Al Obeidat',
   '75b48837e5b58d1802108b87f2fce0b22f9e72c0df81d5a93e598e17c5ef205c',
   'https://openalex.org/A5037131693', 'https://orcid.org/0000-0001-6941-6555',
   'da7db26b9064a3ba5f07d90dd3a8aa69de3dee89498c17ab4ab35feb9978653e'),
  ('Mahmoud Agha',
   '16bb4ef9867034e0764d672f0901f77ac35d8c94f7a3d6f745494e68ca88cea9',
   'https://openalex.org/A5074445320', 'https://orcid.org/0000-0002-4237-0056',
   '1ba877a88c8a6550d413323a66574b71c00370224f28687d16ae83a3e10f4325'),
  ('Moo Sung Kim',
   '90aa6db170e7c6dfec972f56be0bf5ec1fd09962c26a82d87cbbdbe5d6f0ad83',
   'https://openalex.org/A5063716848', 'https://orcid.org/0000-0002-5073-9984',
   'f946ae9a9c3d373d28cc02ff588058ff55236216f49494f1022d31b95a21c7b5');

-- COMMAND ----------
-- Reviewed exception: these two profiles link to orcid.org without a valid ORCID
-- (Silver flag unusable_profile_orcid). That flag alone is accepted for them only.
CREATE OR REPLACE TEMP VIEW rm_batch06_flag_exceptions (candidate_id) AS VALUES
  ('206df6e52a31a386f322fdf1b9c631f516a49dcd97cfaf9fb502973854d015c4'),
  ('75b48837e5b58d1802108b87f2fce0b22f9e72c0df81d5a93e598e17c5ef205c');

-- COMMAND ----------
-- Consult decisions directly: *_current status stays unchanged until a rebuild.
CREATE OR REPLACE TEMP VIEW rm_batch06_latest_decisions AS
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
CREATE OR REPLACE TEMP VIEW rm_batch06_checks AS
SELECT t.*, c.researcher_id, c.status, r.profile_url,
       COALESCE(
         c.openalex_author_id = t.expected_author_id
         AND c.evidence_hash = t.expected_evidence_hash
         AND c.match_method IN ('exact_normalized_name', 'name_tokens')
         AND c.status IN ('pending', 'verified')
         AND size(c.quality_flags) = 0
         AND (size(r.quality_flags) = 0
              OR (size(r.quality_flags) = 1
                  AND array_contains(r.quality_flags, 'unusable_profile_orcid')
                  AND t.candidate_id IN (SELECT candidate_id FROM rm_batch06_flag_exceptions)))
         AND size(r.profile_orcids) = 0
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
FROM rm_batch06_targets t
LEFT JOIN entity_resolution_review_current c ON c.candidate_id = t.candidate_id
LEFT JOIN researchers_current r ON r.researcher_id = c.researcher_id
LEFT JOIN authors_current a ON a.openalex_author_id = c.openalex_author_id
LEFT JOIN rm_batch06_latest_decisions d ON d.candidate_id = t.candidate_id;

-- COMMAND ----------
-- Expect exactly four rows, all five safety columns true. already_recorded is
-- false on the first run, true after approval (even before rebuilding Silver).
SELECT reviewed_name, profile_url, expected_author_id, expected_orcid, status,
       evidence_matches, no_cross_profile_ambiguity, no_split_orcid,
       no_work_orcid_conflict, decision_allows_accept, already_recorded
FROM rm_batch06_checks
ORDER BY reviewed_name;

-- COMMAND ----------
-- Counts are collected work records, not necessarily distinct research studies.
SELECT t.reviewed_name,
       COUNT(DISTINCT w.work_id) AS collected_work_records,
       COUNT(DISTINCT CASE WHEN w.eligible_for_expertise THEN w.work_id END)
         AS potentially_eligible_work_records,
       COUNT(DISTINCT CASE WHEN w.is_retracted THEN w.work_id END) AS retracted_records
FROM rm_batch06_targets t
LEFT JOIN work_authorships_current wa ON wa.openalex_author_id = t.expected_author_id
LEFT JOIN works_current w ON w.work_id = wa.work_id
GROUP BY t.reviewed_name
ORDER BY t.reviewed_name;

-- COMMAND ----------
-- Publication evidence for review. This deliberately does not require a ZU
-- affiliation: earlier career work belongs in a verified researcher's profile.
SELECT DISTINCT t.reviewed_name, w.work_id, w.title, w.doi, w.publication_year,
       wa.author_orcid, wa.institution_ids, w.eligible_for_expertise, w.quality_flags
FROM rm_batch06_targets t
JOIN work_authorships_current wa ON wa.openalex_author_id = t.expected_author_id
JOIN works_current w ON w.work_id = wa.work_id
ORDER BY t.reviewed_name, w.publication_year DESC, w.work_id;

-- COMMAND ----------
-- The ENTIRE batch is blocked if any check fails or a join duplicates a target.
CREATE OR REPLACE TEMP VIEW rm_batch06_gate AS
SELECT COUNT(*) = 4 AND COUNT(DISTINCT candidate_id) = 4
       AND SUM(CASE WHEN evidence_matches AND no_cross_profile_ambiguity
                          AND no_split_orcid AND no_work_orcid_conflict
                          AND decision_allows_accept THEN 1 ELSE 0 END) = 4
       AS all_checks_pass
FROM rm_batch06_checks;

SELECT p.approve_reviewed_batch, g.all_checks_pass,
       CASE WHEN p.approve_reviewed_batch AND g.all_checks_pass
            THEN 'Enabled: only previously unrecorded accepts will be inserted'
            ELSE 'Disabled: no decisions will be inserted' END AS action
FROM rm_batch06_permission p CROSS JOIN rm_batch06_gate g;

-- COMMAND ----------
-- ONLY persistent write in this notebook. Default permission=false inserts zero.
-- Explicit column names; exact whitelist; no updates/deletes or blanket approvals.
INSERT INTO identity_decisions
  (decision_id, candidate_id, evidence_hash, decision, rationale, reviewed_by, reviewed_at)
SELECT uuid(), c.candidate_id, c.expected_evidence_hash, 'accept',
       'Approved batch 06 after reviewing docs/identity_review_batch_06.md and current SQL evidence: ORCID registry record with current ZU employment matches OpenAlex; publication evidence checked.',
       current_user(), current_timestamp()
FROM rm_batch06_checks c
CROSS JOIN rm_batch06_permission p
CROSS JOIN rm_batch06_gate g
WHERE p.approve_reviewed_batch AND g.all_checks_pass AND NOT c.already_recorded;

-- COMMAND ----------
-- Expect zero records in preview mode; four after the first deliberate approval.
-- Re-running does not add duplicate accepts. Any rejection/stale decision blocks
-- this batch and must be reviewed separately, not overwritten here.
SELECT t.reviewed_name, d.decision, d.evidence_hash, d.reviewed_by, d.reviewed_at
FROM rm_batch06_targets t
JOIN identity_decisions d ON d.candidate_id = t.candidate_id
ORDER BY t.reviewed_name, d.reviewed_at DESC;

-- Restore approve_reviewed_batch=false. Then Run All in notebook 05 with
-- mode=validate; inspect counts, then mode=build. Run SQL 06 again afterwards.
