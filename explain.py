from google.genai import types
from run_eval import client, MODEL
import time
from llm_usage import record_usage

MAX_ROWS = 20


def result_to_text(result, max_rows=MAX_ROWS):
    """Turn the SQL result into text for the prompt. Returns (text, total_rows)."""
    if hasattr(result, "to_string"):          # pandas DataFrame
        total = len(result)
        text = result.head(max_rows).to_string(index=False)
    else:                                     # list of rows
        rows = list(result)
        total = len(rows)
        text = "\n".join(str(r) for r in rows[:max_rows])
    return text, total


def explain_result(question, sql, result):
    """Ask Gemini to explain the result in 2-3 plain-English sentences."""
    table, total = result_to_text(result)

    if total == 0:
        note = "the query returned 0 rows"
        table = "(no rows)"
    elif total > MAX_ROWS:
        note = f"showing the first {MAX_ROWS} of {total} rows"
    else:
        note = f"{total} row(s)"

    prompt = f"""You are a data analyst. A user asked a question about an e-commerce database,
and a SQL query was run to answer it.

Question: {question}

SQL query:
{sql}

Result ({note}):
{table}

Answer the user's question in 2-3 plain-English sentences using this result.
Rules:
- Use only numbers, names and values that appear in the result above.
  Do not calculate new totals, averages or percentages, and never guess.
- If the result has 0 rows, say that no matching data was found.
- If only the first rows are shown, say so and give the total number of rows.
- If only the first rows are shown, do not say which value is the largest, smallest,
  highest, lowest, or the biggest change overall, because you have not seen every row.
  Only describe the rows that are shown.
- Do not mention SQL, tables or column names, and do not repeat the query."""

    for attempt in range(1, 4):                      # try up to 3 times
        try:
            response = client.models.generate_content(
                model=MODEL, contents=prompt,
                config=types.GenerateContentConfig(temperature=0),
            )
            record_usage("answer", response)               
            return response.text.strip()
        except Exception as e:
            if "503" in str(e) and attempt < 3:      # server busy -> wait, retry
                print(f"  Gemini busy (503), waiting 30s (attempt {attempt}/3)")
                time.sleep(30)
            else:
                raise                                # other errors: stop and show them