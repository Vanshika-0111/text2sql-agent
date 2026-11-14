"""Day 30: query-planning node.

make_plan(question, schema) -> (plan dict, ok)
plan_to_text(plan)          -> text block for write_sql / fix_sql ("" if no plan)

No examples in the plan prompt (Day 29 design: the plan fixes output shape
before examples can prime it). prompt_rules.txt is unchanged; the column
principle lives only in this prompt.

v1 (Day 30): one-sentence column principle (kept as plan_v1_day30.py).
v2 (Day 30): principle split into steps after test_plan_day30 v1 (tuned Q43/50/52
missed the rank column / thresholded measure); step 3 scoped to grouped results
after check_principle_day30.py (single-number questions Q22/25/37/38/55 and
Q35 must not get extra columns). Last non-bug change to this prompt.
"""
import json
import time

from google.genai import types
from pydantic import BaseModel, ValidationError

from link_tables import client, MODEL, PAUSE, HEADER, TABLES, RULES, pick_tables
from schema_parts import build_schema
from llm_usage import record_usage

PLAN_VERSION = "v2"


# ---------- output format (sent to Gemini as response_schema) ----------
class OutputColumn(BaseModel):
    name: str
    type: str        # integer / numeric / text / date / timestamp
    meaning: str


class Filter(BaseModel):
    table: str
    column: str
    value: str


class RuleUse(BaseModel):
    rule: str        # rule number or short name from the rules list
    how: str         # how it applies to this question


class Plan(BaseModel):
    output_columns: list[OutputColumn]
    count_unit: str
    tables: list[str]
    filters: list[Filter]
    group_by: list[str]
    order_limit: str
    rules: list[RuleUse]


COLUMN_PRINCIPLE = """Choose the output columns with these steps, in this order:
  1. If the result has one row per group or entity, the grouping/entity columns.
  2. Every measure the question names.
  3. If the result has one row per group AND the question keeps only the groups that pass a
     threshold on a group-level measure ("at least N ...", "more than N ...",
     "only include ... with at least N ..."), that thresholded measure as its own column,
     even if the question does not ask to see it.
  4. If the question asks to rank, a rank column.
  No other columns. If the question asks for a single number (how many ..., what percentage ...),
  return only that number."""


def plan_prompt(question, schema):
    return f"""You are a PostgreSQL expert. Before any SQL is written, make a plan for the query.

{schema}

{RULES}

Question: {question}

Make a plan with these fields:
- output_columns: the columns the final query must return, in order. For each: name,
  type (one of: integer, numeric, text, date, timestamp) and meaning (one short phrase).
- count_unit: what one counted row is (for example "order", "customer", "order item",
  "review"); write "none" if nothing is counted.
- tables: the tables the query needs, including tables used only for joins.
- filters: every condition the question states (status, date range, category, city, ...),
  as table, column and the value as it should be compared.
- group_by: the grouping expressions; empty list if none.
- order_limit: the ORDER BY and LIMIT the question asks for; "none" if not asked.
- rules: each rule from the rules list that applies here, and how it applies.

Output columns:
{COLUMN_PRINCIPLE}

Use only tables and columns that appear in the schema above. Do not write SQL."""


def make_plan(question, schema):
    """One Gemini call. Returns (plan dict, True) or ({}, False) if the output can't be parsed.
    API errors (quota, network) are raised, not hidden."""
    response = client.models.generate_content(
        model=MODEL,
        contents=plan_prompt(question, schema),
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=Plan,
        ),
    )
    record_usage("plan", response)
    try:
        plan = Plan.model_validate_json(response.text or "")
    except (ValidationError, ValueError):
        return {}, False
    if not plan.output_columns:
        return {}, False
    return plan.model_dump(), True


def plan_to_text(plan):
    """Plan -> text block for the SQL prompt. Empty plan -> "" (old Week 4 behaviour)."""
    if not plan:
        return ""
    lines = ["The query must return exactly these columns, in this order, with these types:"]
    for i, c in enumerate(plan["output_columns"], 1):
        lines.append(f"  {i}. {c['name']} ({c['type']}): {c['meaning']}")
    lines.append(f"Count unit (one counted row is one): {plan['count_unit']}")
    lines.append(f"Tables: {', '.join(plan['tables'])}")
    if plan["filters"]:
        lines.append("Filters:")
        for f in plan["filters"]:
            lines.append(f"  - {f['table']}.{f['column']}: {f['value']}")
    else:
        lines.append("Filters: none")
    lines.append(f"Group by: {', '.join(plan['group_by']) if plan['group_by'] else 'none'}")
    lines.append(f"Order/limit: {plan['order_limit']}")
    if plan["rules"]:
        lines.append("Rules that apply:")
        for r in plan["rules"]:
            lines.append(f"  - {r['rule']}: {r['how']}")
    return "\n".join(lines)


if __name__ == "__main__":
    # smoke test: 2 Gemini calls (picker + plan)
    q = "Which 5 product categories had the highest revenue in 2017?"
    names = pick_tables(q)
    print("Tables picked:", names)
    time.sleep(PAUSE)

    schema = build_schema(HEADER, TABLES, names)
    plan, ok = make_plan(q, schema)
    print("plan_ok:", ok)
    print(json.dumps(plan, indent=2, ensure_ascii=False))
    print()
    print("--- plan_to_text ---")
    print(plan_to_text(plan))