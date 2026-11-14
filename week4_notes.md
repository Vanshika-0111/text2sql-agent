# Week 4 notes — Schema-aware RAG + few-shot retrieval

## Day 23 — Baseline (3 repeats) — 2026-11-07

**Agent:** `agent.py` as of Day 20 (schema linking via LLM table picker, 12 prompt rules,
4 fixed few-shot examples, SQLGlot safety check, fix loop with MAX_FIXES 2, temperature 0, EXPLAIN=False).
Model: gemini-3.5-flash-lite.

### Changes made today
- **Q7 reworded** (tuned set; backup `test_questions_backup_day23.json`, script `fix_q7.py`):
  "Which payment type is used most often?" → "Which payment type appears in the most payment
  records, and how many payment records does it have?" Gold SQL unchanged (`COUNT(*)` over
  `order_payments`). Reason: the old wording was ambiguous (payment rows vs distinct orders);
  this fixes the question, not the model. Held-out set untouched.
- **`agent.py`**: `DAY` / `REPEATS` / `QUESTION_IDS` settings; one results file per repeat
  (`agent_{DAY}_{SET}_rep{N}_results.json`), resumable per repeat; `rep` field in the run log.
  Old version kept as `agent_day20.py`.
- **`lenient_score.py`**: `score()` also reports strict and returns its numbers;
  new `score_repeats(prefix, reps)` prints mean, range, and always-failing vs flaky questions.
  Old version kept as `lenient_score_day22.py`.

### Accuracy (mean, range over 3 repeats)
| Set | Strict | Lenient | Subset (main) |
|---|---|---|---|
| Tuned (60) | 52.0 (51–53) | 53.0 (52–54) | **59.7 (59–60)** |
| Held-out (20) | 16.0 (16–16) | 18.0 (18–18) | **18.0 (18–18)** |

- Day 19 single run for comparison: tuned 50 / 51 / 58, held-out 16 / 18 / 18.
- **Q7 now passes in all 3 repeats** (strict too).
- Tuned subset: only **Q56** flaky (failed 1 of 3; NULL first-month row convention).
- Tuned strict-only flips: Q31, Q45, Q51, Q58. Always fail strict but pass subset (shape only):
  Q43, Q50, Q52, Q54, Q60.
- Held-out: Q114, Q119 fail subset every repeat; Q108, Q117 fail strict only. No flaky questions.

### Cost and latency (from `log_summary.py`)
| Set | Gemini calls / question | Tokens / question | Avg seconds / question | Fixes |
|---|---|---|---|---|
| Tuned (180 question-runs) | 2.03 | ~3,120 | ~12.9 | 4 total |
| Held-out (60 question-runs) | 2.00 | ~2,970 | ~21.1 | 0 |

Per call type: `pick_tables` ~1,040 prompt + 14 output tokens; `write_sql` ~1,890 prompt + 91 output;
`fix_sql` ~2,040 prompt + 140 output. The table picker is about **35% of tokens per question**.

Run ids: held-out `20261107_121924`; tuned `20261107_125044` (stopped at Q23, 1 crash, resumed)
+ `20261107_130011`; switch test `20261107_115133` (day23_test files, not part of the baseline).

### What this means for Week 4
- Tuned subset is near its ceiling (59.7/60): RAG gains won't show there. Judge RAG on
  **held-out subset**, **strict/lenient**, and **tokens per question**.
- Tuned strict varies by ±1 between repeats, held-out by 0. A change counts only if it moves
  the mean outside the baseline range.
- If retrieval over table descriptions (Day 26) can replace the LLM table picker, that removes
  one Gemini call and ~1,050 tokens per question.


## Day 24 — Example bank + overlap check

### Built
- example_bank.json: 40 hand-written, verified examples, ids 201–240
  - 201–210 easy, 211–220 medium, 221–230 advanced, 231–240 conventions (one per prompt rule)
  - fields: id, question, sql, difficulty, tags
- check_examples.py (old version kept as check_examples_day12.py): runs every example (4 fixed + bank)
  on Postgres, reports SQL errors and 0-row results, and flags overlap with all 80 gold questions:
  - SAME SQL: equal after SQLGlot normalisation
  - SAME RESULT: same rows as a gold query (numbers rounded to 2 dp, column order ignored)
  - SIMILAR WORDING: difflib ratio >= 0.65
- Final check: 44 examples, 0 SQL errors, 0 empty results, no SAME SQL / SAME RESULT flags.

### Leak found and fixed
- Fixed few-shot example 3 (top seller by revenue in SP/RJ/MG) was the same question as held-out Q118.
  Wording similarity was only 0.48, so the Day 12 check missed it; the new result check caught it.
- Replaced with "top seller by items sold in 3 categories" (same ROW_NUMBER skill);
  old file kept as few_shot_examples_day12.py.
- Effect on past scores probably none: Q118 passed on Day 11, before any few-shot examples.
- Held-out re-baselined with the clean examples (agent_day24_heldout, 3 reps):
  strict 16/20, lenient 18/20, subset 18/20 in all 3 reps (range 0) — identical to Day 23.
  Subset fails Q114, Q119 every rep; Q108, Q117 fail strict only. Q118 still passes without the leaked example.
  This is the held-out baseline for Day 27.

### Overlap policy
- Near-copy of a held-out question (same question with a value or filter changed) -> rewrite.
  Rewritten: 201, 202, 206 (Q101/Q113/Q102), 224 (Q117), 240 (Q111).
- Tuned question, different result -> keep (tuned set was already used to write the prompt rules),
  unless the example teaches nothing beyond the test question itself.
  Rewritten: 218 (Q25, 0.92), 223 (Q34, missed by the checker, see below).
- Wording flags that are false positives (different question, shared phrasing) -> keep: 218 vs Q115, 233 vs Q101.
- Deliberately avoided: monthly 2018 change (Q117), top-1 per group (Q118), delivery duration by state (Q114),
  counting reviews vs orders (Q119), category weights (Q120).

### Things to watch
- Example 234 shows prompt rule 4 (durations in days, purchase -> approval). Held-out Q114 fails on that rule,
  so if Q114 starts passing after Day 27, this example is the likely reason.
- Checker limitation: a text column (e.g. band labels) stops SAME RESULT from matching, and wording can differ a lot.
  223 vs Q34 (price bands) was only found by reading. Day 25: add an embedding-similarity check.
- Week 6: run the overlap check the other way too, on the 70 new benchmark questions against the bank.

### Interview line
"My overlap check compared only wording. I added result-equality and normalised-SQL checks, and the result check
found a held-out question hiding in my few-shot prompt, worded differently enough to pass a text-similarity check."


## Day 25 — Embeddings, retriever, overlap check, variant-set finding

### Built
- Model: gemini-embedding-001, task_type SEMANTIC_SIMILARITY, question text only, dim 3072.
- embed_bank.py -> example_embeddings.json (40); embed_questions.py -> question_embeddings.json (80).
  Both resumable; re-embed only when the text changes.
- retriever.py: question_vector() (cache first, API only if not cached) + retrieve(vec, k=3), cosine similarity.
  Offline: matches the question frame more than the SQL pattern (Q114 -> 230, not duration example 234).
  Not tuned for (Q114 is in the variant set); conventions were left to Day 26 rule retrieval.
- list_questions.py: prints all 120 question texts (80 test + 40 bank) for reading.

### Embedding overlap check
- embed_overlap.py: 40 x 80 = 3,200 example-vs-question pairs. Scores are bunched: median 0.80, p99 0.906.
- Threshold 0.92 = review list. Real near-copies all >= 0.93.
- Misses label-hidden cases: 223/Q34 was only 0.876 -> the Day 24 result check stays.
- Rewrote 203 (≈Q103) and 208 (≈Q104/Q9) via rewrite_examples_day25.py (backup example_bank_backup_day25.json):
  now a review-comment NULL count and a geolocation top-5 states by zip prefix.
  The first rewrite attempt landed on Q111/Q120 and was caught by both checks.
- embed_overlap.REVIEWED holds the 11 accepted pairs. check_examples.py now ends with embedding_report().
  Final check: 0 errors, 0 empty, 11 pairs >= 0.92, all reviewed, 0 new.
- Day 27 watch list (tuned near-copy examples kept on purpose): Q51 (ex 222), Q28 (ex 212).

### Finding — the "held-out" set is mostly variants of tuned questions
- compare_sets.py (held-out vs tuned: embedding similarity + same text + same SQL):
  - Q104 = Q9 word for word.
  - Paraphrases: Q115≈Q22, Q116≈Q54, Q106≈Q27.
  - One parameter changed from a tuned question: Q108, Q113, Q118, Q114, Q117, Q109, Q105.
  - 12 of 20 pairs >= 0.92. Only Q101, 102, 103, 107, 110, 111, 119, 120 are reasonably distinct.
- **Decision:** keep the 20 as the **variant set** (tests robustness to rewording; the Day 23/24 baselines stay valid)
  and build a **fresh held-out set** in Week 6. From here on, "held-out" in these notes means the variant set
  (file names still say `heldout`).

### Interview line
"Before trusting my held-out number I compared it with the tuned set. 12 of 20 held-out questions were
paraphrases or one-parameter variants of tuned ones, so I relabelled it a robustness set and planned a
separate held-out set, checked for overlap before any run."


## Day 26 — Schema and rule retrieval (offline, tuned set only)

21 Gemini calls (embeddings only). No agent runs.

### Built
- embed_schema.py -> schema_embeddings.json: 9 table chunks (from schema_parts.load_schema_parts())
  + 12 rule chunks (from prompt_rules.txt); keys `table:<name>` / `rule:<n>`; same model and task type;
  resumable. table_chunks() / rule_chunks() / all_chunks() importable.

### Tables — can embedding retrieval replace the LLM table picker?
check_table_retrieval.py (-> table_retrieval_day26.json) and check_table_hybrid.py
(FK graph + smallest connected superset, "closure"). Gold tables average 1.78 per question.

| Method | All gold tables covered | Avg tables sent |
|---|---|---|
| LLM picker (linking_check_v2) | **60/60** | **2.02** |
| Embedding top-3 | 41/60 | 3.00 |
| Top-k + orders + closure, k=2 | 51/60 | 3.02 |
| Top-k + orders + closure, k=3 | 57/60 | 3.73 |
| Top-k + orders + closure, k=4 | 58/60 | 4.43 |

- Misses: `orders` first (hub table, never named in questions; closure fixes it), then vocabulary gaps
  closure can't fix: "revenue" -> order_items, "spent" -> order_payments (that knowledge lives in rule 7),
  state codes -> customers.
- `sellers` ranks top-1 on 11 questions (short, generic description).
- **Decision: keep the LLM picker.** Embedding retrieval can't replace it (needs ~2x the tables and still
  misses 3) and can't add recall (the picker misses 0). The Day 23 hope of saving the picker call is dropped.

### Rules — can retrieval trim the 12 prompt rules?
- label_rules.py -> rule_labels_day26.json: rules each tuned question needs, from gold-SQL patterns.
  Rules 1 and 9 always apply; rule 6 needed by 32/60, rule 12 by 24/60.
- check_rule_retrieval.py: needed rules median similarity 0.784 vs 0.729 for not-needed.
  Rules 3, 7, 8 rank 1st almost always. Rule 10 (quoted labels) ranks 4–8 (it is about form, not topic).
  Rule 4 (durations, mostly a SQL formula) ranks 3–6.
- check_rule_hybrid.py: always-on {1, 6, 9, 12} + regex for rule 10 + top-k of rules 2, 3, 4, 5, 7, 8, 11.

| Rule set | Questions with all needed rules | Rule tokens saved (of ~320) |
|---|---|---|
| All 12 rules (current) | 60/60 | 0 |
| Hybrid, k=4 | 58/60 | ~104 |
| Hybrid, k=3 | 57/60 | ~129 (~4% of a question's tokens) |

- k=3 misses rule 4 on Q24, Q52, Q60 (+ rule 2 on Q52).
- **Decision: keep all 12 rules in the prompt.** The saving is too small for the risk to duration questions.

### Consequence for the CV item "schema-aware RAG"
Built on Day 27 as **schema-aware example retrieval**: examples ranked by question similarity *and* by
overlap between the example's tables and the tables the picker linked. This targets the Day 25 weakness
(retrieval follows wording, not SQL pattern).

### Interview line
"I benchmarked embedding retrieval over schema chunks against LLM schema linking: 57/60 vs 60/60 table recall,
with almost twice the tables in the prompt. I kept the LLM linker and used the linked tables to steer example
retrieval instead."


## Day 27 — Schema-aware example retrieval — 2026-11-08

### Choosing w (offline, tuned set only)
- tag_examples.py -> example_tables_day27.json: tables per bank example + gold tables per tuned question.
  About 2 exact-table-match examples per tuned question; Q32, Q39, Q50, Q59 have none (so top-3 exact
  match is out of 56).
- check_example_retrieval.py -> example_retrieval_day27.json:
  score = cosine + w · Jaccard(example tables, question tables).
- Independent check: Jaccard of SQLGlot pattern features (window, CTE, CASE, HAVING, GROUP BY, top-N,
  COUNT DISTINCT, subquery, ratio, COALESCE, date part/cast). Needed because the table metric rises with w
  by construction.

| w | Top-1 exact tables | Exact in top-3 (of 56) | Pattern J@3 | Cosine@3 | Top-3 changed |
|---|---|---|---|---|---|
| 0 | 21/60 | 41 | 0.368 | 0.879 | 0 |
| **0.05** | **40/60** | **50** | **0.342** | **0.876** | **28** |
| 0.1 | 49/60 | 54 | 0.323 | 0.870 | 43 |
| 0.3 | 56/60 | 56 | 0.296 | 0.863 | 47 |

- **w = 0.05** (the knee): top-1 schema match 21 -> 40 for 0.003 cosine. Higher w trades SQL-pattern
  similarity for table matching; table-matching examples tend to have a *different* SQL shape.

### Code
- retriever.py: W_TABLES = 0.05; example table tags computed from the bank SQL at load (never stale);
  retrieve_schema_aware(vec, tables, k, w). Offline check: 60/60 identical to the grid; w=0 = plain retrieve().
- run_eval.py: format_examples(); ask_gemini / fix_sql take optional examples_text
  (None -> the 4 fixed examples, old behaviour unchanged).
- agent.py (Day 24 version kept as agent_day24.py): `retrieve` node after pick_tables (no API call for cached
  questions); EXAMPLE_MODE "schema_aware" / "fixed"; K_EXAMPLES = 3; example ids + scores saved per result,
  example_ids in the run log. All 12 rules stay.
- At run time the picker's tables are used, not gold tables (the picker sometimes adds one, e.g. Q28),
  so a few rankings differ slightly from the offline grid.

### Results (3 reps; agent_day27_{tuned,heldout}_rep1-3_results.json)
| | Baseline (4 fixed examples) | Day 27 (3 schema-aware examples) |
|---|---|---|
| Tuned strict | 52.0 (51–53) | 50.3 (50–51) |
| Tuned subset | 59.7 (59–60) | 58.7 (58–59) |
| Variant strict | 16.0 (16–16) | 15.0 (15–15) |
| Variant subset | 18.0 (18–18) | 18.0 (18–18) |
| Tokens / question | ~3,000–3,100 | ~2,820 (−7–9%) |

Baselines: tuned = Day 23, variant = Day 24 re-baseline (clean examples).

### What changed — output-shape copying, not logic errors (check_copying.py)
- **Q18**: copies example 211's `DATE_TRUNC(…) GROUP BY 1` pattern with 'year' -> returns `2016-01-01`
  instead of `2016`. Fails subset in all 3 tuned reps = the whole tuned subset drop.
- **Q9 / Q104** (same question): adds a `customer_state` column. State-centric examples (213, 230) prime it;
  no example groups by city + state. Still passes subset; Q104 = the whole variant strict drop.
- Q33 dropped the count column in 2 reps.
- Now passing: Q31 (failed 2 Day 23 reps) and Q56.
- Q51 passes all reps but has near-copy example 222 -> discounted.
- **Q114**: duration example 234 still not retrieved (gets 230, 226, 232) -> still fails. Retrieval follows
  wording and tables, not conventions.
- Unchanged: Q119, Q114, and the strict-fail group 43, 45, 50, 52, 54, 58, 60. Retrieval fixed none of the
  known convention failures.

### Decision
Keep schema-aware retrieval: neutral on variant subset, −1 question on tuned subset, fewer tokens.
No tuning of w or of the examples against Q18/Q9: that would be fitting the test set. Output shape is a
Week 5 query-planning problem.

### Bug found and fixed
- Gemini sometimes fences SQL with ```postgresql. clean_sql only stripped ```sql, leaving a `postgresql`
  line -> safety parse error (Q14, Q21 tuned rep 3; Q108 variant rep 1). The fix loop recovered all 3,
  so scores were not affected, but those runs spent an extra call.
- clean_sql fixed after the Day 27 runs; test_clean_sql.py all pass. Applies from Day 28 on.

### Interview line
"Schema-aware example retrieval cut prompt tokens 7–9% at equal variant-set accuracy. It didn't fix
convention errors and introduced output-shape copying (DATE_TRUNC year, extra columns), which motivated
query planning."


## Week 4 summary

### Baseline vs end of Week 4 (mean, range over 3 reps)
| Metric | Day 23/24 baseline | Day 27 (end of Week 4) | Change |
|---|---|---|---|
| Tuned strict (60) | 52.0 (51–53) | 50.3 (50–51) | −1.7 |
| Tuned subset (60) | 59.7 (59–60) | 58.7 (58–59) | −1.0 (Q18) |
| Variant strict (20) | 16.0 (16–16) | 15.0 (15–15) | −1.0 (Q104) |
| Variant subset (20) | 18.0 (18–18) | 18.0 (18–18) | 0 |
| Tokens / question | ~3,000–3,100 | ~2,820 | −7–9% |
| Few-shot examples in prompt | 4 fixed | top-3 retrieved of 40 | — |
| Rules in prompt | 12 | 12 | — |
| Schema linking | LLM picker | LLM picker | kept (beat embeddings 60/60 vs 57/60) |

### What Week 4 built
- 40-example verified bank with a three-way overlap check (normalised SQL, result equality, embeddings).
- Embedding cache + retriever (gemini-embedding-001), resumable.
- Schema/rule embedding retrieval, benchmarked and rejected with numbers.
- Schema-aware example retrieval in the LangGraph agent (`retrieve` node).
- One leak fixed (example 3 = Q118), one dataset problem found (held-out = variants -> variant set).

### Honest read
Accuracy did not go up in Week 4. What it delivered is a cheaper prompt, a clean and checked example bank,
an evidence-based design choice (LLM linker over embedding linking), and a clear diagnosis of what is left:
convention and output-shape errors, which retrieval does not fix.

### CV wording (what is true now)
- "Implemented schema-aware RAG: few-shot examples retrieved by question-embedding similarity plus overlap
  with LLM-linked tables (gemini-embedding-001), cutting prompt tokens 7–9% at equal accuracy."
- "Benchmarked embedding-based schema retrieval against LLM schema linking (57/60 vs 60/60 table recall)
  and kept the LLM linker."
- "Built a leakage check for few-shot examples (normalised-SQL, result-equality and embedding similarity)
  that caught a test question hidden in the prompt."
- Still to earn: query planning, database tools (Week 5); 150 questions, >80% execution accuracy,
  LLMOps report (Week 6).

### Interview lines (one per day, short form)
- Day 24: result-equality check found a held-out question hidden in the few-shot prompt.
- Day 25: held-out set was 12/20 variants of tuned questions -> relabelled, fresh held-out planned.
- Day 26: embedding schema retrieval 57/60 vs LLM linker 60/60 -> kept the LLM linker.
- Day 27: retrieval saved 7–9% tokens, no accuracy gain, revealed output-shape copying -> query planning.


## Carried into Week 5 (query planning + database tools)
- **Main remaining error type = output shape**: column set (extra `customer_state` in Q9/Q104, missing count
  in Q33) and value types (Q18 year as `2016-01-01` vs `2016`). The plan step should state the expected
  output columns and their types before SQL is written.
- **Convention failures retrieval didn't touch**: Q114 (rule 4 duration), Q119 (reviews vs orders), strict
  group 43, 45, 50, 52, 54, 58, 60. Check whether a plan step (or a database tool to inspect values) helps.
- **Keep from Week 4**: LLM picker, all 12 rules, schema-aware retrieval with w = 0.05, K_EXAMPLES = 3.
  Don't tune w or the bank against specific failing questions.
- **Watch**: Q51 (near-copy 222, discounted), Q28 (near-copy 212), Q18 and Q9/Q104 copying.
- **Cost**: ~2,820 tokens and ~2 calls per question; the picker is still ~35% of tokens. A planning step
  adds a call, so report tokens and latency next to accuracy.
- **Process**: 3 reps per eval, report mean and range; measure on tuned + variant; first Day 28+ runs are
  the first with the clean_sql fence fix (expect fewer fix-loop calls).
- **Week 6**: fresh held-out set (20+ of the ~70 new questions), each checked with compare_sets.py and
  embed_overlap.py before any run; final report gives tuned, variant and fresh held-out separately.


## Correction (Day 29, 2026-11-08) — variant-set gold fixes

A rule audit of all 80 gold queries (audit_gold_day29.py) found 3 variant golds that broke the documented rules:
Q108 and Q117 used TO_CHAR(..., 'YYYY-MM') for months (rule 5), and Q114 used ::date subtraction for a duration (rule 4).
All three were fixed (fix_gold_day29.py; backup heldout_questions_backup_day29.json; log gold_fixes_day29.json).
Saved runs were re-scored offline from their saved SQL (rescore_day29.py, sync_gold_sql_day29.py; originals in backup_day29_*).
The agent's SQL was correct for all three questions in all 9 variant runs.

Corrected variant-set numbers, mean (range) over 3 runs:

| Run | Strict | Lenient | Subset |
|---|---|---|---|
| Day 23 baseline | 19.0 (19–19), was 16.0 | 19.0 | 19.0, was 18.0 |
| Day 24 (leak fixed) | 19.0 (19–19), was 16.0 | 19.0 | 19.0 |
| Day 27 schema-aware | 18.0 (18–18), was 15.0 | 18.0 | 19.0, was 18.0 |

Remaining variant failures: Q119 (counts reviews, but the question asks about orders) in all runs, and Q104 (extra customer_state column) in Day 27 strict only.

Corrections to earlier sections:
- "Q114 still fails / retrieval fixed no convention failures" is wrong: Q114 was a gold error.
- The tuned strict group 43, 45, 50, 52, 54, 58, 60 consists of output-shape differences (missing supporting columns, column order), not logic errors.
- The Day 27 decision is unchanged: −1 variant strict (Q104), equal variant subset, −7–9% tokens.

Run logs keep the old `correct` values for these 9 runs, so accuracy should be taken from the results files.