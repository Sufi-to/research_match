-- Databricks notebook source
-- Eleven reviewed author records (split ORCIDs) ONLY. Default Run All records no decisions.
-- First read docs/identity_review_batch_04.md and inspect the preview below.
-- Do not run concurrently with another reviewer, a Silver build, or source refresh.
USE CATALOG workspace;
USE SCHEMA researchmatch_silver;

-- COMMAND ----------
-- Keep false for the first run. Change to true ONLY if you approve all eleven
-- author records after review. Restore false immediately after recording decisions.
CREATE OR REPLACE TEMP VIEW rm_batch04_permission AS
SELECT false AS approve_reviewed_batch;

-- COMMAND ----------
-- Pinned to the evidence reviewed on 2026-10-28. Never replace these hashes with
-- new hashes just to pass a check: changed evidence requires another review.
-- Each person's ZU profile ORCID appears on several OpenAlex author records; every
-- record carrying that ORCID is listed (Ahed Abugabah's main record is already verified).
CREATE OR REPLACE TEMP VIEW rm_batch04_targets
  (reviewed_name, candidate_id, expected_author_id, expected_orcid, expected_evidence_hash)
AS VALUES
  ('Ahed Abugabah',
   'b5557733b81846c7e9dd5536f3556159e5ea67ec25c8e6fa3f19bfad8094e265',
   'https://openalex.org/A5136766665', 'https://orcid.org/0000-0002-3181-5822',
   '0cb554a0882f754c518d3b9be121013e209e2ea9403ee647d3f4c2ec2d858982'),
  ('Ahed Abugabah',
   '7df85907bd25e6a5587dbb18dd6b3724c925f33c97d1df7544281f88495fd754',
   'https://openalex.org/A5135111126', 'https://orcid.org/0000-0002-3181-5822',
   '67e38a2a055de2ef0875b48d675555781bed95b469b87ae1323f0c5940ca3c18'),
  ('Ahmed Seffah',
   '49beba3499cd7b5896a49ee6ef30df24b093a2b720a3d84e9079567a2abdac14',
   'https://openalex.org/A5073929162', 'https://orcid.org/0000-0001-7166-8567',
   'a8878213606409ee882a7d2c93a4e70b1be5bf461d90efcf7bc2e74e90218d6f'),
  ('Ahmed Seffah',
   'bca793bd62c9e44f30d15e6f11cf2a4872cfba0f07e8ecbbd248afe850c74c4c',
   'https://openalex.org/A5144282536', 'https://orcid.org/0000-0001-7166-8567',
   '02067860daeed14f0ad9a4f0502995002066b2b5b6f6b55707a7fffbbf8ee319'),
  ('Ahmed Seffah',
   '5b332149bc72424f841ab50ca7411035e8b67a516bdf4ec4f4094adfc34a1267',
   'https://openalex.org/A5137791213', 'https://orcid.org/0000-0001-7166-8567',
   '5d41d5466c406641ed8d6f90ddf98cf97c97c4c3b3cbc4b9494d0c8b8e03f25a'),
  ('Belal AlSinglawi',
   'bde5628631e61a11f04c569d81fe91efb0d2cbd1aa264701e83509849d3359b5',
   'https://openalex.org/A5018299454', 'https://orcid.org/0000-0003-0316-3641',
   '50e501c567127990fcb214f023e5f1831d735470817bb18c4df90d7fd2b85d12'),
  ('Belal AlSinglawi',
   '55519e8b455c696c00ecc15827df5275ff63c7018d1ecfff92fc077e31f17748',
   'https://openalex.org/A5135604592', 'https://orcid.org/0000-0003-0316-3641',
   '4352f71e64e88cd81d3745d5346bf300a41cb248fae31ef6aebda66ed16aac7a'),
  ('Edmund Evangelista',
   'dade5a8dee0b453282ae2fe09fdac4c6d0d54cccd41e3db81bee9f4da82ce536',
   'https://openalex.org/A5003856994', 'https://orcid.org/0000-0002-8050-1360',
   'd89c1ce426f42f132c0ee697a6dc2efe88544e7027df6410b6a10ef2debcbed7'),
  ('Edmund Evangelista',
   '37dd11ca16952a43ca10b01d4aeaa39a7bfccab62e50d15055d536a53a3afe04',
   'https://openalex.org/A5136695590', 'https://orcid.org/0000-0002-8050-1360',
   'c430d1298473480356bdd02eedc71be716f0233e88d2f1c788c7d5ed99e6d441'),
  ('Farkhund Iqbal',
   '4f10f74e5bfa09bf5a7a2a815297ec1f9006d2b502eec940c7de1e62c9c633cd',
   'https://openalex.org/A5068447696', 'https://orcid.org/0000-0001-9081-3598',
   '4a421cf3256855d32e42bd2e16114283ea94d629121e9d1f7ade4ee1bc77bb01'),
  ('Farkhund Iqbal',
   'fb7db0a96dfb7065afdaf8d60685a763a72968dee15f4060ce6a7a8901bce5e4',
   'https://openalex.org/A5135285921', 'https://orcid.org/0000-0001-9081-3598',
   'b6d549b0a62a9f1bf18ad72d4fd3dbcc853d81195f3de734783f8fe8b72c25cd');

-- COMMAND ----------
-- Consult decisions directly: *_current status stays unchanged until a rebuild.
CREATE OR REPLACE TEMP VIEW rm_batch04_latest_decisions AS
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
-- Differences from batch 03: the only allowed candidate flag is the expected
-- shared-ORCID flag, and instead of "no split ORCID" every author record with the
-- ORCID must be in this batch or already verified for the SAME researcher.
CREATE OR REPLACE TEMP VIEW rm_batch04_checks AS
SELECT t.*, c.researcher_id, c.status, r.profile_url,
       COALESCE(
         c.openalex_author_id = t.expected_author_id
         AND c.evidence_hash = t.expected_evidence_hash
         AND c.match_method = 'profile_orcid'
         AND c.status IN ('pending', 'verified')
         AND size(c.quality_flags) = 1
         AND array_contains(c.quality_flags, 'shared_orcid_multiple_author_ids')
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
           AND other.openalex_author_id NOT IN (SELECT expected_author_id FROM rm_batch04_targets)
           AND NOT EXISTS (
             SELECT 1 FROM entity_resolution_review_current v
             WHERE v.openalex_author_id = other.openalex_author_id
               AND v.researcher_id = c.researcher_id AND v.status = 'verified')
       ) AND NOT EXISTS (
         SELECT 1 FROM rm_batch04_targets t2
         JOIN entity_resolution_review_current c2 ON c2.candidate_id = t2.candidate_id
         WHERE t2.expected_orcid = t.expected_orcid AND c2.researcher_id <> c.researcher_id
       ) AS all_orcid_records_reviewed,
       NOT EXISTS (
         SELECT 1 FROM work_authorships_current wa
         WHERE wa.openalex_author_id = t.expected_author_id
           AND wa.author_orcid IS NOT NULL AND wa.author_orcid <> t.expected_orcid
       ) AS no_work_orcid_conflict,
       (d.decision_id IS NULL OR
         (d.decision = 'accept' AND d.evidence_hash = t.expected_evidence_hash)
       ) AS decision_allows_accept,
       d.decision_id IS NOT NULL AS already_recorded
FROM rm_batch04_targets t
LEFT JOIN entity_resolution_review_current c ON c.candidate_id = t.candidate_id
LEFT JOIN researchers_current r ON r.researcher_id = c.researcher_id
LEFT JOIN authors_current a ON a.openalex_author_id = c.openalex_author_id
LEFT JOIN rm_batch04_latest_decisions d ON d.candidate_id = t.candidate_id;

-- COMMAND ----------
-- Expect exactly eleven rows, all five safety columns true. already_recorded is
-- false on the first run, true after approval (even before rebuilding Silver).
SELECT reviewed_name, profile_url, expected_author_id, expected_orcid, status,
       evidence_matches, no_cross_profile_ambiguity, all_orcid_records_reviewed,
       no_work_orcid_conflict, decision_allows_accept, already_recorded
FROM rm_batch04_checks
ORDER BY reviewed_name, expected_author_id;

-- COMMAND ----------
-- Counts are collected work records, not necessarily distinct research studies.
SELECT t.reviewed_name, t.expected_author_id,
       COUNT(DISTINCT w.work_id) AS collected_work_records,
       COUNT(DISTINCT CASE WHEN w.eligible_for_expertise THEN w.work_id END)
         AS potentially_eligible_work_records,
       COUNT(DISTINCT CASE WHEN w.is_retracted THEN w.work_id END) AS retracted_records
FROM rm_batch04_targets t
LEFT JOIN work_authorships_current wa ON wa.openalex_author_id = t.expected_author_id
LEFT JOIN works_current w ON w.work_id = wa.work_id
GROUP BY t.reviewed_name, t.expected_author_id
ORDER BY t.reviewed_name, t.expected_author_id;

-- COMMAND ----------
-- Publication evidence for review. This deliberately does not require a ZU
-- affiliation: earlier career work belongs in a verified researcher's profile.
SELECT DISTINCT t.reviewed_name, t.expected_author_id, w.work_id, w.title, w.doi,
       w.publication_year, wa.author_orcid, wa.institution_ids, w.eligible_for_expertise,
       w.quality_flags
FROM rm_batch04_targets t
JOIN work_authorships_current wa ON wa.openalex_author_id = t.expected_author_id
JOIN works_current w ON w.work_id = wa.work_id
ORDER BY t.reviewed_name, w.publication_year DESC, w.work_id;

-- COMMAND ----------
-- The ENTIRE batch is blocked if any check fails or a join duplicates a target.
CREATE OR REPLACE TEMP VIEW rm_batch04_gate AS
SELECT COUNT(*) = 11 AND COUNT(DISTINCT candidate_id) = 11
       AND SUM(CASE WHEN evidence_matches AND no_cross_profile_ambiguity
                          AND all_orcid_records_reviewed AND no_work_orcid_conflict
                          AND decision_allows_accept THEN 1 ELSE 0 END) = 11
       AS all_checks_pass
FROM rm_batch04_checks;

SELECT p.approve_reviewed_batch, g.all_checks_pass,
       CASE WHEN p.approve_reviewed_batch AND g.all_checks_pass
            THEN 'Enabled: only previously unrecorded accepts will be inserted'
            ELSE 'Disabled: no decisions will be inserted' END AS action
FROM rm_batch04_permission p CROSS JOIN rm_batch04_gate g;

-- COMMAND ----------
-- ONLY persistent write in this notebook. Default permission=false inserts zero.
-- Explicit column names; exact whitelist; no updates/deletes or blanket approvals.
INSERT INTO identity_decisions
  (decision_id, candidate_id, evidence_hash, decision, rationale, reviewed_by, reviewed_at)
SELECT uuid(), c.candidate_id, c.expected_evidence_hash, 'accept',
       'Approved batch 04 after reviewing docs/identity_review_batch_04.md and current SQL evidence: every OpenAlex record carrying the ZU profile-linked ORCID accepted together; publication evidence checked.',
       current_user(), current_timestamp()
FROM rm_batch04_checks c
CROSS JOIN rm_batch04_permission p
CROSS JOIN rm_batch04_gate g
WHERE p.approve_reviewed_batch AND g.all_checks_pass AND NOT c.already_recorded;

-- COMMAND ----------
-- Expect zero records in preview mode; eleven after the first deliberate approval.
-- Re-running does not add duplicate accepts. Any rejection/stale decision blocks
-- this batch and must be reviewed separately, not overwritten here.
SELECT t.reviewed_name, t.expected_author_id, d.decision, d.evidence_hash, d.reviewed_by,
       d.reviewed_at
FROM rm_batch04_targets t
JOIN identity_decisions d ON d.candidate_id = t.candidate_id
ORDER BY t.reviewed_name, t.expected_author_id, d.reviewed_at DESC;

-- Restore approve_reviewed_batch=false. Then Run All in notebook 05 with
-- mode=validate; inspect counts, then mode=build. Run SQL 06 again afterwards.
