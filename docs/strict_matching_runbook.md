# Notebook 14 — stricter direct-evidence pilot

**Live checkpoint:** the user confirmed validate, match and evaluate runs. The
evaluation returned six partial-evidence cases, four insufficient-evidence cases
and two refinement requests, matching the local smoke behaviour. The user approved
continuing to a private UI pilot, not an independently labelled accuracy claim.
The next step is the [read-only app deployment](app_runbook.md). The instructions
below remain available for notebook comparison and troubleshooting.

Notebook 13 is preserved unchanged as the comparison baseline. Import
[`14_match_researchers_strict.py`](../notebooks/14_match_researchers_strict.py) as a
separate notebook. Policy: `matching_v2_direct_evidence`.

The user successfully ran notebook 13 in Databricks and supplied medical-imaging,
forensics and transport evidence. Review found topic-label-only subject matches,
partial transport matches and a suspected preprint/conference duplicate. Notebook
14 addresses those rules without changing Bronze, Silver, Gold, identities or the
existing AI Search index. It makes no new resource, model, Lakebase or LLM calls.
Search mode still uses the existing index's embedding service.

## Run it in Databricks

1. Import notebook 14 as a **fresh Python notebook**, alongside notebook 13. Attach
   the same serverless compute and **Run All**. It is self-contained: no `%run`,
   dependency install, new API key, SQL DDL or Volume upload is needed.
2. Keep these defaults:

   | Setting | Value |
   |---|---|
   | `mode` | `validate` |
   | `catalog` | `workspace` |
   | `silver_schema` | `researchmatch_silver` |
   | `gold_schema` | `researchmatch_gold` |
   | `endpoint_name` | `vs_endpoint_1` |
   | `embedding_model` | `databricks-gte-large-en` |
   | `retrieval_type` | `HYBRID` |
   | `required_terms` | empty |
   | `top_k` | `3` |

3. Expect `validation_passed`, seven researchers and 437 eligible publications if
   the data is unchanged. `possible_version_groups` lists same-title records for
   review; it does **not** report confirmed duplicates. All source counts stay
   unchanged. The optional synced-column notice is informational; actual index
   contents are still verified.
4. Set widget execution behavior to **Do Nothing**. Change `mode=match`, set
   `research_topic`, then **Run All** for each of the three previous topics:
   - `Machine learning for brain tumor detection in medical images`
   - `Digital forensics and malware detection`
   - `Traffic prediction and transportation planning for autonomous vehicles`
5. Read **both** `candidates` and `partial_candidates`. The latter are explicitly
   not full-query recommendations. Empty `candidates` is an expected valid outcome.
6. To examine the suspected Dima pair, inspect `possible_version_groups` from
   validation. You can also run `research_topic=Malware detection`. Same-title
   records that qualify independently get one display/bonus slot, with all source
   references retained under `possible_versions`.
7. Clear `required_terms`, set `mode=evaluate`, and Run All for the compact 12-topic
   smoke summary. Share the `checks` and the single-topic evidence above. At most
   one inference query is made per supported topic; unsupported cases skip it.

Do not rebuild or recreate anything for this change. Do not run concurrent
Silver/Gold refreshes or identity decisions. Table versions, build lineage, index
contents and result hashes are checked; failures stop execution rather than being
reported as an absence of expertise. This notebook has no create or sync mode.

## Changed rules

| Rule | Notebook 13 | Notebook 14 |
|---|---|---|
| Qualifying subject terms | Title, abstract or linked topic labels | Title/abstract only; topics are review context |
| Subject signal | Title or topic label | At least one specific anchor in the title |
| Required terms | May be supplied by topics | Must occur in that contribution's title/abstract |
| Full-query list | Could contain partial matches | All interpreted query terms must occur in one contribution |
| Partial matches | Mixed into candidates | Separate `partial_candidates` with missing terms |
| Same-title evidence | Separate contribution IDs counted | One display/bonus slot, unverified relationship warning |

Query normalization is unchanged: English terms, a small explicit inflection/phrase
mapping and generic-word removal. **All interpreted terms** is not every word or
every scientific requirement in the original request. This is lexical evidence,
not semantic entailment, independent scientific review or confirmed suitability.

A partial match still needs at least 60% of specific anchor terms directly in the
same contribution, at least one anchor in its title, and at least two meaningful
query terms (unless a one-term query). Required terms are mandatory even for partial
matches. The same title/abstract must supply them; neither different papers nor
different suspected versions are combined to manufacture a complete match.

Full-query candidates are evaluated separately from partial candidates. Each list
is capped at `top_k`, so up to six people can be displayed across the two lists.
One person appears in only one list. Within a person, full evidence excludes partial
papers from the displayed citations and support count. Evidence is ordered by
direct query coverage, anchor coverage, title anchors, non-preprint status, title
overlap and year. Non-preprint status alone is not proof of peer review.

The heuristic weights remain 35% title-anchor coverage, 35% direct anchor coverage,
20% direct query coverage and 10% support groups capped at three. Topic labels
contribute **zero** to these measures. Retrieval order breaks score ties. We have
not tuned weights to force familiar names into first place. All thresholds remain
uncalibrated and scores are not probabilities.

## Suspected publication versions

For each researcher, Unicode-normalized, case-insensitive titles with punctuation
and whitespace normalized are compared. No fuzzy matching, stopword removal or
cross-researcher merging occurs. Matching titles are **only a review signal**.

For qualifying records sharing a title:

- Select one independently qualifying record for display and ranking support.
- Retain the original contribution IDs and source references in `possible_versions`.
- Do not union abstract text, transfer authorship, assert a confirmed version
  relationship or update Delta records.
- Keep `qualifying_source_contribution_count` separate from
  `support_group_count_for_ranking` and `suspected_duplicate_records_discounted`.
- `preprint_evidence_present` describes displayed primary citations;
  `possible_versions_include_preprint` describes the linked possible versions.

The known review case is Dima's *Fast and Efficient Malware Detection with Joint
Static and Dynamic Features Through Transfer Learning*: `oa:W4310281899` (preprint)
and `oa:W4378587134` (conference paper). The collected records have matching titles;
this implementation does not claim independent publisher/arXiv verification. Any
future Silver version relationship needs source-backed review first. Different
titles can still represent versions, and identical titles can represent different
works: this discount is deliberately conservative, not a new publication total.

## Local checks and expected tradeoffs

On 2026-09-23 all 210 local tests passed, including 38 strict-policy/adapter checks.
The full collection replay completed for all 24 prompts. No live execution of
notebook 14 in Databricks is claimed.

```sh
python3 -m unittest discover -s tests -q
python3 scripts/validate_strict_matching_local.py
```

The replay rebuilds evidence from the existing local collection with the seven
confirmed-identity fixture. It supplies all seven profiles in synthetic ID order;
it does not call live AI Search or verify remote notebook execution. It runs the
12 development smoke prompts and the 12 previously reserved review prompts without
automatic relevance labels. Do not tune weights against the latter and then call
them held out.

The initial full replay showed:

- Ahed retained two direct brain-tumour support groups in the **partial** list.
  The breast-cancer paper was excluded. Missing `image`/`detection` tokens are not
  a scientific assertion that imaging or detection is absent: MRI/classification
  wording and incomplete abstracts can cause lexical false negatives.
- The combined forensics-and-malware query had no qualifying single contribution.
  This does not invalidate either researcher's expertise in the separate areas.
- Transport returned partial evidence for Abdallah and Fatma; Ahed's speed-estimation
  record no longer qualified. Their ordering is a token-based heuristic, not an
  expert judgement that Abdallah is the stronger transport collaborator.
- Nineteen same-title review groups include Dima's suspected pair. No source records were
  deleted, merged or reclassified.

On the 12 previously reserved prompts, the frozen policy returned one direct-term
result, three partial-result cases, seven insufficient-evidence cases and one
refinement request. These are outcome counts, **not correctness labels**. One
partial mobile-evidence result included an SMS-advertising paper, illustrating
that direct token overlap can still be misleading. No weights or rules were
adjusted to fit these results. Further relevance assessment and a fresh unseen set
are needed before claiming an improvement in accuracy.

Expect fewer results and more abstentions. Validate scientific relevance with the
[human review template](matching_evaluation_review.md). Missing abstracts, synonyms,
query intent and false abstentions remain open issues. This is a stricter comparison
pilot, not a calibrated replacement for human judgement or permission to build an
unqualified recommendation UI.
