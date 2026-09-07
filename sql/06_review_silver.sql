-- Databricks notebook source
-- Read-only exploration of the last fully successful Silver build.
USE CATALOG workspace;
USE SCHEMA researchmatch_silver;

-- COMMAND ----------
SELECT build_id, transform_version, completed_at, counts_json
FROM current_build;

-- COMMAND ----------
SELECT researcher_id, display_name, college_codes, personnel_sections,
       profile_url, profile_orcids, identity_status, quality_flags
FROM researchers_current
ORDER BY display_name;

-- COMMAND ----------
-- Review candidates; name matches and OpenAlex affiliation are not identity proof.
SELECT c.candidate_id, r.display_name AS zu_name, r.profile_url,
       r.profile_orcids, a.openalex_author_id, a.display_name AS openalex_name,
       a.orcid AS openalex_orcid, a.last_known_institution_ids,
       c.match_method, c.status, c.quality_flags, c.evidence_hash, c.evidence_json
FROM entity_resolution_review_current c
JOIN researchers_current r ON c.researcher_id = r.researcher_id
JOIN authors_current a ON c.openalex_author_id = a.openalex_author_id
ORDER BY CASE WHEN c.match_method = 'profile_orcid' THEN 0 ELSE 1 END,
         r.display_name, a.openalex_author_id;

-- COMMAND ----------
SELECT r.display_name, r.profile_url, r.profile_orcids
FROM researchers_current r
LEFT ANTI JOIN entity_resolution_review_current c
ON r.researcher_id = c.researcher_id;

-- COMMAND ----------
SELECT COUNT(*) AS works,
       COUNT_IF(abstract IS NOT NULL) AS with_abstract_or_description,
       COUNT_IF(abstract_source = 'oai_description') AS using_repository_description,
       COUNT_IF(is_retracted) AS retracted,
       COUNT_IF(eligible_for_expertise) AS eligible_before_identity_filter
FROM works_current;

-- COMMAND ----------
SELECT work_id, title, doi, publication_year, abstract_source,
       SUBSTRING(abstract, 1, 400) AS text_preview, oai_subjects, quality_flags
FROM works_current
WHERE abstract_source = 'oai_description'
ORDER BY work_id
LIMIT 20;

-- COMMAND ----------
SELECT issue_code, severity, COUNT(*) AS issues
FROM quality_issues_current
GROUP BY issue_code, severity
ORDER BY issues DESC;

-- COMMAND ----------
SELECT COUNT(*) AS publication_award_links,
       COUNT(DISTINCT award_id) AS distinct_award_ids,
       COUNT(DISTINCT CASE WHEN resolution_status = 'unresolved' THEN award_id END) AS unresolved_awards
FROM work_awards_current;

-- COMMAND ----------
-- These are expected to be zero until identity decisions are explicitly accepted.
SELECT 'verified_researcher_works' AS relationship, COUNT(*) AS rows
FROM researcher_works_current
UNION ALL
SELECT 'researcher_topic_scores', COUNT(*) FROM researcher_topic_scores_current
UNION ALL
SELECT 'pilot_coauthor_edges', COUNT(*) FROM coauthor_edges_current
UNION ALL
SELECT 'verified_researcher_awards', COUNT(*) FROM researcher_awards_current;

-- COMMAND ----------
-- Edit this identifier to inspect publication evidence for ONE candidate.
SELECT a.openalex_author_id, a.raw_author_name, a.author_orcid,
       a.institution_ids, w.work_id, w.title, w.publication_year,
       w.is_retracted, w.quality_flags
FROM work_authorships_current a
JOIN works_current w ON a.work_id = w.work_id
WHERE a.openalex_author_id = 'PASTE_ONE_OPENALEX_AUTHOR_URL'
ORDER BY w.publication_year DESC, w.work_id
LIMIT 50;

-- No decisions are made by this notebook. See the runbook's explicit single-candidate
-- approval/rejection procedure. Never bulk-approve candidates based only on names.
