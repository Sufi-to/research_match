-- Databricks notebook source
-- Six reviewed identity candidates ONLY. Default Run All records no decisions.
-- First read docs/identity_review_batch_03.md and inspect the preview below.
-- Do not run concurrently with another reviewer, a Silver build, or source refresh.
USE CATALOG workspace;
USE SCHEMA researchmatch_silver;

-- COMMAND ----------
-- Keep false for the first run. Change to true ONLY if you approve all six
-- identities after review. Restore false immediately after recording decisions.
CREATE OR REPLACE TEMP VIEW rm_batch03_permission AS
SELECT false AS approve_reviewed_batch;

-- COMMAND ----------
-- Pinned to the evidence reviewed on 2026-10-27. Never replace these hashes with
-- new hashes just to pass a check: changed evidence requires another review.
CREATE OR REPLACE TEMP VIEW rm_batch03_targets
  (reviewed_name, candidate_id, expected_author_id, expected_orcid, expected_evidence_hash)
AS VALUES
  ('Ahmad Wazan',
   'c2c44d5df6c3b972789dce895e06c75f6e9b740ba040b8c4f701159086e3b7e3',
   'https://openalex.org/A5106467067', 'https://orcid.org/0000-0003-1385-4462',
   'ab4c7ed176651367e19cb14038a6e42e85aa0ecdc33f6f921019ef45bbb7b8dc'),
  ('Alena Connolly',
   'a4790a06bd23d2468b1112acc8ddfb882b3dd59f8873126afc5f943e3daec81e',
   'https://openalex.org/A5036801930', 'https://orcid.org/0000-0002-7110-9594',
   '35714d1443f318e508462cce94d863457dbc8399e0e0046fe23c372e98b57974'),
  ('Asad Khattak',
   'f5d90f3d4fb15d6b04c131d41fe2d262c898c9f7608ef57e66936d6f5bc47584',
   'https://openalex.org/A5063432011', 'https://orcid.org/0000-0002-0630-1264',
   '4d14024a36289be1cd8ed48f2393b6e804bbc066cb6e48e713e363c0c26bbf2a'),
  ('Babar Shah',
   'd46be5b5f362f55c12ec330f3f2a29638ddfd624b75979b2207c99c09f5ebbc3',
   'https://openalex.org/A5065504551', 'https://orcid.org/0000-0002-5090-4695',
   '1bf55fb4f96828cdb8033b2bff4a0d4fde05673903d48501ecc8763857331995'),
  ('Basel Almourad',
   'cedd8ecdd404e98179ae1c6cc277196a1affdd35a6d941e2046e53a94792cd65',
   'https://openalex.org/A5038578892', 'https://orcid.org/0000-0003-2193-1472',
   'ff46e37ba201133689e317051abda96fd741ad6b5779fa6213452a01132ccd68'),
  ('Fatma Taher',
   '2df1536e893a7a7a615408b031f5d61d42aa002a1ff5d2e5c9bee9ea434839c5',
   'https://openalex.org/A5015357458', 'https://orcid.org/0000-0001-8358-9081',
   '2bc5bd79e72f30c108e68f48375a8c09bbd93bf3c6884b39472af26801172172');

-- COMMAND ----------
-- Consult decisions directly: *_current status stays unchanged until a rebuild.
CREATE OR REPLACE TEMP VIEW rm_batch03_latest_decisions AS
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
CREATE OR REPLACE TEMP VIEW rm_batch03_checks AS
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
FROM rm_batch03_targets t
LEFT JOIN entity_resolution_review_current c ON c.candidate_id = t.candidate_id
LEFT JOIN researchers_current r ON r.researcher_id = c.researcher_id
LEFT JOIN authors_current a ON a.openalex_author_id = c.openalex_author_id
LEFT JOIN rm_batch03_latest_decisions d ON d.candidate_id = t.candidate_id;

-- COMMAND ----------
-- Expect exactly six rows, all five safety columns true. already_recorded is
-- false on the first run, true after approval (even before rebuilding Silver).
SELECT reviewed_name, profile_url, expected_author_id, expected_orcid, status,
       evidence_matches, no_cross_profile_ambiguity, no_split_orcid,
       no_work_orcid_conflict, decision_allows_accept, already_recorded
FROM rm_batch03_checks
ORDER BY reviewed_name;

-- COMMAND ----------
-- Counts are collected work records, not necessarily distinct research studies.
SELECT t.reviewed_name,
       COUNT(DISTINCT w.work_id) AS collected_work_records,
       COUNT(DISTINCT CASE WHEN w.eligible_for_expertise THEN w.work_id END)
         AS potentially_eligible_work_records,
       COUNT(DISTINCT CASE WHEN w.is_retracted THEN w.work_id END) AS retracted_records
FROM rm_batch03_targets t
LEFT JOIN work_authorships_current wa ON wa.openalex_author_id = t.expected_author_id
LEFT JOIN works_current w ON w.work_id = wa.work_id
GROUP BY t.reviewed_name
ORDER BY t.reviewed_name;

-- COMMAND ----------
-- Publication evidence for review. This deliberately does not require a ZU
-- affiliation: earlier career work belongs in a verified researcher's profile.
SELECT DISTINCT t.reviewed_name, w.work_id, w.title, w.doi, w.publication_year,
       wa.author_orcid, wa.institution_ids, w.eligible_for_expertise, w.quality_flags
FROM rm_batch03_targets t
JOIN work_authorships_current wa ON wa.openalex_author_id = t.expected_author_id
JOIN works_current w ON w.work_id = wa.work_id
ORDER BY t.reviewed_name, w.publication_year DESC, w.work_id;

-- COMMAND ----------
-- The ENTIRE batch is blocked if any check fails or a join duplicates a target.
CREATE OR REPLACE TEMP VIEW rm_batch03_gate AS
SELECT COUNT(*) = 6 AND COUNT(DISTINCT candidate_id) = 6
       AND SUM(CASE WHEN evidence_matches AND no_cross_profile_ambiguity
                          AND no_split_orcid AND no_work_orcid_conflict
                          AND decision_allows_accept THEN 1 ELSE 0 END) = 6
       AS all_checks_pass
FROM rm_batch03_checks;

SELECT p.approve_reviewed_batch, g.all_checks_pass,
       CASE WHEN p.approve_reviewed_batch AND g.all_checks_pass
            THEN 'Enabled: only previously unrecorded accepts will be inserted'
            ELSE 'Disabled: no decisions will be inserted' END AS action
FROM rm_batch03_permission p CROSS JOIN rm_batch03_gate g;

-- COMMAND ----------
-- ONLY persistent write in this notebook. Default permission=false inserts zero.
-- Explicit column names; exact whitelist; no updates/deletes or blanket approvals.
INSERT INTO identity_decisions
  (decision_id, candidate_id, evidence_hash, decision, rationale, reviewed_by, reviewed_at)
SELECT uuid(), c.candidate_id, c.expected_evidence_hash, 'accept',
       'Approved batch 03 after reviewing docs/identity_review_batch_03.md and current SQL evidence: ZU profile-linked ORCID matches OpenAlex; publication evidence checked.',
       current_user(), current_timestamp()
FROM rm_batch03_checks c
CROSS JOIN rm_batch03_permission p
CROSS JOIN rm_batch03_gate g
WHERE p.approve_reviewed_batch AND g.all_checks_pass AND NOT c.already_recorded;

-- COMMAND ----------
-- Expect zero records in preview mode; six after the first deliberate approval.
-- Re-running does not add duplicate accepts. Any rejection/stale decision blocks
-- this batch and must be reviewed separately, not overwritten here.
SELECT t.reviewed_name, d.decision, d.evidence_hash, d.reviewed_by, d.reviewed_at
FROM rm_batch03_targets t
JOIN identity_decisions d ON d.candidate_id = t.candidate_id
ORDER BY t.reviewed_name, d.reviewed_at DESC;

-- Restore approve_reviewed_batch=false. Then Run All in notebook 05 with
-- mode=validate; inspect counts, then mode=build. Run SQL 06 again afterwards.
