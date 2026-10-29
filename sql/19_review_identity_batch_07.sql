-- Databricks notebook source
-- Five reviewed identity candidates ONLY (affiliation rule, weaker than ORCID employment). Default Run All records no decisions.
-- First read docs/identity_review_batch_07.md and inspect the preview below.
-- Do not run concurrently with another reviewer, a Silver build, or source refresh.
USE CATALOG workspace;
USE SCHEMA researchmatch_silver;

-- COMMAND ----------
-- Keep false for the first run. Change to true ONLY if you approve all five
-- identities after review. Restore false immediately after recording decisions.
CREATE OR REPLACE TEMP VIEW rm_batch07_permission AS
SELECT false AS approve_reviewed_batch;

-- COMMAND ----------
-- Pinned to the evidence reviewed on 2026-10-28. Never replace these hashes with
-- new hashes just to pass a check: changed evidence requires another review.
-- These ZU profiles list no ORCID and the ORCID records list no employment.
-- AFFILIATION RULE (user-approved 2026-10-29): expected_orcid is the ORCID carried by
-- the name-matched OpenAlex author; that author's last known institution must be ZU
-- and it must have at least 10 ZU-affiliated authorships. Weaker than ORCID employment.
CREATE OR REPLACE TEMP VIEW rm_batch07_targets
  (reviewed_name, candidate_id, expected_author_id, expected_orcid, expected_evidence_hash)
AS VALUES
  ('Abiot Tessema',
   '749e421dd2b164ddaecf056bcce5b966e1be9a664feb8b68bf38f94dd3aab753',
   'https://openalex.org/A5005423855', 'https://orcid.org/0000-0001-8423-8232',
   '76154271a3bdfb91690e37a1ef58500428dcea067084886744079e7b5563e56f'),
  ('Anwer Al-Dulaimi',
   '6701e112ca3c7111aa5b5ad05f6f5441d3fb5e87e702213f560443c0e37d5466',
   'https://openalex.org/A5020575793', 'https://orcid.org/0000-0001-9936-0038',
   '76801ddbb9b0115a5fa3d5f28c5b9f38eb92152ac8a52ccc6ba1fe9990bfe5d7'),
  ('Catherine Nickerson',
   '257363855f22071349dd900035ca5e54494ceb51d5f06a38edd983849130cba3',
   'https://openalex.org/A5040939492', 'https://orcid.org/0000-0002-3379-1465',
   '851741427643f09582d08b688d4f54326208a9908b066567fb3e0c246a0c5e11'),
  ('Maytha Al Ali',
   'd302254dbd54f97a687154f8f1c11dc713b3e38a5b55dc9ed87f6b283a5b4de7',
   'https://openalex.org/A5089919765', 'https://orcid.org/0000-0001-5606-1695',
   '64e9a2f3086937df7022d174f2b3a6fcdc1edee1c1110e2594fe5fe38e0aece2'),
  ('Mohammed Sualihu',
   '2b13cffc55a272e78dcfd0b35fc36e301f0d3a8844ce3a0e3aa0f6947dfb8705',
   'https://openalex.org/A5007637668', 'https://orcid.org/0000-0002-0766-4569',
   '829b842b85a7118c8e320b0c323b544a39d27fe327874bcdb955a274cea97743');

-- COMMAND ----------
-- Consult decisions directly: *_current status stays unchanged until a rebuild.
CREATE OR REPLACE TEMP VIEW rm_batch07_latest_decisions AS
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
CREATE OR REPLACE TEMP VIEW rm_batch07_checks AS
SELECT t.*, c.researcher_id, c.status, r.profile_url,
       COALESCE(
         c.openalex_author_id = t.expected_author_id
         AND c.evidence_hash = t.expected_evidence_hash
         AND c.match_method IN ('exact_normalized_name', 'name_tokens')
         AND c.status IN ('pending', 'verified')
         AND size(c.quality_flags) = 0
         AND size(r.quality_flags) = 0
         AND size(r.profile_orcids) = 0
         AND a.orcid = t.expected_orcid
         AND array_contains(a.last_known_institution_ids, 'https://openalex.org/I91044093')
         AND (SELECT COUNT(DISTINCT wa.work_id) FROM work_authorships_current wa
              WHERE wa.openalex_author_id = t.expected_author_id
                AND array_contains(wa.institution_ids, 'https://openalex.org/I91044093')) >= 10, false
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
FROM rm_batch07_targets t
LEFT JOIN entity_resolution_review_current c ON c.candidate_id = t.candidate_id
LEFT JOIN researchers_current r ON r.researcher_id = c.researcher_id
LEFT JOIN authors_current a ON a.openalex_author_id = c.openalex_author_id
LEFT JOIN rm_batch07_latest_decisions d ON d.candidate_id = t.candidate_id;

-- COMMAND ----------
-- Expect exactly five rows, all five safety columns true. already_recorded is
-- false on the first run, true after approval (even before rebuilding Silver).
SELECT reviewed_name, profile_url, expected_author_id, expected_orcid, status,
       evidence_matches, no_cross_profile_ambiguity, no_split_orcid,
       no_work_orcid_conflict, decision_allows_accept, already_recorded
FROM rm_batch07_checks
ORDER BY reviewed_name;

-- COMMAND ----------
-- Counts are collected work records, not necessarily distinct research studies.
SELECT t.reviewed_name,
       COUNT(DISTINCT w.work_id) AS collected_work_records,
       COUNT(DISTINCT CASE WHEN w.eligible_for_expertise THEN w.work_id END)
         AS potentially_eligible_work_records,
       COUNT(DISTINCT CASE WHEN w.is_retracted THEN w.work_id END) AS retracted_records
FROM rm_batch07_targets t
LEFT JOIN work_authorships_current wa ON wa.openalex_author_id = t.expected_author_id
LEFT JOIN works_current w ON w.work_id = wa.work_id
GROUP BY t.reviewed_name
ORDER BY t.reviewed_name;

-- COMMAND ----------
-- Publication evidence for review. This deliberately does not require a ZU
-- affiliation: earlier career work belongs in a verified researcher's profile.
SELECT DISTINCT t.reviewed_name, w.work_id, w.title, w.doi, w.publication_year,
       wa.author_orcid, wa.institution_ids, w.eligible_for_expertise, w.quality_flags
FROM rm_batch07_targets t
JOIN work_authorships_current wa ON wa.openalex_author_id = t.expected_author_id
JOIN works_current w ON w.work_id = wa.work_id
ORDER BY t.reviewed_name, w.publication_year DESC, w.work_id;

-- COMMAND ----------
-- The ENTIRE batch is blocked if any check fails or a join duplicates a target.
CREATE OR REPLACE TEMP VIEW rm_batch07_gate AS
SELECT COUNT(*) = 5 AND COUNT(DISTINCT candidate_id) = 5
       AND SUM(CASE WHEN evidence_matches AND no_cross_profile_ambiguity
                          AND no_split_orcid AND no_work_orcid_conflict
                          AND decision_allows_accept THEN 1 ELSE 0 END) = 5
       AS all_checks_pass
FROM rm_batch07_checks;

SELECT p.approve_reviewed_batch, g.all_checks_pass,
       CASE WHEN p.approve_reviewed_batch AND g.all_checks_pass
            THEN 'Enabled: only previously unrecorded accepts will be inserted'
            ELSE 'Disabled: no decisions will be inserted' END AS action
FROM rm_batch07_permission p CROSS JOIN rm_batch07_gate g;

-- COMMAND ----------
-- ONLY persistent write in this notebook. Default permission=false inserts zero.
-- Explicit column names; exact whitelist; no updates/deletes or blanket approvals.
INSERT INTO identity_decisions
  (decision_id, candidate_id, evidence_hash, decision, rationale, reviewed_by, reviewed_at)
SELECT uuid(), c.candidate_id, c.expected_evidence_hash, 'accept',
       'Approved batch 07 after reviewing docs/identity_review_batch_07.md and current SQL evidence: name-matched OpenAlex author carries this ORCID, last known institution ZU, at least 10 ZU-affiliated authorships; publication evidence checked.',
       current_user(), current_timestamp()
FROM rm_batch07_checks c
CROSS JOIN rm_batch07_permission p
CROSS JOIN rm_batch07_gate g
WHERE p.approve_reviewed_batch AND g.all_checks_pass AND NOT c.already_recorded;

-- COMMAND ----------
-- Expect zero records in preview mode; five after the first deliberate approval.
-- Re-running does not add duplicate accepts. Any rejection/stale decision blocks
-- this batch and must be reviewed separately, not overwritten here.
SELECT t.reviewed_name, d.decision, d.evidence_hash, d.reviewed_by, d.reviewed_at
FROM rm_batch07_targets t
JOIN identity_decisions d ON d.candidate_id = t.candidate_id
ORDER BY t.reviewed_name, d.reviewed_at DESC;

-- Restore approve_reviewed_batch=false. Then Run All in notebook 05 with
-- mode=validate; inspect counts, then mode=build. Run SQL 06 again afterwards.
