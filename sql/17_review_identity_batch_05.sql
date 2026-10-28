-- Databricks notebook source
-- Ten reviewed Business identity candidates ONLY (ORCID registry evidence). Default Run All records no decisions.
-- First read docs/identity_review_batch_05.md and inspect the preview below.
-- Do not run concurrently with another reviewer, a Silver build, or source refresh.
USE CATALOG workspace;
USE SCHEMA researchmatch_silver;

-- COMMAND ----------
-- Keep false for the first run. Change to true ONLY if you approve all ten
-- identities after review. Restore false immediately after recording decisions.
CREATE OR REPLACE TEMP VIEW rm_batch05_permission AS
SELECT false AS approve_reviewed_batch;

-- COMMAND ----------
-- Pinned to the evidence reviewed on 2026-10-28. Never replace these hashes with
-- new hashes just to pass a check: changed evidence requires another review.
-- These ZU profiles list no ORCID. expected_orcid comes from the public ORCID
-- registry: the only record found for the name with a current Zayed University
-- employment entry (responses summarised in the review doc). The OpenAlex author
-- must carry exactly that ORCID, so a name match alone can never pass.
CREATE OR REPLACE TEMP VIEW rm_batch05_targets
  (reviewed_name, candidate_id, expected_author_id, expected_orcid, expected_evidence_hash)
AS VALUES
  ('Ahmed Abdel-Maksoud',
   'bc5f452961903704050e79a971817e9d15ab57f812b0d15e84637b82300b9131',
   'https://openalex.org/A5068747618', 'https://orcid.org/0000-0002-6932-8501',
   '9978be531a5a92481b7ffe77479cf67c0cbe334c306eb13f505f97bd1c1aa13a'),
  ('Alexandra Henderson',
   'dc5d17c6c898dbd2c480e9bc4484eff3623f6ee1e35b6c1ed3c5048a7aa70feb',
   'https://openalex.org/A5015822964', 'https://orcid.org/0000-0002-3361-168X',
   'ba10c05a5306eca2d5adc00a535a2a638052ac9d4721d7d9ce041aeb2b864646'),
  ('Anestis Fotiadis',
   '0919c2fb015ad69f0f92d3e5e00d2af73b2db866378a2f3b283d4fdf02e886a2',
   'https://openalex.org/A5057670674', 'https://orcid.org/0000-0001-8806-7987',
   '12fb9ee4a2216bcdc48ea499d12198eb56b0a89f035bdc0ac1a9c5aa43566f9c'),
  ('Brendan Galbraith',
   '48eb40901bf7feaf943b06bab97b728e5b5b031e7266664164a0675d9121c51f',
   'https://openalex.org/A5030826659', 'https://orcid.org/0000-0001-9416-5540',
   'ecdf3e18061775a7e8720f06df53e82be5e6b948a9249ddae400f18361d0a458'),
  ('Fathima Saleem',
   '98cf638ab41f139331b0634ce30dff08d8c9c9c27c20b02053d67b58438fe525',
   'https://openalex.org/A5005961739', 'https://orcid.org/0000-0001-5456-1442',
   '0384ac4c052af9de85039c9cbb8e1906e06d8a499eaa8b41b268eeac2da0e68f'),
  ('Ismail Badraoui',
   '5b9d4bce786ad08cc9940c2658cec36be41f12e17ec3f436f5b64782828f03b6',
   'https://openalex.org/A5091232321', 'https://orcid.org/0000-0002-6332-8383',
   '73608688841b30037c7e672e6e941ede1487927d810f498ae8cf7e76b4b23c6a'),
  ('Jamel Khenfer',
   '33a35ccfefb178082a096eb6ad6c4461e0956106b25ba172e8e43a975c1821f1',
   'https://openalex.org/A5056804412', 'https://orcid.org/0000-0002-0489-1272',
   'f5c5fc684d63228ad9641e01140b09e481979b7999f9b30a7cd2f06e2c3079f7'),
  ('Karim Hegazy',
   '235edf9763b3b769a99fdeb76cce68f60bcbf530e1deec20b702e01969f806ea',
   'https://openalex.org/A5026444896', 'https://orcid.org/0000-0003-3576-3238',
   '7d4b429be8005277f24615fac3bf29f82999c328f161760bb6ef166c8f725f55'),
  ('Khadija Abdulrahman',
   '397a192bcd00cddff29dffd58ea4634c7665664f4b9a4f75c075efb473679d72',
   'https://openalex.org/A5064532212', 'https://orcid.org/0000-0001-7875-8709',
   'ccf434515d1f1d14e9a0f95d1b64c227524f8e456a94bde8415fa0d99b6beefb'),
  ('Mariem Khalifa',
   '7546538972f8ae0e87921d0dcf48fe193773d57439df3e03b291b3646b01b64f',
   'https://openalex.org/A5057231192', 'https://orcid.org/0000-0003-1751-0579',
   '2237580dbef449a13b7bd6c42b581a538bc59049ea20359b89c31b7ddd4a2e6c');

-- COMMAND ----------
-- Consult decisions directly: *_current status stays unchanged until a rebuild.
CREATE OR REPLACE TEMP VIEW rm_batch05_latest_decisions AS
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
CREATE OR REPLACE TEMP VIEW rm_batch05_checks AS
SELECT t.*, c.researcher_id, c.status, r.profile_url,
       COALESCE(
         c.openalex_author_id = t.expected_author_id
         AND c.evidence_hash = t.expected_evidence_hash
         AND c.match_method IN ('exact_normalized_name', 'name_tokens')
         AND c.status IN ('pending', 'verified')
         AND size(c.quality_flags) = 0
         AND size(r.quality_flags) = 0
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
FROM rm_batch05_targets t
LEFT JOIN entity_resolution_review_current c ON c.candidate_id = t.candidate_id
LEFT JOIN researchers_current r ON r.researcher_id = c.researcher_id
LEFT JOIN authors_current a ON a.openalex_author_id = c.openalex_author_id
LEFT JOIN rm_batch05_latest_decisions d ON d.candidate_id = t.candidate_id;

-- COMMAND ----------
-- Expect exactly ten rows, all five safety columns true. already_recorded is
-- false on the first run, true after approval (even before rebuilding Silver).
SELECT reviewed_name, profile_url, expected_author_id, expected_orcid, status,
       evidence_matches, no_cross_profile_ambiguity, no_split_orcid,
       no_work_orcid_conflict, decision_allows_accept, already_recorded
FROM rm_batch05_checks
ORDER BY reviewed_name;

-- COMMAND ----------
-- Counts are collected work records, not necessarily distinct research studies.
SELECT t.reviewed_name,
       COUNT(DISTINCT w.work_id) AS collected_work_records,
       COUNT(DISTINCT CASE WHEN w.eligible_for_expertise THEN w.work_id END)
         AS potentially_eligible_work_records,
       COUNT(DISTINCT CASE WHEN w.is_retracted THEN w.work_id END) AS retracted_records
FROM rm_batch05_targets t
LEFT JOIN work_authorships_current wa ON wa.openalex_author_id = t.expected_author_id
LEFT JOIN works_current w ON w.work_id = wa.work_id
GROUP BY t.reviewed_name
ORDER BY t.reviewed_name;

-- COMMAND ----------
-- Publication evidence for review. This deliberately does not require a ZU
-- affiliation: earlier career work belongs in a verified researcher's profile.
SELECT DISTINCT t.reviewed_name, w.work_id, w.title, w.doi, w.publication_year,
       wa.author_orcid, wa.institution_ids, w.eligible_for_expertise, w.quality_flags
FROM rm_batch05_targets t
JOIN work_authorships_current wa ON wa.openalex_author_id = t.expected_author_id
JOIN works_current w ON w.work_id = wa.work_id
ORDER BY t.reviewed_name, w.publication_year DESC, w.work_id;

-- COMMAND ----------
-- The ENTIRE batch is blocked if any check fails or a join duplicates a target.
CREATE OR REPLACE TEMP VIEW rm_batch05_gate AS
SELECT COUNT(*) = 10 AND COUNT(DISTINCT candidate_id) = 10
       AND SUM(CASE WHEN evidence_matches AND no_cross_profile_ambiguity
                          AND no_split_orcid AND no_work_orcid_conflict
                          AND decision_allows_accept THEN 1 ELSE 0 END) = 10
       AS all_checks_pass
FROM rm_batch05_checks;

SELECT p.approve_reviewed_batch, g.all_checks_pass,
       CASE WHEN p.approve_reviewed_batch AND g.all_checks_pass
            THEN 'Enabled: only previously unrecorded accepts will be inserted'
            ELSE 'Disabled: no decisions will be inserted' END AS action
FROM rm_batch05_permission p CROSS JOIN rm_batch05_gate g;

-- COMMAND ----------
-- ONLY persistent write in this notebook. Default permission=false inserts zero.
-- Explicit column names; exact whitelist; no updates/deletes or blanket approvals.
INSERT INTO identity_decisions
  (decision_id, candidate_id, evidence_hash, decision, rationale, reviewed_by, reviewed_at)
SELECT uuid(), c.candidate_id, c.expected_evidence_hash, 'accept',
       'Approved batch 05 after reviewing docs/identity_review_batch_05.md and current SQL evidence: ORCID registry record with current ZU employment matches OpenAlex; publication evidence checked.',
       current_user(), current_timestamp()
FROM rm_batch05_checks c
CROSS JOIN rm_batch05_permission p
CROSS JOIN rm_batch05_gate g
WHERE p.approve_reviewed_batch AND g.all_checks_pass AND NOT c.already_recorded;

-- COMMAND ----------
-- Expect zero records in preview mode; ten after the first deliberate approval.
-- Re-running does not add duplicate accepts. Any rejection/stale decision blocks
-- this batch and must be reviewed separately, not overwritten here.
SELECT t.reviewed_name, d.decision, d.evidence_hash, d.reviewed_by, d.reviewed_at
FROM rm_batch05_targets t
JOIN identity_decisions d ON d.candidate_id = t.candidate_id
ORDER BY t.reviewed_name, d.reviewed_at DESC;

-- Restore approve_reviewed_batch=false. Then Run All in notebook 05 with
-- mode=validate; inspect counts, then mode=build. Run SQL 06 again afterwards.
