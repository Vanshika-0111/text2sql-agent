# Week 3 — Rebuilding the pipeline as a LangGraph agent (Days 15–21)

Model: gemini-3.5-flash-lite, temperature 0 (unchanged from Week 2).
Framework: LangGraph 1.2.19.
Goal: same pipeline as Week 2 (pick_tables -> write SQL -> SQLGlot check -> run on Postgres ->
self-correction, max 2 fixes), rebuilt as a graph, with no loss in accuracy.
Evaluation: same tuned set (60 questions) and held-out set (20, ids 101–120), scored with
lenient_score.py. Main metric = subset. Eval runs use EXPLAIN = False (no answer node).

## The graph

State: one TypedDict (AgentState) with question, tables, first_sql, sql, safe, error,
result, fixes. Each node returns only the fields it changes.

Nodes (thin wrappers around the Week 2 functions, no logic rewritten):
- pick_tables — Gemini chooses the relevant tables (schema linking, Week 2 Day 13).
- write_sql — builds the schema for those tables, Gemini writes the query
  (12 rules + 4 few-shot). Saves it as both first_sql and sql.
- safety_check — SQLGlot gate (check_sql): exactly one read-only SELECT, otherwise blocked.
- run_sql — executes on Postgres, stores rows or the error (cut to 300 chars).
- fix_sql — Gemini gets the question, the failed SQL and the error, returns a new query;
  fixes + 1.
- answer (Day 18) — Gemini explains the result in plain English, only when EXPLAIN = True.
- cannot_answer (Day 18) — fixed message when the fix budget runs out.

Edges:
- Straight line: START -> pick_tables -> write_sql -> safety_check.
- route_after_safety: safe -> run_sql; blocked and fixes < 2 -> fix_sql; otherwise give up.
- route_after_run: no error -> done; error and fixes < 2 -> fix_sql; otherwise give up.
- fix_sql -> safety_check: the loop-back edge, so every fixed query is safety-checked again.
- done -> answer (EXPLAIN = True) or END; give up -> cannot_answer -> END.
- The self-correction loop is conditional edges in the graph, not a Python while-loop.
  The fix count lives in the state.

Scoring happens outside the graph: the agent never sees the gold SQL.

Reliability: RetryPolicy on the 4 Gemini nodes (pick_tables, write_sql, fix_sql, answer),
retrying only on 503, with waits of 30s then 60s.

Diagram: agent_graph.png (rendered from the compiled graph).

## What was built each day
- Day 15 — LangGraph basics with toy graphs, no Gemini:
  day15_toy_graph.py (straight line), day15_branch.py (conditional edge),
  day15_loop.py (loop with a counter in the state).
- Day 16 — straight-line agent (agent_day16.py): the Week 2 functions wrapped as nodes,
  pick_tables -> write_sql -> safety_check -> run_sql, no self-correction yet.
  Q34 failed with a Postgres alias error, which became the first test case for Day 17.
- Day 17 — self-correction as graph edges (agent_day17.py): two routers
  (route_after_safety, route_after_run) and the fix_sql -> safety_check loop-back,
  max 2 fixes. test_routing.py tests the routers without calling Gemini.
  agent_graph.png drawn from the compiled graph. Results saved after every question,
  so a crashed run resumes where it stopped.   Checked on 5 questions (Q1, 15, 25, 34, 45): 4/5 strict, Q34 correct after 2 fixes.
- Day 18 — endings and reliability: answer node (explain.py, sees the first 20 rows +
  total row count) and cannot_answer node, EXPLAIN flag to switch the answer node on/off,
  RetryPolicy on the 4 Gemini nodes (503 only, 30s then 60s). Found that a truncated
  result made Gemini claim a false "sharpest drop" in its explanation; fixed with a
  prompt rule. Tests: test_routing.py (31 checks), test_explain_off.py (fake Gemini and
  Postgres, checks EXPLAIN = False behaves like Day 17).
- Day 19 — full evaluation (EXPLAIN = False) on both sets via a SET switch
  (tuned / heldout), results in agent_day19_{SET}_results.json. Found and fixed the
  EXPLAIN bug (see Findings). Result: agent matches Week 2.
- Day 20 — per-run logging (LLMOps basics):
  - run_log.py: one JSON line per question in logs/agent_runs.jsonl (run_id, set, qid,
    status answered / gave_up / crashed / interrupted, correct, fixes, node path,
    seconds per node, total seconds, error).
  - llm_usage.py: record_usage after every Gemini call -> calls and prompt / output /
    thinking tokens per question.
  - Crashes and Ctrl+C are logged, then re-raised, so the run stays resumable.
  - log_summary.py: per-run table (counts, tokens, avg seconds, gave_up / crashed ids)
    and per-call-type token table.
- Day 21 — these notes.

## Agent vs Week 2
Same model, prompts, few-shot examples, safety gate and fix limit; only the orchestration
changed (Python script -> LangGraph graph). Week 2 = Day 13/14 final. Agent = Day 19 run,
EXPLAIN = False.

| Set | Pipeline | Strict | Lenient | Subset | Subset fails |
|---|---|---|---|---|---|
| Tuned (60) | Week 2 | 50/60 | 51/60 | 59/60 | Q56 |
| Tuned (60) | Agent | 50/60 | 51/60 | 58/60 | Q7, Q56 |
| Held-out (20) | Week 2 | 16/20 | 18/20 | 18/20 | Q114, Q119 |
| Held-out (20) | Agent | 16/20 | 18/20 | 18/20 | Q114, Q119 |

- Strict and lenient are identical on both sets; held-out is identical on all three metrics,
  with the same two failures.
- The one subset difference (Q7) is a scoring artifact, not worse SQL: same query logic
  as Week 2, but the agent also returned a count column that differs from gold
  (see Findings).
- Q33 changed shape too (dropped the count column) but still passes subset.
- Conclusion: the rebuild kept Week 2 accuracy. Honest numbers to quote: 98% tuned subset
  in Week 2, 90% held-out subset for both.

## Findings
### 1. Temperature 0 is not deterministic (Q34)
- Q34 across runs: Day 17 needed 2 fixes, Day 18 1, Day 19 2, Day 20 gave up after 2 fixes.
  Same prompt, same model, temperature 0.
- So single-run scores carry noise of about ±1 question. A 1-question difference between
  two pipelines is not evidence that one is better; repeated runs would be needed for that.
- Not tuning prompts for Q34: chasing one flaky question risks overfitting to noise.

### 2. Q7 is a scoring artifact, not a regression
- The question is ambiguous about what to count. Agent and Week 2 both use COUNT(DISTINCT order_id)
  and both give the right answer (credit_card). Gold counts payment rows instead.
- This time the agent also returned its count column, which doesn't match gold's count,
  so subset fails. Week 2 happened to return only the payment type.
- Lesson: a result-comparison metric punishes extra columns on ambiguous questions. The
  fix belongs in the question/gold (make the wording precise), not in the prompt.

### 3. The EXPLAIN bug: configuration has to live in the state
- The EXPLAIN flag was never put into the start state, so the router never saw it and the
  answer node ran on every question, even in eval runs.
- Accuracy was unaffected (scoring uses the SQL result), but every question paid for an
  extra Gemini call: more tokens, more time, more exposure to 503s.
- Lesson: in LangGraph, nodes and routers only see the state (or the run config). A global
  flag that isn't passed in is silently ignored. Day 20's per-question node path would
  make this visible immediately (answer appearing in an EXPLAIN = False run).

### 4. Handling 503 "high demand" errors
- RetryPolicy on the Gemini nodes retries only on 503 (30s, then 60s). Other errors fail
  fast, so real bugs aren't hidden by retries.
- Some 503 bursts outlasted both retries and crashed the run. Because results are saved
  after every question, a rerun skips finished questions, so the crashes cost time but
  no work.
- Since Day 20, crashes are logged with status "crashed" before being re-raised, so they
  show up in log_summary.py instead of only in the terminal.

### 5. Token costs (from the Day 20 logs)
- Simple question (no fixes): 2 Gemini calls, ~2,600 tokens.
- 5-question run: 12 calls, 18,916 tokens, ~3,800 tokens per question.
- Prompt tokens are ~97–99% of the total; output is small and thinking tokens are 0.
- Avg prompt tokens per call: pick_tables ~1,040, write_sql ~1,790, fix_sql ~1,920.
  Each fix adds ~2,000 tokens, so failing questions cost far more than easy ones.
- So cost is driven by prompt size (schema + 12 rules + 4 few-shot), not by the answers.
  The cheapest savings would come from shortening or caching the fixed part of the prompt.
- Caveat: logged node seconds include the PAUSE sleep, so they overstate real latency.

### 6. Truncated results can make the explanation wrong (Day 18)
- The answer node saw a truncated result and claimed a "sharpest drop" that wasn't
  actually the sharpest in the full data.
- Fixed with a prompt rule. Lesson: when the LLM sees only part of the data, the prompt
  has to say so, or it will state conclusions about the whole.

## Remaining failures
Subset failures in the Day 19 run (same as Week 2 except Q7):

- Q7 (tuned): scoring artifact. The count column the agent added doesn't match gold's
  count. The answer itself (credit_card) is right. See Findings #2.
- Q56 (tuned): drops the NULL first-month growth row. A convention choice, carried over
  from Week 2.
- Q114 (held-out): duration convention mismatch, carried over from Week 2.
- Q119 (held-out): counts reviews instead of orders, carried over from Week 2.

Flaky but currently passing on most runs:
- Q34 (tuned): needs 1–2 fixes and sometimes runs out of fixes (gave up on Day 20).
  See Findings #1.

None of these were caused by the LangGraph rebuild. Q56, Q114 and Q119 are the same
convention/interpretation errors as Week 2, and Q7 is a metric issue.

## What I'd do next
Measurement first (so later changes can be trusted):
- Repeat each eval 3 times and report mean and range instead of one run. Q34 shows a
  single run can move by a question.
- Grow the held-out set beyond 20. At 20 questions, one question is 5 percentage points.
- Fix Q7 at the source: reword the question (or gold) so "count" is unambiguous.
- Time nodes without the PAUSE sleep, so logged seconds show real latency.

Cost:
- Few-shot examples were accuracy-neutral in Week 2. Test removing them and measure the
  token saving on write_sql / fix_sql.
- Use Gemini context caching for the fixed part of the prompt (rules + examples), since
  prompt tokens are ~97–99% of the cost.

Agent features (what LangGraph makes easy that the Week 2 script didn't):
- RAG: retrieve the most similar few-shot examples and only the relevant rules per question,
  instead of sending the same fixed set every time.
- Clarifying questions: when a question is ambiguous (Q7, Q119 style), pause the graph
  (human-in-the-loop interrupt) and ask the user instead of guessing.
- Checkpointer: replace the hand-written resumable JSON with LangGraph's built-in
  checkpointing, which also enables follow-up questions in a conversation.

Product:
- A simple UI (Streamlit or FastAPI) so someone can type a question and see the SQL,
  the table and the explanation.

## Summary for CV / interviews
### CV bullets (Weeks 1–3)
- Built an agentic Text-to-SQL engine over the 9-table Olist e-commerce database
  (PostgreSQL) using LangGraph and the Gemini API: schema linking, SQL generation,
  SQLGlot read-only safety gate, and a self-correction loop that feeds Postgres errors
  back to the LLM.
- Raised execution accuracy from 78% to 98% on a 60-question tuned set through
  business-convention prompt rules and self-correction; 90% on a 20-question held-out set,
  unchanged at every stage (no overfitting).
- Cut the schema in each prompt to ~2 of 9 tables via LLM schema linking; added per-question
  JSONL logging of node path, latency, retries and token usage (~3,800 tokens/question,
  97–99% prompt tokens).

### 30-second version
"I built a natural-language-to-SQL agent on a real e-commerce database. First I made a
proper test set with verified gold SQL, including a held-out set I never tuned on. Then I
improved accuracy step by step: prompt rules for business conventions gave the biggest
jump, and a self-correction loop fixed queries that errored. It reaches 98% on the tuned
set and 90% on held-out. Every query goes through a SQLGlot check, so nothing but a
single read-only SELECT can reach the database. In Week 3 I rebuilt it as a LangGraph
agent with retries and logging, and showed it kept the same accuracy."

### Questions to expect (short answers)
- Why LangGraph instead of a Python script? The loop and branches become explicit,
  testable edges (routers tested offline); per-node retry policies; easy to add nodes like
  an answer step or human-in-the-loop later.
- How do you know it isn't overfitting? A held-out set of 20 never used for tuning;
  stayed at 18/20 through every change. I quote 90%, not 98%.
- How did you measure accuracy? Run gold SQL and agent SQL on Postgres and compare
  results: strict, lenient, and subset (agent result contains gold), subset as main metric.
- What about safety? SQLGlot parses the query: exactly one statement, top-level SELECT,
  no write nodes anywhere, including inside CTEs. Blocked queries go to the fix loop.
- What surprised you? Temperature 0 still isn't deterministic (Q34). A config flag that
  wasn't in the graph state was silently ignored. And a truncated result made the LLM
  invent a conclusion about the full data.
- Where does the cost go? Prompt tokens are ~97–99%; each fix adds ~2,000. Savings come
  from shortening or caching the fixed prompt, not the answers.