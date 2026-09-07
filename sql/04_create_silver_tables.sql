-- Databricks notebook source
-- ResearchMatch Silver v1: immutable build snapshots and governed review inputs.
-- Run once before notebook 05. No Bronze data is changed.
USE CATALOG workspace;
CREATE SCHEMA IF NOT EXISTS researchmatch_silver COMMENT 'Structured research evidence and reviewed identity links';
USE SCHEMA researchmatch_silver;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS build_runs (
  build_id STRING NOT NULL,
  transform_version STRING NOT NULL,
  input_fingerprint STRING NOT NULL,
  status STRING NOT NULL,
  started_at TIMESTAMP NOT NULL,
  completed_at TIMESTAMP,
  as_of_year INT NOT NULL,
  source_versions_json STRING NOT NULL,
  counts_json STRING,
  error_message STRING,
  created_by STRING NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS identity_decisions (
  decision_id STRING NOT NULL,
  candidate_id STRING NOT NULL,
  evidence_hash STRING NOT NULL,
  decision STRING NOT NULL,
  rationale STRING NOT NULL,
  reviewed_by STRING NOT NULL,
  reviewed_at TIMESTAMP NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS researchers (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  researcher_id STRING NOT NULL,
  display_name STRING NOT NULL,
  normalized_name STRING NOT NULL,
  profile_url STRING NOT NULL,
  college_codes ARRAY<STRING> NOT NULL,
  personnel_sections ARRAY<STRING> NOT NULL,
  biography STRING,
  research_activity_text STRING,
  profile_orcids ARRAY<STRING> NOT NULL,
  identity_status STRING NOT NULL,
  quality_flags ARRAY<STRING> NOT NULL,
  source_record_keys ARRAY<STRING> NOT NULL,
  observed_at TIMESTAMP NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS researcher_aliases (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  researcher_id STRING NOT NULL,
  alias STRING NOT NULL,
  normalized_alias STRING NOT NULL,
  source_record_keys ARRAY<STRING> NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS researcher_identifiers (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  researcher_id STRING NOT NULL,
  identifier_type STRING NOT NULL,
  identifier_value STRING NOT NULL,
  verification_status STRING NOT NULL,
  source_record_keys ARRAY<STRING> NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS researcher_affiliations (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  researcher_id STRING NOT NULL,
  institution_id STRING NOT NULL,
  college_code STRING,
  personnel_section STRING,
  profile_url STRING NOT NULL,
  observed_at TIMESTAMP NOT NULL,
  verification_status STRING NOT NULL,
  source_record_keys ARRAY<STRING> NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS authors (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  openalex_author_id STRING NOT NULL,
  display_name STRING,
  normalized_name STRING,
  orcid STRING,
  works_count BIGINT NOT NULL,
  last_known_institution_ids ARRAY<STRING> NOT NULL,
  source_record_keys ARRAY<STRING> NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS entity_resolution_review (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  candidate_id STRING NOT NULL,
  researcher_id STRING NOT NULL,
  openalex_author_id STRING NOT NULL,
  match_method STRING NOT NULL,
  evidence_hash STRING NOT NULL,
  evidence_json STRING NOT NULL,
  status STRING NOT NULL,
  decision_id STRING,
  quality_flags ARRAY<STRING> NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS works (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  work_id STRING NOT NULL,
  title STRING,
  doi STRING,
  publication_year INT,
  publication_type STRING,
  abstract STRING,
  abstract_source STRING,
  oai_subjects ARRAY<STRING> NOT NULL,
  is_retracted BOOLEAN NOT NULL,
  eligible_for_expertise BOOLEAN NOT NULL,
  quality_flags ARRAY<STRING> NOT NULL,
  source_record_keys ARRAY<STRING> NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS work_sources (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  work_id STRING NOT NULL,
  source_system STRING NOT NULL,
  source_id STRING NOT NULL,
  doi STRING,
  source_title STRING,
  match_method STRING NOT NULL,
  is_deleted BOOLEAN NOT NULL,
  source_record_keys ARRAY<STRING> NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS work_authorships (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  work_id STRING NOT NULL,
  source_system STRING NOT NULL,
  source_id STRING NOT NULL,
  author_position INT NOT NULL,
  openalex_author_id STRING,
  raw_author_name STRING,
  author_orcid STRING,
  institution_ids ARRAY<STRING> NOT NULL,
  is_corresponding BOOLEAN NOT NULL,
  source_record_keys ARRAY<STRING> NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS researcher_works (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  researcher_id STRING NOT NULL,
  work_id STRING NOT NULL,
  openalex_author_ids ARRAY<STRING> NOT NULL,
  candidate_ids ARRAY<STRING> NOT NULL,
  verification_status STRING NOT NULL,
  eligible_for_expertise BOOLEAN NOT NULL,
  source_record_keys ARRAY<STRING> NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS topics (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  topic_id STRING NOT NULL,
  display_name STRING,
  subfield_name STRING,
  field_name STRING,
  domain_name STRING,
  source_record_keys ARRAY<STRING> NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS work_topics (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  work_id STRING NOT NULL,
  topic_id STRING NOT NULL,
  score DOUBLE NOT NULL,
  is_primary BOOLEAN NOT NULL,
  source_record_keys ARRAY<STRING> NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS researcher_topic_scores (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  researcher_id STRING NOT NULL,
  topic_id STRING NOT NULL,
  score DOUBLE NOT NULL,
  supporting_work_count BIGINT NOT NULL,
  scoring_version STRING NOT NULL,
  as_of_year INT NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS coauthor_edges (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  researcher_id_a STRING NOT NULL,
  researcher_id_b STRING NOT NULL,
  shared_work_count BIGINT NOT NULL,
  work_ids ARRAY<STRING> NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS funders (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  funder_id STRING NOT NULL,
  display_name STRING,
  country_code STRING,
  source_record_keys ARRAY<STRING> NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS awards (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  award_id STRING NOT NULL,
  title STRING,
  funder_id STRING,
  funder_award_id STRING,
  amount DOUBLE,
  currency STRING,
  start_date STRING,
  end_date STRING,
  institution_awarded_json STRING,
  investigators_json STRING,
  attribution_status STRING NOT NULL,
  source_record_keys ARRAY<STRING> NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS work_awards (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  work_id STRING NOT NULL,
  award_id STRING NOT NULL,
  resolution_status STRING NOT NULL,
  source_record_keys ARRAY<STRING> NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS award_evidence (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  award_id STRING NOT NULL,
  work_id STRING,
  assertion_type STRING NOT NULL,
  assertion_json STRING NOT NULL,
  source_record_keys ARRAY<STRING> NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS researcher_awards (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  researcher_id STRING NOT NULL,
  award_id STRING NOT NULL,
  role STRING,
  verification_status STRING NOT NULL,
  evidence_id STRING NOT NULL
) USING DELTA;
-- COMMAND ----------
CREATE TABLE IF NOT EXISTS quality_issues (
  build_id STRING NOT NULL,
  row_key STRING NOT NULL,
  entity_type STRING NOT NULL,
  entity_id STRING NOT NULL,
  issue_code STRING NOT NULL,
  severity STRING NOT NULL,
  detail STRING NOT NULL,
  source_record_keys ARRAY<STRING> NOT NULL
) USING DELTA;
-- COMMAND ----------
-- Only a fully checked build becomes visible through the current views.
CREATE VIEW IF NOT EXISTS current_build AS
SELECT * FROM build_runs
WHERE status = 'succeeded'
QUALIFY ROW_NUMBER() OVER (ORDER BY completed_at DESC, build_id DESC) = 1;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS researchers_current AS
SELECT t.* FROM researchers t
JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS researcher_aliases_current AS
SELECT t.* FROM researcher_aliases t
JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS researcher_identifiers_current AS
SELECT t.* FROM researcher_identifiers t
JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS researcher_affiliations_current AS
SELECT t.* FROM researcher_affiliations t
JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS authors_current AS
SELECT t.* FROM authors t
JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS entity_resolution_review_current AS
SELECT t.* FROM entity_resolution_review t
JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS works_current AS
SELECT t.* FROM works t
JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS work_sources_current AS
SELECT t.* FROM work_sources t
JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS work_authorships_current AS
SELECT t.* FROM work_authorships t
JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS researcher_works_current AS
SELECT t.* FROM researcher_works t
JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS topics_current AS
SELECT t.* FROM topics t
JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS work_topics_current AS
SELECT t.* FROM work_topics t
JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS researcher_topic_scores_current AS
SELECT t.* FROM researcher_topic_scores t
JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS coauthor_edges_current AS
SELECT t.* FROM coauthor_edges t
JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS funders_current AS
SELECT t.* FROM funders t
JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS awards_current AS
SELECT t.* FROM awards t
JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS work_awards_current AS
SELECT t.* FROM work_awards t
JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS award_evidence_current AS
SELECT t.* FROM award_evidence t
JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS researcher_awards_current AS
SELECT t.* FROM researcher_awards t
JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
CREATE VIEW IF NOT EXISTS quality_issues_current AS
SELECT t.* FROM quality_issues t
JOIN current_build b ON t.build_id = b.build_id;
-- COMMAND ----------
SHOW TABLES IN workspace.researchmatch_silver;
