import json
import os
import time
from typing import Any, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import RetryPolicy

RETRY_ERRORS = {"ReadError", "ConnectError", "RemoteProtocolError", "ReadTimeout",
                "ConnectTimeout", "WriteError", "PoolTimeout"}


def is_busy(e):
    """Retry "server busy" (503) and dropped connections (Day 33: WinError 10054 / httpx errors).
    Quota errors (429) and everything else are NOT retried."""
    if "429" in str(e):
        return False
    return "503" in str(e) or type(e).__name__ in RETRY_ERRORS or "10054" in str(e)


GEMINI_RETRY = RetryPolicy(max_attempts=3, initial_interval=30.0,
                           backoff_factor=2.0, retry_on=is_busy)

# Reuse Week 2 code (all safe to import: nothing calls Gemini on import)
from link_tables import pick_tables
from schema_parts import build_schema
from run_eval import ask_gemini, fix_sql, format_examples, client, HEADER, TABLES, PAUSE, MAX_FIXES
from sql_safety import check_sql
from compare_results import run_sql, same_result
from explain import explain_result
from run_log import new_run_id, log_question
from llm_usage import reset as reset_usage, summary as usage_summary
from retriever import question_vector, retrieve_schema_aware, W_TABLES   # Day 27
from plan import make_plan, plan_to_text, PLAN_VERSION                   # Day 31
from db_tools import lookup, facts_to_text                               # NEW (Day 33)


SET = "new70"
QUESTIONS_FILE = {"tuned": "test_questions.json",
                  "heldout": "heldout_questions.json",
                  "new70": "new70.json"}[SET]
REPEATS = 3             # Day 33: reps 1-2; Day 34: set to 3 (reps 1-2 are skipped)
QUESTION_IDS = None     # all questions
EXPLAIN = False         # Day 19 eval: skip the answer node

EXAMPLE_MODE = "schema_aware"   # Day 27: "schema_aware" (top-K retrieved) or "fixed" (4 Day 12 examples)
K_EXAMPLES = 3

# "none"  = Day 27 graph
# "plan"  = Day 31 graph (plan columns -> write_sql + shape check); rejected Day 32
# "tools" = Day 33 graph (plan filters -> lookup -> facts -> write_sql; no plan columns, no shape check)
PLAN_MODE = "tools"
DAY = {"none": "day33_noplan", "plan": "day31", "tools": "day33"}[PLAN_MODE]


def results_file(rep):  # Day 23: one results file per repeat
    return f"agent_{DAY}_{SET}_rep{rep}_results.json"


# ---------- STATE ----------
class AgentState(TypedDict):
    question: str
    tables: list
    examples: list          # Day 27: retrieved examples (id, score, cosine, table_overlap)
    examples_text: str      # Day 27: block for the prompt ("" -> fixed examples)
    plan: dict              # Day 31: plan JSON ({} if parse failed)
    plan_ok: bool           # Day 31
    plan_text: str          # Day 31: column plan for write_sql ("" in tools mode)
    lookup: list            # NEW (Day 33): lookup results per filter
    facts_text: str         # NEW (Day 33): "Database facts" block ("" -> no facts section)
    lookup_seconds: float   # NEW (Day 33)
    shape_fixes: int        # Day 31: shape-check fixes used (max 1)
    shape_events: list      # Day 31: one entry per shape_check visit
    sql_before_shape_fix: str   # Day 31
    cols_before_shape_fix: int  # Day 31
    first_sql: str          # Gemini's first attempt (like Week 2)
    sql: str                # current SQL (changes after each fix)
    safe: bool
    error: str
    result: Any
    fixes: int              # loop counter
    answer: str             # final message for the user
    explain: bool           # True = run the answer node


# ---------- HELPERS ----------
def _examples_text(state):
    return state.get("examples_text") or None     # "" or missing -> fixed examples


def _plan_text(state):
    return state.get("plan_text") or None         # "" or missing -> no plan section


def _facts_text(state):
    return state.get("facts_text") or None        # NEW (Day 33): "" or missing -> no facts section


def n_columns(result):
    """Day 31: number of result columns, or None if it can't be told."""
    if result is None:
        return None
    cols = getattr(result, "columns", None)        # pandas DataFrame
    if cols is not None:
        return len(cols)
    if isinstance(result, dict) and "columns" in result:
        return len(result["columns"])
    if isinstance(result, (list, tuple)) and len(result) > 0:
        first = result[0]
        if isinstance(first, (list, tuple, dict)):
            return len(first)
        return 1
    return None


# ---------- NODES ----------
def pick_tables_node(state: AgentState) -> dict:
    return {"tables": pick_tables(state["question"])}


def retrieve_node(state: AgentState) -> dict:
    """Day 27: schema-aware example retrieval (cosine + W_TABLES * table overlap)."""
    if EXAMPLE_MODE == "fixed":
        return {"examples": [], "examples_text": ""}
    vec, source = question_vector(state["question"], client=client)
    top = retrieve_schema_aware(vec, state["tables"], k=K_EXAMPLES)
    info = [{"id": e["id"], "score": round(e["score"], 4), "cosine": round(e["cosine"], 4),
             "table_overlap": round(e["table_overlap"], 2), "vector": source} for e in top]
    return {"examples": info, "examples_text": format_examples(top)}


def plan_node(state: AgentState) -> dict:
    """Day 31: one Gemini call. Parse failure -> plan {}, plan_ok False.
    Day 33: the column plan text is produced only in "plan" mode; in "tools" mode
    the plan is used only for its filters (lookup node)."""
    schema = build_schema(HEADER, TABLES, state["tables"])   # same schema as write_sql
    time.sleep(PAUSE)
    plan, ok = make_plan(state["question"], schema)
    plan_text = plan_to_text(plan) if (ok and PLAN_MODE == "plan") else ""
    return {"plan": plan, "plan_ok": ok, "plan_text": plan_text}


def lookup_node(state: AgentState) -> dict:
    """NEW (Day 33): 0 Gemini calls. Plan filters -> validated database lookups -> facts block.
    Skipped (no facts) when the plan failed to parse."""
    if not state.get("plan_ok"):
        return {"lookup": [], "facts_text": "", "lookup_seconds": 0.0}
    results, secs = lookup((state.get("plan") or {}).get("filters"), state["tables"])
    return {"lookup": results, "facts_text": facts_to_text(results), "lookup_seconds": secs}


def write_sql_node(state: AgentState) -> dict:
    schema = build_schema(HEADER, TABLES, state["tables"])
    time.sleep(PAUSE)
    sql = ask_gemini(state["question"], schema, _examples_text(state),
                     _plan_text(state), _facts_text(state))
    return {"sql": sql, "first_sql": sql}


def safety_check_node(state: AgentState) -> dict:
    ok, reason = check_sql(state["sql"])
    if ok:
        return {"safe": True, "error": ""}
    return {"safe": False, "error": f"Blocked by safety check: {reason}"}


def run_sql_node(state: AgentState) -> dict:
    try:
        return {"result": run_sql(state["sql"]), "error": ""}
    except Exception as e:
        return {"result": None, "error": str(e)[:300]}


def shape_check_node(state: AgentState) -> dict:
    """Day 31 ("plan" mode only): 0 Gemini calls. See agent_day31.py; unchanged."""
    events = list(state.get("shape_events") or [])
    plan_cols = [c["name"] for c in (state.get("plan") or {}).get("output_columns", [])]
    got = n_columns(state.get("result"))
    ev = {"expected": len(plan_cols), "got": got, "fixes_before": state["fixes"]}

    if not state.get("plan_ok") or not plan_cols:
        ev["outcome"] = "skipped_no_plan"
    elif got is None:
        ev["outcome"] = "unknown"
    elif got == len(plan_cols):
        ev["outcome"] = "match" if state.get("shape_fixes", 0) == 0 else "match_after_shape_fix"
    elif state.get("shape_fixes", 0) >= 1:
        ev["outcome"] = "mismatch_after_shape_fix"
    elif state["fixes"] >= MAX_FIXES:
        ev["outcome"] = "shape_mismatch_unfixed"
    else:
        ev["outcome"] = "fired"
        msg = (f"Not a PostgreSQL error: the query ran but returned {got} column(s); "
               f"the query plan requires exactly {len(plan_cols)} column(s), in this order: "
               f"{', '.join(plan_cols)}. Return exactly these columns.")
        events.append(ev)
        return {"error": msg, "shape_fixes": state.get("shape_fixes", 0) + 1,
                "sql_before_shape_fix": state["sql"], "cols_before_shape_fix": got,
                "shape_events": events}

    events.append(ev)
    return {"shape_events": events}


def fix_sql_node(state: AgentState) -> dict:
    schema = build_schema(HEADER, TABLES, state["tables"])  # same schema as write_sql
    time.sleep(PAUSE)
    new_sql = fix_sql(state["question"], state["sql"], state["error"], schema,
                      _examples_text(state), _plan_text(state), _facts_text(state))
    return {"sql": new_sql, "fixes": state["fixes"] + 1}


def answer_node(state: AgentState) -> dict:
    time.sleep(PAUSE)   # free-tier rate limit, same as other Gemini nodes
    return {"answer": explain_result(state["question"], state["sql"], state["result"])}


def cannot_answer_node(state: AgentState) -> dict:
    # No Gemini call: fixed message with the last error
    msg = (
        "Sorry, I could not answer this question. "
        f"The SQL still failed after {state['fixes']} fix attempt(s). "
        f"Last error: {state['error'][:200]}"
    )
    return {"answer": msg}


# ---------- ROUTERS ----------
def route_after_safety(state: AgentState) -> str:
    if state["safe"]:
        return "ok"
    if state["fixes"] < MAX_FIXES:
        return "fix"
    return "give_up"


def route_after_run(state: AgentState) -> str:           # Day 27 router (none + tools modes)
    if state["error"] == "":
        if state.get("explain", True):
            return "done"           # -> answer node
        return "skip_answer"        # -> END (Day 17 behaviour)
    if state["fixes"] < MAX_FIXES:
        return "fix"
    return "give_up"


def route_after_run_plan(state: AgentState) -> str:      # Day 31 ("plan" mode)
    if state["error"] == "":
        return "shape"
    if state["fixes"] < MAX_FIXES:
        return "fix"
    return "give_up"


def route_after_shape(state: AgentState) -> str:         # Day 31 ("plan" mode)
    if state["error"] != "":
        return "fix"
    if state.get("explain", True):
        return "done"
    return "skip_answer"


# ---------- GRAPH ----------
def build_graph(plan_mode):
    """"none" -> Day 27 graph; "plan" -> Day 31 graph; "tools" -> Day 33 graph."""
    builder = StateGraph(AgentState)
    builder.add_node("pick_tables", pick_tables_node, retry_policy=GEMINI_RETRY)
    builder.add_node("retrieve", retrieve_node, retry_policy=GEMINI_RETRY)
    builder.add_node("write_sql", write_sql_node, retry_policy=GEMINI_RETRY)
    builder.add_node("fix_sql", fix_sql_node, retry_policy=GEMINI_RETRY)
    builder.add_node("answer", answer_node, retry_policy=GEMINI_RETRY)
    builder.add_node("safety_check", safety_check_node)
    builder.add_node("run_sql", run_sql_node)
    builder.add_node("cannot_answer", cannot_answer_node)

    builder.add_edge(START, "pick_tables")
    builder.add_edge("pick_tables", "retrieve")

    if plan_mode == "plan":
        builder.add_node("plan", plan_node, retry_policy=GEMINI_RETRY)
        builder.add_node("shape_check", shape_check_node)
        builder.add_edge("retrieve", "plan")
        builder.add_edge("plan", "write_sql")
    elif plan_mode == "tools":
        builder.add_node("plan", plan_node, retry_policy=GEMINI_RETRY)
        builder.add_node("lookup", lookup_node)
        builder.add_edge("retrieve", "plan")
        builder.add_edge("plan", "lookup")
        builder.add_edge("lookup", "write_sql")
    else:
        builder.add_edge("retrieve", "write_sql")

    builder.add_edge("write_sql", "safety_check")
    builder.add_conditional_edges(
        "safety_check", route_after_safety,
        {"ok": "run_sql", "fix": "fix_sql", "give_up": "cannot_answer"},
    )

    if plan_mode == "plan":
        builder.add_conditional_edges(
            "run_sql", route_after_run_plan,
            {"shape": "shape_check", "fix": "fix_sql", "give_up": "cannot_answer"},
        )
        builder.add_conditional_edges(
            "shape_check", route_after_shape,
            {"done": "answer", "skip_answer": END, "fix": "fix_sql"},
        )
    else:
        builder.add_conditional_edges(
            "run_sql", route_after_run,
            {"done": "answer", "skip_answer": END, "fix": "fix_sql", "give_up": "cannot_answer"},
        )

    builder.add_edge("fix_sql", "safety_check")   # the loop-back arrow
    builder.add_edge("answer", END)
    builder.add_edge("cannot_answer", END)
    return builder.compile()


graph = build_graph(PLAN_MODE)


# ---------- RESUMABLE RUN ----------
def load_results(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_results(results, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)


def _short(node_name, update):
    if node_name == "retrieve":
        return {"examples": [(e["id"], e["score"]) for e in update["examples"]]}
    if node_name == "plan":
        return {"plan_ok": update["plan_ok"],
                "filters": [(f["table"], f["column"], f["value"])
                            for f in update["plan"].get("filters", [])]}
    if node_name == "lookup":                                                # NEW (Day 33)
        return {"statuses": [r["status"] for r in update["lookup"]],
                "facts_lines": update["facts_text"].count("\n- ") + update["facts_text"].startswith("- "),
                "seconds": update["lookup_seconds"]}
    if node_name == "shape_check":
        return update["shape_events"][-1]
    return {k: str(v)[:120] for k, v in update.items()}


def run_one_repeat(rep, questions, ids, run_id):
    path = results_file(rep)
    results = load_results(path)
    print(f"\n========== REPEAT {rep}/{REPEATS} -> {path} ==========")

    for qid in ids:
        if str(qid) in results:
            print(f"Q{qid}: already done, skipping")
            continue

        q = questions[qid]
        print(f"\nQ{qid} (rep {rep}): {q['question']}")

        start = {"question": q["question"], "error": "", "fixes": 0,
                 "explain": EXPLAIN, "shape_fixes": 0, "shape_events": []}
        final = dict(start)
        t_start = time.perf_counter()
        t_prev = t_start
        steps = []
        reset_usage()
        try:
            for step in graph.stream(start, stream_mode="updates"):
                for node_name, update in step.items():
                    now = time.perf_counter()
                    steps.append({"node": node_name,
                                  "seconds": round(now - t_prev, 2)})
                    t_prev = now
                    print(f"  [{node_name}] -> {_short(node_name, update)}")
                    final.update(update)
        except BaseException as e:
            log_question({
                "run_id": run_id,
                "set": SET,
                "rep": rep,
                "qid": qid,
                "plan_mode": PLAN_MODE,
                "status": "interrupted" if isinstance(e, KeyboardInterrupt) else "crashed",
                "path": [s["node"] for s in steps],
                "node_seconds": steps,
                "total_seconds": round(time.perf_counter() - t_start, 2),
                "error": f"{type(e).__name__}: {str(e)[:300]}",
                **usage_summary(),
            })
            raise                                                           # stop like before
        total_seconds = round(time.perf_counter() - t_start, 2)
        usage = usage_summary()

        # Score outside the graph (the agent itself never sees gold SQL)
        gold = run_sql(q["sql"])
        ran_ok = final.get("error") == "" and final.get("result") is not None
        correct = ran_ok and same_result(gold, final["result"])
        print(f"  fixes used: {final['fixes']}, facts: {bool(final.get('facts_text'))}, correct: {correct}")

        results[str(qid)] = {
            "question": q["question"],
            "tables": final.get("tables"),
            "example_mode": EXAMPLE_MODE,
            "w_tables": W_TABLES if EXAMPLE_MODE == "schema_aware" else None,
            "examples": final.get("examples"),
            "plan_mode": PLAN_MODE,
            "plan_version": PLAN_VERSION if PLAN_MODE in ("plan", "tools") else None,
            "plan": final.get("plan"),
            "plan_ok": final.get("plan_ok"),
            "lookup": final.get("lookup"),                                   # NEW (Day 33)
            "facts_text": final.get("facts_text"),                           # NEW (Day 33)
            "lookup_seconds": final.get("lookup_seconds"),                   # NEW (Day 33)
            "shape_events": final.get("shape_events"),
            "shape_fixes": final.get("shape_fixes", 0),
            "sql_before_shape_fix": final.get("sql_before_shape_fix"),
            "cols_before_shape_fix": final.get("cols_before_shape_fix"),
            "result_columns": n_columns(final.get("result")),
            "first_sql": final.get("first_sql"),
            "sql": final.get("sql"),
            "gemini_sql": final.get("sql") if final.get("safe") else "",
            "fixes": final.get("fixes"),
            "safe": final.get("safe"),
            "error": final.get("error"),
            "correct": bool(correct),
            "explain": final.get("explain"),
            "answer": final.get("answer"),
            "result_preview": str(final.get("result"))[:500],
            "gold_sql": q["sql"],
            "node_seconds": steps,
            "total_seconds": total_seconds,
            "usage": usage,
        }
        save_results(results, path)

        log_question({
            "run_id": run_id,
            "set": SET,
            "rep": rep,
            "qid": qid,
            "plan_mode": PLAN_MODE,
            "plan_ok": final.get("plan_ok"),
            "facts": bool(final.get("facts_text")),                          # NEW (Day 33)
            "lookup_statuses": [r["status"] for r in (final.get("lookup") or [])],
            "status": "answered" if ran_ok else "gave_up",
            "correct": bool(correct),
            "fixes": final.get("fixes"),
            "example_ids": [e["id"] for e in (final.get("examples") or [])],
            "path": [s["node"] for s in steps],
            "node_seconds": steps,
            "total_seconds": total_seconds,
            "error": final.get("error"),
            **usage,
        })
        time.sleep(PAUSE)

    return results


if __name__ == "__main__":
    with open(QUESTIONS_FILE, encoding="utf-8") as f:
        questions = {q["id"]: q for q in json.load(f)}
    ids = sorted(questions) if QUESTION_IDS is None else QUESTION_IDS

    RUN_ID = new_run_id()
    print(f"run_id: {RUN_ID}  set: {SET}  repeats: {REPEATS}  questions: {len(ids)}  "
          f"examples: {EXAMPLE_MODE} (k={K_EXAMPLES}, w={W_TABLES})  "
          f"mode: {PLAN_MODE} (plan {PLAN_VERSION if PLAN_MODE != 'none' else '-'})")

    scores = []
    for rep in range(1, REPEATS + 1):
        results = run_one_repeat(rep, questions, ids, RUN_ID)
        done = [results[str(i)] for i in ids if str(i) in results]
        scores.append((rep, sum(r["correct"] for r in done), len(done)))

    print("\n========== SUMMARY (strict) ==========")
    for rep, correct, n in scores:
        print(f"  rep {rep}: {correct}/{n}")