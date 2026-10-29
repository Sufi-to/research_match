# Rebuild order and Free Edition traps

## After new identity approvals (e.g. a batch NN SQL file)

1. **Approvals:** the guarded SQL (`sql/07`, `sql/15`, …) records accepts under the
   user's identity. It is the user's judgement: ask explicitly. If running it for them, the
   Statement API has no session, so inline the temp views as CTEs into the single INSERT,
   with only the permission switch set to `true`. Cut the INSERT at its real end; the
   rationale string contains a `;`. Then verify the accepts with SELECT.
2. **Silver:** notebook 05 `validate`, check `identity_statuses`, then `build`.
3. **Silver checks:** SQL 08 cells 4 and 6 (per-researcher counts; three zero-violation checks).
4. **Gold:** notebook 10 `validate` then `build`, with `expected_verified_profiles` = the
   verified count. Then SQL 11 cells 1, 2 and 8 (freshness, counts, seven zero checks).
5. **Index:** notebook 12 `sync` (once), wait for `indexed_row_count` to match, then
   `status`, then `test`. Poll with
   `databricks vector-search-indexes get-index workspace.researchmatch_gold.researcher_profiles_index`.
6. **App:** use the `rm-app-deploy` skill. Connection check should show the new count.
7. **Health check:** `scripts/health_check.sh` should be all PASS, with the index rows equal
   to the new `document_count`.

## Traps seen on 2026-10-27

- **Endpoint gone:** Free Edition removes idle AI Search endpoints. `create` mode
  recreates `vs_endpoint_1`. A new endpoint has no app permission, so the user must grant
  the app CAN_USE (`databricks permissions update vector-search-endpoints <id> …`).
- **Orphaned index name:** after an endpoint vanishes, the index's Unity Catalog entry
  remains, and `create` fails with `ResourceAlreadyExists`. The user deletes it, then
  re-run `create`.
- **`VECTOR_SEARCH_SOURCE_HISTORY_OUT_OF_RETENTION`:** sync retries repeatedly and fails
  when the index's last processed source version is older than the table's retention.
  The fix is to delete and recreate the index. `sql/09` now sets 365-day retention on
  `researcher_search_documents`; check with `SHOW TBLPROPERTIES` if the table was ever
  recreated.
- **Grants lost on recreation:** a recreated index has no app grant. The user runs
  `databricks grants update table workspace.researchmatch_gold.researcher_profiles_index --json '{"changes":[{"principal":"<app SP application id>","add":["SELECT"]}]}'`.
- **Stale running app:** after Gold changes its profile count, the deployed app's
  validation fails until the app code matching that Gold is deployed.

## Traps seen on 2026-10-29

- **Silver rebuild breaks the app until Gold is rebuilt.** The app requires Gold's
  `silver_build_id` to equal the current Silver build (`matching_policy.py`
  `source_snapshot`). Any Silver build, even one adding only pending candidates, must
  be followed by Gold (same profile count), one index sync and `status`.
- **Equal row counts do not prove a sync finished.** When the profile count is
  unchanged, `indexed_row_count` already matches before the sync. Wait for notebook 12
  `status` to succeed; `health_check.sh` now also compares the index's processed source
  version with the source table's latest version.
- **Judge a run by its `RUN <id> SUCCESS|FAILED` line, never by grepping for a success
  message.** The run export includes the notebook source, so the message text appears
  even when the run failed.
- **Recording approvals can be blocked by Claude Code's auto-mode safety check**
  ("Modify Shared Resources"). Then prepare the CTE-inlined INSERT payload locally and
  give the user one `!` command to run it, or they run the batch SQL in the workspace
  (preview, then permission `true`). Do not build helper scripts or edit permission
  settings to get around the block; that is also refused ("Auto-Mode Bypass"), and
  granting the permission is the user's decision.
- **`apps deploy` stdout can come back empty** even when the deployment succeeds;
  `deploy_app.sh` now reports the app's active deployment instead.

