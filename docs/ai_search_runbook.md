# ResearchMatch AI Search pilot

**Checkpoint:** the user confirmed Gold's freshness/count checks, seven zero-violation
checks and Change Data Feed. Abdallah's preview contains 5,216 characters and 12
publication references. Gold does not need rebuilding for this task.

The [notebook 12](../notebooks/12_setup_and_test_ai_search.py) discovers embedding
models in **your workspace**, creates the intended index, verifies its contents and
runs research-topic searches. You chose to execute it yourself. The user has now
confirmed GTE inference for all seven profiles (1,242–1,535 tokens), and successful
index creation on the existing `vs_endpoint_1` STANDARD endpoint. The index reports
seven rows, ready, and processed source version 2. **Full content verification and
live topic-search results remain pending.**

### Resume after the optional column-list error

The previous notebook rejected an index description that omitted `columns_to_sync`.
That was an overly strict client-side check, not evidence that creation failed.
The API defines this column list as optional and its GET example omits it.
[Get-index reference](https://docs.databricks.com/api/workspace/vectorsearchindexes/getindex)

Re-import the updated notebook 12 (or replace its code), keep widget execution set
to **Do Nothing**, and use:

- `endpoint_name=vs_endpoint_1`
- `embedding_model=databricks-gte-large-en`
- `mode=status`

Run **all cells**, including the function definitions. No `create`, `probe`, `sync`
or Gold rebuild is needed solely for this fix. Missing description metadata now
defers to the mandatory content scan; it does not waive any data checks. Only after
the full `Index content verified...` message should you switch to `mode=test`.
If the scan reports missing actual columns or stale values, stop and share that
new error; do not delete/recreate the index. All 142 offline tests pass, including
regressions for omitted/null column lists and missing/stale indexed data.

Confirmed lineage: Gold `9c1571ed-a5fb-45e3-8f5f-b066801e7cd5`, Silver
`3230bb0f-28c7-4830-90b5-5a21034794cb`, Gold source version 2, identity version 3.

## 1. Which embedding model?

Official documentation checked on 2026-09-18 lists these Databricks-hosted models:

| Model endpoint | Documented context | Pilot choice |
|---|---:|---|
| `databricks-gte-large-en` | 8,192 tokens | First choice for the existing English profiles |
| `databricks-qwen3-embedding-0-6b` | About 32K tokens | Alternative if available; documented as preview |
| `databricks-bge-large-en` | 512 tokens | Not used: too short for the full profile documents |

All support 1,024-dimensional embeddings; Qwen also supports configurable dimensions.
These are product-supported models, **not a guarantee that every Free Edition
workspace exposes or permits querying each one**.
[Databricks model specifications](https://docs.databricks.com/aws/en/machine-learning/foundation-model-apis/supported-models)

Free Edition limits AI Search to one endpoint/one search unit, does not support
Direct Vector Access and excludes provisioned throughput. We use an existing
hosted embedding endpoint—not a custom/GPU/provisioned deployment. Usage limits
can temporarily suspend compute; do not upgrade or provision paid resources for
this pilot.
[Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations)

`auto` prefers a listed GTE endpoint, then listed Qwen. An existing index retains
its model. Listing is only discovery; the separate probe sends all seven complete
documents, checks embedding shape/values and reports token usage when supplied.
If a listed endpoint denies access or fails, the notebook stops rather than
silently switching models. A successful probe proves inference access, not index
compatibility or research-matching quality. Review any unexpectedly capped token
usage; the 6,000-character document bound is not itself a token guarantee.

## 2. Exact Databricks steps

No new SQL file, Volume upload, OpenAlex key or Bronze/Silver/Gold rerun is needed.
Do not run Gold/Silver refreshes or identity approvals concurrently with this notebook.

1. Import `notebooks/12_setup_and_test_ai_search.py` into Workspace as a **fresh
   Python notebook**. Attach serverless notebook compute.
2. **Run All**, including the definitions cell. Defaults:

   | Widget | Value |
   |---|---|
   | `mode` | `discover` |
   | `catalog` | `workspace` |
   | `silver_schema` | `researchmatch_silver` |
   | `gold_schema` | `researchmatch_gold` |
   | `endpoint_name` | `researchmatch-search` |
   | `embedding_model` | `auto` |

   Discovery checks current Gold/Silver/identity lineage and prints the model
   inventory and existing search resources. It makes no inference calls or writes.
   It uses the installed `databricks-sdk` and platform-managed notebook authentication.
   No manually generated token is required.
   [SDK notebook authentication](https://docs.databricks.com/aws/en/dev-tools/sdk-python)
3. In widget settings choose **Do Nothing** before changing values. If there is an
   existing STANDARD AI Search endpoint, set `endpoint_name` to that exact name
   to reuse it. Do not delete it or create a second endpoint. Existing indexes
   with another name are left alone. A conflicting intended index stops the run.
4. Set `mode=probe`, **Run All**. Expect `model_probe: passed` and seven document
   entries. Keep the printed model by entering its exact name in `embedding_model`
   for subsequent steps. This prevents inventory changes from changing the choice
   between runs. Probe mode performs seven inference calls but creates no resources.
5. Set `mode=create`, **Run All**. If the endpoint does not exist, the notebook
   probes again, submits creation of **one STANDARD endpoint**, and stops with
   `endpoint_creation_submitted`. Wait a few minutes, then rerun `create`.
6. Once the endpoint is ONLINE, `create` probes again and submits the index with
   initial ingestion. Expect `index_creation_submitted`. Creation is asynchronous;
   this message does **not** mean the index is ready. Existing compatible resources
   are reused; no replacement/deletion or permissions changes are performed.
7. Wait a few minutes, change to `mode=status`, **Run All**. If it reports pending,
   inspect the printed service message and try `status` later. Do not repeatedly
   issue `sync` or recreate the index. Continue only after:

   ```text
   Index content verified: seven document IDs, all synced metadata, text and hashes match current Gold.
   ```

8. Set `mode=test`, **Run All**. This performs the content/freshness checks again,
   then 16 live queries: eight topics with both ANN and HYBRID, top three candidates
   each. It prints `checks` and `results` including evidence links. It does not
   write evaluations or change Gold tables.
9. Share the model name/probe summary, verified index status and the `checks` array.
   Also share results/evidence for `forensics`, `medical_imaging` and `out_of_scope`.
   Never share access tokens, headers or your full `.env`.

If the installed SDK is missing or notebook authentication fails, stop and share
the error. The notebook deliberately does not auto-install packages or ask you to
paste a token. We can resolve the actual runtime issue without guessing credentials.

## 3. Index configuration and safety

| Setting | Value |
|---|---|
| Endpoint | `researchmatch-search` or explicitly selected existing STANDARD endpoint |
| Index | `workspace.researchmatch_gold.researcher_profiles_index` |
| Source | `workspace.researchmatch_gold.researcher_search_documents` |
| Index type | `DELTA_SYNC` |
| Pipeline type | `TRIGGERED` |
| Primary key | `document_id` |
| Embedding source | `search_text` |
| Queries | ANN and HYBRID; no extra reranker/LLM |

The source has CDF enabled. Selected scalar columns include researcher IDs, names,
URLs, topic labels, count/coverage labels, Gold/Silver build IDs and the content
hash. Arrays/full publication rows remain in Gold rather than being copied into
the index. Embeddings are managed by the index; there is no extra writeback table.
[Index creation requirements](https://docs.databricks.com/aws/en/ai-search/create-ai-search)

The notebook uses documented workspace REST routes through `WorkspaceClient`'s
authenticated API client. Their route names retain `vector-search` despite the
product's AI Search name. It needs no separate AI Search SDK install.
[Index API](https://docs.databricks.com/api/workspace/vectorsearchindexes/createindex)

The serving table is checked at one Delta version. A ready flag or a count of seven
is insufficient: status scans every indexed row and compares IDs, full search text,
hash and all synced metadata against the current Gold documents. Tests filter by
the captured Gold build and recheck result lineage plus source freshness. Stale
results are rejected, not shown as current matches. Only one producer/operator at
a time is supported; these checks are not cross-service transactions.

An omitted/null synced-column list in the description is treated as unknown, not
missing data. If a list is explicitly returned, it must include required metadata.
Actual indexed rows must always pass the content scan before status/test succeeds.

## 4. What the search tests mean

Topics cover blockchain security, digital forensics, medical imaging, technology
adoption, transportation, employability and reflective education, plus an
out-of-scope astronomy topic. Candidate names in the tests are **hypotheses from
our observed topic summaries**, not independently labelled ground truth.

- `hypothesis_hit_at_3` indicates whether at least one hypothesized candidate is in
  the top three. It is a diagnostic, not product accuracy or proof of expertise.
- ANN searches meaning; hybrid combines semantic and keyword retrieval. Scores
  are not probabilities and should not be compared directly across these modes.
  [Querying AI Search](https://docs.databricks.com/aws/en/ai-search/query-ai-search)
- The negative query may still return three people. Nearest-neighbour retrieval
  does not establish suitability; the test explicitly labels those results for
  out-of-scope review. A calibrated abstention policy remains future app work.
- Publication previews are selected by transparent lexical overlap with the
  query in eligible Gold titles/abstract excerpts. They are supporting material
  for human review, not an LLM explanation or the index's exact reason for ranking.
  No overlap produces an empty preview, never invented evidence.
- Preprint labels remain visible. Retractions and aliases stay excluded through
  the existing Gold evidence table. Funding is not inferred or used for ranking.

Review ranking and evidence before building the app. With only seven people,
retrieval evaluation is a smoke test, not evidence that the full university roster
is covered. No confirmed live search results are recorded until you run `test`.

## 5. Refreshes and errors

After a deliberate Silver/Gold refresh, run notebook 12 `discover`, then `sync`
once, wait, then `status` and `test`. `create` does not silently trigger repeated
syncs for an existing index. Each create stage re-probes at most seven documents;
this small redundant check is deliberate, and still consumes inference allowance.

- **No compatible model listed:** check the Serving UI. If a documented GTE/Qwen
  endpoint is shown there, enter its exact name and run `probe`. Do not substitute
  BGE, create a custom model or alter source documents to bypass the check.
- **403/permission failure:** listing, invocation and index creation have distinct
  privileges. Report the error; it is not treated as a missing resource.
- **429/quota error:** stop and wait for quota recovery; no retry loop or paid
  upgrade is configured by this notebook.
- **Pending/failed initialization:** inspect Compute → AI Search and the index's
  pipeline/status message. Retain existing resources; do not repeatedly create.
- **Stale content:** if initialization is complete, check source freshness, trigger
  one sync and recheck. Never suppress the hash/build checks.
- **Different existing index model/source:** the notebook stops. Replacement needs
  a separate reviewed decision; no destructive recreation is included.

## 6. Local verification

`python3 -m unittest discover -s tests -q` tests discovery-only defaults, model
selection, endpoint quota guards, exact creation payloads, repeat-create behavior,
pending states, explicit sync, scan/result lineage, embedding probes and evidence
selection using simulated services. These tests do not provision Databricks or
measure live retrieval quality. The existing Gold fixture/replay remains unchanged.
