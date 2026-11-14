import json
import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types


from compare_results import run_sql, same_result
from few_shot_examples import EXAMPLES          # NEW (Day 12)
from schema_parts import load_schema_parts, build_schema   # NEW (Day 13)
from link_tables import pick_tables                        # NEW (Day 13)
from sql_safety import check_sql                           # NEW (Day 14)
from llm_usage import record_usage

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
MODEL = "gemini-3.5-flash-lite"

# NEW (Day 12): one switch picks which question set to run
SET = "heldout"      # "tuned" (60 questions) or "heldout" (20 questions)
QUESTIONS_FILE = "test_questions.json" if SET == "tuned" else "heldout_questions.json"
RESULTS_FILE = f"results_{MODEL}_day14_safety_{SET}.json"

PAUSE = 5        # 15 RPM -> 1 call every 4s; 5s gives a safety margin
MAX_FIXES = 2    # how many times Gemini may fix a failing query

HEADER, TABLES = load_schema_parts()   # NEW (Day 13): schema is built per question

with open("prompt_rules.txt", encoding="utf-8") as f:
    RULES = f.read()


def format_examples(examples):
    """NEW (Day 27): turn a list of {question, sql} dicts into one block of text."""
    return "\n\n".join(f"Question: {ex['question']}\nSQL:\n{ex['sql']}" for ex in examples)


# Day 12: the 4 fixed examples (default when no retrieved examples are given)
EXAMPLES_TEXT = format_examples(EXAMPLES)

LANG_TAGS = {"sql", "postgresql", "postgres", "psql", "pgsql"}   # NEW (Day 27)


def clean_sql(text):
    """Remove code fences, a leftover language tag, and whitespace from Gemini's reply.
    Day 27: also handles ```postgresql (it left a stray 'postgresql' line before)."""
    text = text.strip()
    if text.startswith("```"):
        text = text[3:]                        # drop the opening fence
    text = text.replace("```", "").strip()     # drop the closing fence
    first, _, rest = text.partition("\n")
    if first.strip().lower() in LANG_TAGS and rest.strip():
        text = rest                            # drop the language tag line
    return text.strip()


def _extra_sections(plan_text, facts_text):
    """Day 30 plan section + Day 33 facts section. Both None/"" -> "" (Week 4 prompt)."""
    plan_section = (f"Query plan for this question (follow it):\n{plan_text}\n\n"
                    if plan_text else "")
    facts_section = f"{facts_text}\n\n" if facts_text else ""    # NEW (Day 33)
    return plan_section + facts_section


def sql_prompt(question, schema, examples_text=None, plan_text=None, facts_text=None):
    """Day 30: write_sql prompt. plan_text=None/"" -> exactly the Week 4 prompt.
    Day 33: facts_text=None/"" -> no facts section (lookup output goes right before the question)."""
    if examples_text is None:
        examples_text = EXAMPLES_TEXT
    extra = _extra_sections(plan_text, facts_text)
    return f"""You are a PostgreSQL expert.
Here are the tables in the database:

{schema}

{RULES}

Here are some example questions with correct queries:

{examples_text}

{extra}Write one PostgreSQL query that answers this question:
{question}

Return ONLY the SQL query, with no explanation."""


def ask_gemini(question, schema, examples_text=None, plan_text=None, facts_text=None):
    """Send the schema, rules, examples (and plan / facts) and question to Gemini and return just the SQL.
    Day 27: examples_text=None -> the 4 fixed examples (old behaviour).
    Day 30: plan_text=None -> no plan section (Week 4 prompt).
    Day 33: facts_text=None -> no facts section."""
    prompt = sql_prompt(question, schema, examples_text, plan_text, facts_text)
    response = client.models.generate_content(model=MODEL, contents=prompt, config=types.GenerateContentConfig(temperature=0),)
    record_usage("write_sql", response)
    return clean_sql(response.text)


def fix_prompt(question, bad_sql, error, schema, examples_text=None, plan_text=None, facts_text=None):
    """Day 30: fix_sql prompt. plan_text=None/"" -> exactly the Week 4 prompt.
    Day 33: facts_text=None/"" -> no facts section."""
    if examples_text is None:
        examples_text = EXAMPLES_TEXT
    extra = _extra_sections(plan_text, facts_text)
    return f"""You are a PostgreSQL expert.
Here are the tables in the database:

{schema}

{RULES}

Here are some example questions with correct queries:

{examples_text}

{extra}Question:
{question}

You wrote this query:
{bad_sql}

PostgreSQL returned this error:
{error}

Fix the query so that it runs and answers the question.
Return ONLY the corrected SQL query, with no explanation."""


def fix_sql(question, bad_sql, error, schema, examples_text=None, plan_text=None, facts_text=None):
    """Show Gemini its failed query and the Postgres error, and ask for a fixed query.
    Day 27: examples_text=None -> the 4 fixed examples (old behaviour).
    Day 30: plan_text=None -> no plan section (Week 4 prompt).
    Day 33: facts_text=None -> no facts section."""
    prompt = fix_prompt(question, bad_sql, error, schema, examples_text, plan_text, facts_text)
    response = client.models.generate_content(model=MODEL, contents=prompt, config=types.GenerateContentConfig(temperature=0),)
    record_usage("fix_sql", response)
    return clean_sql(response.text)


def load_results():
    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_results(results):
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)


def evaluate(q):
    """Ask Gemini, run its SQL, and if Postgres errors, send the error back to Gemini to fix."""
    gold = run_sql(q["sql"])   # gold runs outside the loop: a gold error is our bug, not Gemini's

    tables = pick_tables(q["question"])               # NEW (Day 13): step 1, pick tables
    schema = build_schema(HEADER, TABLES, tables)
    time.sleep(PAUSE)                                 # second API call for this question
    gemini_sql = ask_gemini(q["question"], schema)
    record = {
        "question": q["question"],
        "difficulty": q["difficulty"],
        "tables": tables,
        "gold_sql": q["sql"],
        "first_sql": gemini_sql,   # Gemini's original attempt
        "gemini_sql": gemini_sql,  # final query after any fixes
        "fix_attempts": 0,
        "errors": [],              # every Postgres error seen, in order
        "correct": False,
        "error": None,             # error of the final query (None if it ran)
    }

    for attempt in range(MAX_FIXES + 1):
        try:
            ok, reason = check_sql(gemini_sql)       # NEW (Day 14): safety gate
            if not ok:
                raise ValueError(f"Blocked by safety check: {reason}")
            pred = run_sql(gemini_sql)
            record["correct"] = same_result(gold, pred)
            record["error"] = None
            break                              # query ran, so stop fixing
        except Exception as e:
            err = str(e)[:300]
            record["error"] = err
            record["errors"].append(err)
            if attempt == MAX_FIXES:
                break                          # out of fixes
            time.sleep(PAUSE)                  # rate-limit safe before the next API call
            gemini_sql = fix_sql(q["question"], gemini_sql, err, schema)
            record["fix_attempts"] += 1
            record["gemini_sql"] = gemini_sql

    return record

if __name__ == "__main__":

    with open(QUESTIONS_FILE, encoding="utf-8") as f:
        questions = json.load(f)

    results = load_results()

    for q in questions:
        key = str(q["id"])
        if key in results:
            continue   # already done on an earlier run

        record = None
        for attempt in range(1, 6):   # try up to 5 times
            try:
                record = evaluate(q)
                break
            except Exception as e:
                wait = 30 * attempt   # wait 30s, 60s, 90s, ...
                print(f"Q{key} attempt {attempt} failed: {str(e)[:80]}")
                print(f"   waiting {wait}s before retrying...")
                time.sleep(wait)

        if record is None:
            print(f"Giving up at Q{key}. Rerun later; finished questions are kept.")
            break
        results[key] = record

        save_results(results)
        mark = "OK " if results[key]["correct"] else "BAD"
        print(f"Q{key:>2} [{q['difficulty']}] {mark}")
        time.sleep(PAUSE)

    print(f"\nDone: {len(results)}/{len(questions)} questions in {RESULTS_FILE}")