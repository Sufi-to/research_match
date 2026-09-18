# Evidence-backed researcher matching — notebook 13

## Current checkpoint

The user confirmed notebook 12's full index-content check and all 16 live search
checks on `vs_endpoint_1`, using `databricks-gte-large-en`. The seven documents match
Gold build `9c1571ed-a5fb-45e3-8f5f-b066801e7cd5`, Silver build
`3230bb0f-28c7-4830-90b5-5a21034794cb`, serving Delta version 2, identity version 3.

For seven in-scope topics, the hypothesized researcher appeared in the top three
for both ANN and hybrid. They were listed first in 6/7 ANN and 5/7 hybrid results;
the blockchain hybrid leaders tied. These are small hypothesis-based smoke checks,
not independently labelled accuracy. Astronomy still returned candidates with empty
selected evidence, including a hybrid score of 1.0. Some publication previews matched
only broad terms. Those findings motivate the evidence gate in notebook 13.

Notebook 13 is now implemented for local testing and user execution. It **does not
rebuild the index or change any tables**. Live matching results remain to be checked
in Databricks. No UI, team assembly or verified grant-matching feature is included.

## 1. Run in Databricks

1. Import [notebook 13](../notebooks/13_match_researchers.py) as a **fresh Python
   notebook**, attach serverless and **Run All**. It is self-contained; notebook 12
   does not need to be run or imported first. No packages or API keys are added.
2. Defaults are:

   | Widget | Value |
   |---|---|
   | `mode` | `validate` |
   | `catalog` | `workspace` |
   | `silver_schema` | `researchmatch_silver` |
   | `gold_schema` | `researchmatch_gold` |
   | `endpoint_name` | `vs_endpoint_1` |
   | `embedding_model` | `databricks-gte-large-en` |
   | `retrieval_type` | `HYBRID` |
   | `research_topic` | `Machine learning for brain tumor detection in medical images` |
   | `required_terms` | empty |
   | `top_k` | `3` |

   Expect `status: validation_passed`, `researchers: 7`,
   `eligible_publications: 437`. Validation reads tables and scans the existing
   index, but makes no embedding/search inference calls or data/resource writes.
3. Set widget execution behavior to **Do Nothing**. Change `mode=match` and **Run
   All**, including the definitions cell. Inspect the medical-imaging result,
   evidence links, publication types and any missing query terms.
4. Try `research_topic=Digital forensics and malware detection`, leaving
   `required_terms` empty. Run All again. One supported match request makes at most
   one search query; screening can stop unsupported/broad topics before inference.
5. Try `research_topic=Exoplanet atmospheric spectroscopy and stellar nucleosynthesis`.
   Expect no recommendations under this pilot policy, rather than seven-profile
   nearest neighbours being portrayed as astronomy experts.
6. Clear `required_terms`, change `mode=evaluate` and Run All. This produces a
   **compact** 12-query summary, avoiding notebook 12's very long evidence dump.
   No pass-rate/accuracy claim is made. For details on one result, use `mode=match`
   with that topic. Full single-topic output is also held in `match_result`.
7. Share validation counts, the compact evaluation `checks`, and single-topic
   evidence for medical imaging and forensics. We will review these before UI work.

You can repeat the evaluation with `retrieval_type=ANN` to compare retrieval order,
but each run uses allowance. Do not run concurrent Silver/Gold refreshes or identity
approvals. Never run `create` or `sync` for this task: notebook 13 has no such modes.

## 2. How matching works

1. Confirm published Gold still corresponds to current Silver and identity decisions.
   Verify every indexed document's text, hash and synced metadata against Gold.
2. Read bounded, version-pinned Gold publication, topic and topic-evidence tables.
   Check joins, contribution uniqueness, eligibility flags and support counts.
3. Normalize a short English topic into meaningful terms. A small explicit mapping
   handles AI/ML phrases and common inflections (such as tumour/tumor and forensics).
   Boilerplate and generic words are removed. Broad terms such as learning,
   information, detection or education cannot establish a match alone.
4. Treat the remaining specific terms as **anchor terms**. If there are no anchors,
   request refinement. Negation/exclusions also request refinement, because the
   lexical policy cannot reliably interpret them. If an anchor is absent from all
   collected evidence, return insufficient evidence before querying the index.
5. Retrieve up to all seven profiles from the existing index, filtered to the
   captured Gold build and searchable records. Validate returned IDs/hashes/builds.
   ANN or hybrid is selectable; no new model or LLM reranker is used.
6. A qualifying **single contribution** must contain at least 60% of anchor terms
   in its title, OpenAlex abstract excerpt or explicitly linked source-topic labels.
   At least one anchor must appear in the title or linked topic, not solely in an
   abstract's passing mention. At least two meaningful query terms must match unless
   the query contains just one. Every explicitly required term must match that
   same contribution. Unrelated papers cannot be joined to fabricate joint evidence.
7. Rank supporting publications by anchor hits in their titles first, then anchor
   coverage, query coverage, remaining title overlap and year. Display up to three
   evidence rows, with source-topic matches distinguished from title/abstract matches.
8. Rank candidates using the best displayed contribution: 35% title-anchor coverage,
   35% combined anchor coverage, 20% query-term coverage, and 10% supporting-group
   count capped at three. Retrieval order only breaks ties. Raw search scores,
   citations, grant amounts and researcher rank do not establish confidence.
9. Return up to `top_k` candidates, or insufficient evidence. Recheck source and
   evidence-table versions before printing. An operational failure is raised as an
   error, never disguised as “no suitable researcher.”

**The 60% gate and weights are explicit initial heuristics, not calibrated thresholds.**
They are versioned as `matching_v1_explicit_evidence`. Test coverage establishes
that the code follows the policy, not that the policy is scientifically accurate.
Use the manual review template before changing weights or claiming product quality.

Databricks supports both semantic ANN and hybrid retrieval. Here they provide
candidates; the custom evidence gate determines what is shown for human review.
[AI Search query documentation](https://docs.databricks.com/aws/en/ai-search/query-ai-search)

## 3. Reading the response

- `candidates_for_review`: qualifying evidence was found; collaboration suitability
  is not confirmed. A candidate with `partial_query_coverage` lacks some anchor terms.
- `insufficient_evidence`: nothing qualified under this collection/policy. It does
  not prove that no ZU researcher has the expertise, even within the seven profiles.
- `needs_refinement`: the topic is too broad, complex or contains unsupported negation.
- `relevance_score_heuristic`: an ordering aid, **not a probability or percentage**.
  A value of 1.0 does not mean certainty or perfect scientific relevance.
- `qualifying_contribution_count`: eligible contribution groups meeting the rule,
  not necessarily independent studies or peer-reviewed papers.
- `evidence_scope=all_anchor_terms`: all specific anchors occurred in the best
  evidence bundle; broader query terms can still be missing. Inspect
  `best_evidence_missing_terms`, title/abstract matches and linked topics.
- `topic_only_anchor_terms`: terms supported only by OpenAlex topic labels, not the
  retained publication title/abstract. Topic classification can be broad or wrong;
  these are clearly marked inferred evidence, not direct claims from the paper.
- Preprints are labelled in each evidence row. Retractions/concept aliases remain
  excluded. Funding and employment/availability remain unestablished.

`required_terms` can contain comma-separated terms already in the topic, for example
`brain, tumor`. All normalized required terms must occur in one contribution's
evidence. This is token matching, not exact phrase matching or a clinical/domain
ontology. Leave it empty for the smoke evaluation.

## 4. Important limitations and review

English lexical screening trades recall for transparency. Synonyms, abbreviations,
unseen spellings, implicit methods, relationships and scientific negation may be
missed or misread. Abstract excerpts are incomplete and source topics can be noisy.
An unrecognized topic term stops the query conservatively, even if the underlying
idea is present under another name. Broad-word suppression also means a topic such
as “machine learning” needs an application area. The whole query is not parsed as
a logical/scientific proposition.

Inspect publication titles and source links; term occurrence alone is not an expert
assessment. No free-text source is executed as instructions and there is no LLM
explanation. Queries go to the existing Databricks embedding service only when the
gate permits retrieval; avoid entering confidential proposal details in this pilot.

Use [the manual evaluation template](matching_evaluation_review.md) to label
acceptable candidates and evidence, including negatives and paraphrases. Separate
familiar smoke prompts from held-out prompts. Only then decide whether to adjust
the policy, add semantic publication reranking, or build the UI. No saved-search
or feedback state is stored yet; if added later it belongs in Lakebase, not here.

## 5. Local checks and recovery

```sh
python3 -m unittest discover -s tests -q
python3 scripts/validate_matching_local.py
```

The replay reconstructs the existing Silver/Gold evidence using the offline fixture
of seven confirmed identities and supplies all seven profiles in a fixed synthetic
order. It tests evidence rules, **not live embeddings or AI Search rankings**.
It makes no source downloads, approvals or Delta writes.

Local checkpoint: all 172 tests passed, and the full-collection replay completed
for all 12 smoke prompts. Astronomy and the unsupported mixed-domain topic returned
insufficient evidence; the broad-method and negated topics requested refinement.
The medical-imaging result's first evidence directly names brain tumours in its
title. These are observed policy behaviours, not human-certified relevance or
results from live notebook 13 execution. Partial and source-topic-only matches
still need careful review.

Notebook 13 mirrors only the needed read/verify helpers from notebook 12 so a fresh
Workspace import works independently. Tests compare their syntax trees to prevent
drift. Source/index mismatch, missing evidence columns, stale builds or permission
failures stop execution. Do not remove those checks to get a result. If a legitimate
Gold refresh occurs, complete the existing refresh/sync workflow separately, then
rerun validation. This notebook never repairs, replaces or syncs resources itself.
