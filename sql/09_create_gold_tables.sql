-- Databricks notebook source
-- Gold pilot DDL only. Does not change Bronze/Silver or create an AI Search endpoint.
USE CATALOG workspace;
CREATE SCHEMA IF NOT EXISTS researchmatch_gold COMMENT 'Verified researcher profiles and contribution-backed search evidence';
USE SCHEMA researchmatch_gold;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS build_runs (
  build_id STRING NOT NULL,
  silver_build_id STRING NOT NULL,
  transform_version STRING NOT NULL,
  input_fingerprint STRING NOT NULL,
  status STRING NOT NULL,
  started_at TIMESTAMP NOT NULL,
  validated_at TIMESTAMP,
  as_of_year INT NOT NULL,
  source_versions_json STRING NOT NULL,
  counts_json STRING,
  document_count BIGINT NOT NULL,
  error_message STRING,
  created_by STRING NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS researcher_profiles (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  silver_build_id STRING NOT NULL,
  researcher_id STRING NOT NULL,
  display_name STRING NOT NULL,
  profile_url STRING NOT NULL,
  college_codes ARRAY<STRING> NOT NULL,
  profile_orcids ARRAY<STRING> NOT NULL,
  verified_openalex_author_ids ARRAY<STRING> NOT NULL,
  profile_observed_at TIMESTAMP NOT NULL,
  identity_status STRING NOT NULL,
  affiliation_status STRING NOT NULL,
  linked_source_record_count BIGINT NOT NULL,
  contribution_count BIGINT NOT NULL,
  eligible_contribution_count BIGINT NOT NULL,
  excluded_contribution_count BIGINT NOT NULL,
  eligible_preprint_count BIGINT NOT NULL,
  recent_contribution_count BIGINT NOT NULL,
  latest_publication_year INT,
  funding_status STRING NOT NULL,
  verified_award_count BIGINT,
  profile_summary STRING NOT NULL,
  as_of_year INT NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS researcher_profile_publications (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  silver_build_id STRING NOT NULL,
  publication_evidence_id STRING NOT NULL,
  researcher_id STRING NOT NULL,
  contribution_id STRING NOT NULL,
  representative_work_id STRING NOT NULL,
  source_work_ids ARRAY<STRING> NOT NULL,
  evidence_rank INT NOT NULL,
  title STRING NOT NULL,
  doi STRING,
  evidence_url STRING NOT NULL,
  publication_year INT,
  publication_type STRING NOT NULL,
  is_preprint BOOLEAN NOT NULL,
  abstract_excerpt STRING,
  repository_description_excerpt STRING,
  abstract_source STRING,
  quality_flags ARRAY<STRING> NOT NULL,
  source_record_keys ARRAY<STRING> NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS researcher_profile_topics (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  silver_build_id STRING NOT NULL,
  researcher_id STRING NOT NULL,
  topic_id STRING NOT NULL,
  topic_name STRING NOT NULL,
  field_name STRING,
  subfield_name STRING,
  domain_name STRING,
  topic_rank INT NOT NULL,
  score DOUBLE NOT NULL,
  supporting_contribution_count BIGINT NOT NULL,
  recent_supporting_contribution_count BIGINT NOT NULL,
  latest_support_year INT,
  evidence_strength STRING NOT NULL,
  scoring_version STRING NOT NULL,
  as_of_year INT NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS researcher_topic_evidence (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  silver_build_id STRING NOT NULL,
  researcher_id STRING NOT NULL,
  topic_id STRING NOT NULL,
  contribution_id STRING NOT NULL,
  publication_evidence_id STRING NOT NULL,
  representative_work_id STRING NOT NULL,
  source_topic_score DOUBLE NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS profile_quality_metrics (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  silver_build_id STRING NOT NULL,
  researcher_id STRING NOT NULL,
  candidate_count BIGINT NOT NULL,
  pending_candidate_count BIGINT NOT NULL,
  eligible_openalex_abstract_count BIGINT NOT NULL,
  eligible_repository_description_count BIGINT NOT NULL,
  eligible_missing_abstract_count BIGINT NOT NULL,
  same_title_group_count BIGINT NOT NULL,
  source_issue_codes ARRAY<STRING> NOT NULL,
  coverage_warnings ARRAY<STRING> NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS researcher_search_document_history (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  silver_build_id STRING NOT NULL,
  document_id STRING NOT NULL,
  researcher_id STRING NOT NULL,
  display_name STRING NOT NULL,
  profile_url STRING NOT NULL,
  college_codes_text STRING NOT NULL,
  top_topics_text STRING NOT NULL,
  search_text STRING NOT NULL,
  search_text_sha256 STRING NOT NULL,
  publication_evidence_ids ARRAY<STRING> NOT NULL,
  topic_ids ARRAY<STRING> NOT NULL,
  eligible_contribution_count BIGINT NOT NULL,
  eligible_preprint_count BIGINT NOT NULL,
  affiliation_status STRING NOT NULL,
  funding_status STRING NOT NULL,
  is_searchable BOOLEAN NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS researcher_search_documents (
  gold_build_id STRING NOT NULL,
  silver_build_id STRING NOT NULL,
  document_id STRING NOT NULL,
  researcher_id STRING NOT NULL,
  display_name STRING NOT NULL,
  profile_url STRING NOT NULL,
  college_codes_text STRING NOT NULL,
  top_topics_text STRING NOT NULL,
  search_text STRING NOT NULL,
  search_text_sha256 STRING NOT NULL,
  publication_evidence_ids ARRAY<STRING> NOT NULL,
  topic_ids ARRAY<STRING> NOT NULL,
  eligible_contribution_count BIGINT NOT NULL,
  eligible_preprint_count BIGINT NOT NULL,
  affiliation_status STRING NOT NULL,
  funding_status STRING NOT NULL,
  is_searchable BOOLEAN NOT NULL
) USING DELTA TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');
-- COMMAND ----------
ALTER TABLE researcher_search_documents SET TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');
-- COMMAND ----------
-- The AI Search index syncs from this table's change history. With the 7-day
-- default, a Gold rebuild more than a week after the last one fails with
-- VECTOR_SEARCH_SOURCE_HISTORY_OUT_OF_RETENTION. The table is tiny; keep a year.
ALTER TABLE researcher_search_documents SET TBLPROPERTIES (
  'delta.deletedFileRetentionDuration' = 'interval 365 days',
  'delta.logRetentionDuration' = 'interval 365 days');
-- COMMAND ----------
-- Publication pointer: one atomic serving-table MERGE switches all current views.
-- A validated snapshot in build_runs alone is NOT a published build.
CREATE VIEW IF NOT EXISTS current_build AS
WITH published AS (
  SELECT MIN(gold_build_id) AS build_id, COUNT(*) AS document_count
  FROM researcher_search_documents
  HAVING COUNT(DISTINCT gold_build_id) = 1
     AND COUNT(*) = COUNT(DISTINCT document_id)
)
SELECT b.* FROM build_runs b
JOIN published p ON b.build_id = p.build_id AND b.document_count = p.document_count
WHERE b.status = 'validated';
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS researcher_profiles_current AS
SELECT t.* FROM researcher_profiles t JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS researcher_profile_publications_current AS
SELECT t.* FROM researcher_profile_publications t JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS researcher_profile_topics_current AS
SELECT t.* FROM researcher_profile_topics t JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS researcher_topic_evidence_current AS
SELECT t.* FROM researcher_topic_evidence t JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS profile_quality_metrics_current AS
SELECT t.* FROM profile_quality_metrics t JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS researcher_search_document_history_current AS
SELECT t.* FROM researcher_search_document_history t JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
SHOW TABLES IN workspace.researchmatch_gold;
