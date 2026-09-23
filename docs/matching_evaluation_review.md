# Matching pilot — human review template

All rows are **unreviewed**. They are not labelled ground truth. Before examining
the algorithm's ranking, use the seven profiles' eligible publication evidence to
record acceptable candidates (or no supported candidate), specific supporting
publication IDs, missing evidence and ambiguity. Names alone are not sufficient.

Notebook 14's offline replay includes H01–H12 as a frozen-policy observation set,
not as automatic labels. No relevance tuning is performed against those outputs.
If they are later used to change the policy, reserve a new unseen test set. Review
full and partial candidate lists separately and count false abstentions as well as
unsupported recommendations.

Use `supported`, `partial`, `insufficient`, or `refine` for the human judgement.
For scientific relevance, consult a qualified reviewer where necessary. Mark a
topic uncertain when the collected data cannot support a judgement.

The first eight prompts overlap with development and must not be advertised as a
held-out evaluation. Reserve the remaining prompts from weight tuning if you want
an honest follow-up test. Suggested negatives are not automatically true negatives:
inspect the actual evidence before labelling them.

| ID | Split | Research topic | Human judgement | Acceptable researchers + evidence IDs | Reviewer / notes |
|---|---|---|---|---|---|
| D01 | Development | Blockchain applications for information security | Unreviewed | | |
| D02 | Development | Digital forensics and malware detection | Unreviewed | | |
| D03 | Development | Machine learning for brain tumor detection in medical images | Unreviewed | | |
| D04 | Development | Technology adoption and organizational employee performance | Unreviewed | | |
| D05 | Development | Traffic prediction and transportation planning for autonomous vehicles | Unreviewed | | |
| D06 | Development | Higher education employability and career development | Unreviewed | | |
| D07 | Development | Reflective teaching practices and learning in higher education | Unreviewed | | |
| D08 | Development | Exoplanet atmospheric spectroscopy and stellar nucleosynthesis | Unreviewed | | |
| H01 | Held-out proposal | Detecting phishing messages in email | Unreviewed | | |
| H02 | Held-out proposal | Recovering digital evidence from mobile applications | Unreviewed | | |
| H03 | Held-out proposal | Classifying malicious software from runtime behavior | Unreviewed | | |
| H04 | Held-out proposal | RFID implementation in hospitals | Unreviewed | | |
| H05 | Held-out proposal | Trust and willingness to adopt connected home devices | Unreviewed | | |
| H06 | Held-out proposal | Road congestion forecasting with connected vehicles | Unreviewed | | |
| H07 | Held-out proposal | Electronic portfolios for graduate career readiness | Unreviewed | | |
| H08 | Held-out proposal | Reflective assessment in computing courses | Unreviewed | | |
| H09 | Held-out proposal | Coral reef restoration and marine biodiversity | Unreviewed | | |
| H10 | Held-out proposal | Gene editing in drought-resistant wheat | Unreviewed | | |
| H11 | Held-out proposal | Machine learning for stellar spectroscopy | Unreviewed | | |
| H12 | Held-out proposal | Artificial intelligence | Unreviewed | | |

For each run record `policy_version`, Gold/Silver build IDs, retrieval type, query,
required terms and top-k. Compare candidate inclusion, ordering, citation relevance,
partial coverage, incorrect recommendations and false abstentions separately.
Do not interpret a search or heuristic score as confidence, and do not tune an
abstention threshold from the single astronomy example. Labels and thresholds
require more than passing execution tests.
