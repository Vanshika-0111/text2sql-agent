import json
import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types
from schema_parts import load_schema_parts
from llm_usage import record_usage

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
MODEL = "gemini-3.5-flash-lite"
PAUSE = 5

HEADER, TABLES = load_schema_parts()

with open("prompt_rules.txt", encoding="utf-8") as f:
    RULES = f.read()

# one short line per table: the line right after "TABLE: <name>"
TABLE_LIST = "\n".join(
    f"- {name}: {chunk.split(chr(10))[1].strip()}" for name, chunk in TABLES.items()
)


def pick_tables(question):
    """Ask Gemini which tables the question needs. Returns a list of valid table names."""
    prompt = f"""You are a PostgreSQL expert choosing tables for a query.

{HEADER}

{RULES}

Tables:
{TABLE_LIST}

Question: {question}

Which tables are needed to answer this question? Include every table needed for joins.
If the question is about customers as people (top customers, repeat buyers), include customers.
If you are unsure whether a table is needed, include it.
Return ONLY a JSON list of table names, e.g. ["orders", "customers"]."""
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json"),
    )
    record_usage("pick_tables", response)
    try:
        names = json.loads(response.text)
    except json.JSONDecodeError:
        names = []

    picked = [n for n in names if n in TABLES]   # drop anything that isn't a real table
    return picked if picked else list(TABLES)    # nothing valid -> fall back to all tables


if __name__ == "__main__":
    print("Short table list sent to the picker:")
    print(TABLE_LIST)
    print()

    tests = [
        "How many orders were delivered late?",
        "Which 5 product categories had the highest revenue in 2017?",
        "What is the average review score for customers in each state?",
    ]
    for q in tests:
        print(q)
        print("   ->", pick_tables(q))
        time.sleep(PAUSE)