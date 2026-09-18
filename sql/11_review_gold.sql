-- Databricks notebook source
-- Read-only checks after SQL 09 and notebook 10 validate/build.
USE CATALOG workspace;
USE SCHEMA researchmatch_gold;

-- COMMAND ----------
-- Must return one row and source_is_current=true. Do not serve/sync stale Gold.
SELECT g.build_id AS gold_build_id, g.silver_build_id,
       s.build_id AS latest_silver_build_id,
       g.silver_build_id = s.build_id AS source_is_current,
       g.transform_version, g.status, g.document_count, g.counts_json
FROM current_build g
CROSS JOIN workspace.researchmatch_silver.current_build s;

-- COMMAND ----------
-- Expect 7 profiles, 437 publication evidence rows, 534 topics and 7 documents.
SELECT 'profiles' AS dataset, COUNT(*) AS rows FROM researcher_profiles_current
UNION ALL SELECT 'publication_evidence', COUNT(*) FROM researcher_profile_publications_current
UNION ALL SELECT 'topics', COUNT(*) FROM researcher_profile_topics_current
UNION ALL SELECT 'topic_evidence_links', COUNT(*) FROM researcher_topic_evidence_current
UNION ALL SELECT 'quality_metrics', COUNT(*) FROM profile_quality_metrics_current
UNION ALL SELECT 'search_documents', COUNT(*) FROM researcher_search_documents;

-- COMMAND ----------
-- Funding is unknown, not zero. eligible_preprint_count is a subset of eligible.
SELECT display_name, linked_source_record_count, contribution_count,
       eligible_contribution_count, excluded_contribution_count,
       eligible_preprint_count, recent_contribution_count, latest_publication_year,
       affiliation_status, funding_status, verified_award_count
FROM researcher_profiles_current
ORDER BY display_name;

-- COMMAND ----------
SELECT display_name, profile_summary, profile_url, profile_observed_at
FROM researcher_profiles_current ORDER BY display_name;

-- COMMAND ----------
SELECT p.display_name, t.topic_rank, t.topic_name, ROUND(t.score, 4) AS score,
       t.supporting_contribution_count, t.recent_supporting_contribution_count,
       t.evidence_strength, t.scoring_version
FROM researcher_profile_topics_current t
JOIN researcher_profiles_current p ON p.researcher_id = t.researcher_id
WHERE t.topic_rank <= 5
ORDER BY p.display_name, t.topic_rank;

-- COMMAND ----------
-- Evidence rank means recent-first presentation, not scientific quality/relevance.
SELECT r.display_name, p.evidence_rank, p.title, p.publication_year,
       p.publication_type, p.is_preprint, p.evidence_url, p.abstract_source,
       p.contribution_id, p.representative_work_id, p.source_work_ids
FROM researcher_profile_publications_current p
JOIN researcher_profiles_current r ON r.researcher_id = p.researcher_id
WHERE p.evidence_rank <= 3
ORDER BY r.display_name, p.evidence_rank;

-- COMMAND ----------
SELECT display_name, document_id, LENGTH(search_text) AS search_characters,
       SIZE(publication_evidence_ids) AS included_publication_count,
       top_topics_text, SUBSTRING(search_text, 1, 1200) AS search_text_preview
FROM researcher_search_documents ORDER BY display_name;

-- COMMAND ----------
-- All checks must report zero violations.
WITH support AS (
  SELECT researcher_id, topic_id, COUNT(DISTINCT contribution_id) AS contributions
  FROM researcher_topic_evidence_current GROUP BY researcher_id, topic_id
), document_evidence AS (
  SELECT gold_build_id, researcher_id, EXPLODE(publication_evidence_ids) AS publication_evidence_id
  FROM researcher_search_documents
)
SELECT 'unverified_profile' AS check_name, COUNT(*) AS violations
FROM researcher_profiles_current g
LEFT JOIN workspace.researchmatch_silver.researchers s
  ON s.build_id = g.silver_build_id AND s.researcher_id = g.researcher_id
WHERE s.researcher_id IS NULL OR s.identity_status <> 'verified'
UNION ALL
SELECT 'unsafe_publication_evidence', COUNT(*)
FROM researcher_profile_publications_current p
LEFT JOIN workspace.researchmatch_silver.works w
  ON w.build_id = p.silver_build_id AND w.work_id = p.representative_work_id
LEFT JOIN workspace.researchmatch_silver.researcher_contributions rc
  ON rc.build_id = p.silver_build_id AND rc.researcher_id = p.researcher_id
  AND rc.contribution_id = p.contribution_id
WHERE w.work_id IS NULL OR w.is_retracted OR NOT w.eligible_for_expertise
  OR rc.contribution_id IS NULL OR NOT rc.eligible_for_expertise
  OR rc.representative_work_id <> p.representative_work_id
  OR ARRAY_CONTAINS(w.quality_flags, 'concept_doi_alias')
UNION ALL
SELECT 'topic_support_count_mismatch', COUNT(*)
FROM researcher_profile_topics_current t
LEFT JOIN support s ON s.researcher_id = t.researcher_id AND s.topic_id = t.topic_id
WHERE s.contributions IS NULL OR s.contributions <> t.supporting_contribution_count
UNION ALL
SELECT 'missing_search_publication_reference', COUNT(*)
FROM document_evidence d
LEFT JOIN researcher_profile_publications_current p
  ON p.build_id = d.gold_build_id AND p.researcher_id = d.researcher_id
  AND p.publication_evidence_id = d.publication_evidence_id
WHERE p.publication_evidence_id IS NULL
UNION ALL
SELECT 'invalid_search_text', COUNT(*)
FROM researcher_search_documents
WHERE LENGTH(search_text) = 0 OR LENGTH(search_text) > 6000
  OR SHA2(search_text, 256) <> search_text_sha256
UNION ALL
SELECT 'duplicate_search_document_id', COUNT(*) - COUNT(DISTINCT document_id)
FROM researcher_search_documents
UNION ALL
SELECT 'unsupported_funding_claim', COUNT(*)
FROM researcher_profiles_current
WHERE funding_status <> 'not_established' OR verified_award_count IS NOT NULL;

-- COMMAND ----------
SELECT p.display_name, q.pending_candidate_count, q.eligible_openalex_abstract_count,
       q.eligible_repository_description_count, q.eligible_missing_abstract_count,
       q.same_title_group_count, q.source_issue_codes, q.coverage_warnings
FROM profile_quality_metrics_current q
JOIN researcher_profiles_current p ON p.researcher_id = q.researcher_id
ORDER BY p.display_name;

-- COMMAND ----------
-- Review candidates only: equal titles do NOT establish version equivalence.
SELECT r.display_name, LOWER(p.title) AS normalized_title,
       COLLECT_SET(p.representative_work_id) AS work_ids,
       COLLECT_SET(p.publication_type) AS publication_types,
       COUNT(DISTINCT p.contribution_id) AS contribution_groups
FROM researcher_profile_publications_current p
JOIN researcher_profiles_current r ON r.researcher_id = p.researcher_id
GROUP BY r.display_name, LOWER(p.title)
HAVING COUNT(DISTINCT p.contribution_id) > 1;

-- COMMAND ----------
-- Must be true. This only prepares a future Delta Sync index; it does not create one.
SHOW TBLPROPERTIES researcher_search_documents ('delta.enableChangeDataFeed');
