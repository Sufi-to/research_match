-- Databricks notebook source
-- Read-only checks AFTER updated SQL 04 and notebook 05 validate/build.
USE CATALOG workspace;
USE SCHEMA researchmatch_silver;

-- COMMAND ----------
SELECT build_id, transform_version, completed_at
FROM current_build;

-- COMMAND ----------
SELECT concept_work_id, version_work_id, concept_doi, version_doi,
       relation_type, checked_on, source_urls, evidence_sha256
FROM work_version_relationships_current;

-- COMMAND ----------
-- Expect two source records, one contribution, version 18509542 representative.
SELECT w.work_id, w.doi, w.publication_type, w.eligible_for_expertise,
       w.quality_flags, c.contribution_id, c.representative_work_id,
       c.is_representative, c.verification_status
FROM works_current w
JOIN work_contributions_current c ON c.work_id = w.work_id
WHERE w.doi IN ('10.5281/zenodo.18509541', '10.5281/zenodo.18509542')
ORDER BY w.doi;

-- COMMAND ----------
-- Counts for Gold: source links are retained; contributions remove reviewed aliases.
WITH raw_counts AS (
  SELECT researcher_id, COUNT(*) AS linked_source_records
  FROM researcher_works_current GROUP BY researcher_id
), contribution_counts AS (
  SELECT researcher_id, COUNT(*) AS contributions,
         SUM(CASE WHEN eligible_for_expertise THEN 1 ELSE 0 END) AS eligible_contributions,
         SUM(CASE WHEN publication_type = 'preprint' THEN 1 ELSE 0 END) AS preprint_contributions
  FROM researcher_contributions_current GROUP BY researcher_id
)
SELECT r.display_name, raw.linked_source_records, c.contributions,
       c.eligible_contributions, c.preprint_contributions
FROM contribution_counts c
JOIN researchers_current r ON r.researcher_id = c.researcher_id
JOIN raw_counts raw ON raw.researcher_id = c.researcher_id
ORDER BY r.display_name;

-- COMMAND ----------
SELECT status, COUNT(*) AS candidates
FROM entity_resolution_review_current GROUP BY status ORDER BY status;

-- COMMAND ----------
-- Every check must be zero. Positive expertise requires a direct verified link
-- to the representative, not just authorship on its concept DOI alias.
SELECT 'multiple_eligible_members_per_researcher_contribution' AS check_name, COUNT(*) AS violations
FROM (
  SELECT rw.researcher_id, wc.contribution_id
  FROM researcher_works_current rw
  JOIN work_contributions_current wc ON wc.work_id = rw.work_id
  WHERE rw.eligible_for_expertise
  GROUP BY rw.researcher_id, wc.contribution_id HAVING COUNT(*) > 1
) duplicates
UNION ALL
SELECT 'eligible_contribution_without_verified_representative', COUNT(*)
FROM researcher_contributions_current rc
LEFT JOIN researcher_works_current rw
  ON rw.researcher_id = rc.researcher_id AND rw.work_id = rc.representative_work_id
WHERE rc.eligible_for_expertise
  AND (rw.work_id IS NULL OR NOT rw.eligible_for_expertise)
UNION ALL
SELECT 'eligible_concept_alias', COUNT(*)
FROM works_current w
JOIN work_contributions_current wc ON wc.work_id = w.work_id
WHERE w.eligible_for_expertise AND NOT wc.is_representative;

-- COMMAND ----------
-- Recomputed topic support counts distinct contributions, not concept+version twice.
SELECT r.display_name, t.display_name AS topic, s.supporting_work_count,
       ROUND(s.score, 4) AS score, s.scoring_version
FROM researcher_topic_scores_current s
JOIN researchers_current r ON r.researcher_id = s.researcher_id
JOIN topics_current t ON t.topic_id = s.topic_id
WHERE r.display_name = 'Dima Rabadi'
ORDER BY s.score DESC, s.topic_id;

-- COMMAND ----------
SELECT issue_code, severity, COUNT(*) AS issues
FROM quality_issues_current GROUP BY issue_code, severity ORDER BY issue_code;
