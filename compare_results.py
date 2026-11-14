import json
import os
from decimal import Decimal

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, URL, text

load_dotenv()

url = URL.create(
    "postgresql+psycopg2",
    username="postgres",
    password=os.getenv("DB_PASSWORD"),
    host="localhost",
    port=5432,
    database="olist",
)
engine = create_engine(url)


def run_sql(sql):
    """Run a query and return the result as a DataFrame."""
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn)


def normalize(df):
    """Turn a result into a sorted list of rows so we can compare fairly."""
    rows = []
    for row in df.itertuples(index=False):
        clean = []
        for v in row:
            if isinstance(v, (float, Decimal)):
                v = round(float(v), 2)   # 12.3456 and 12.35 count as equal
            clean.append(str(v))
        rows.append(tuple(clean))
    return sorted(rows)                  # ignore row order


def same_result(df1, df2):
    return normalize(df1) == normalize(df2)


def column_signatures(df):
    """Describe each column by its sorted, cleaned values."""
    sigs = []
    for col in df.columns:
        vals = []
        for v in df[col]:
            if isinstance(v, (float, Decimal)):
                v = round(float(v), 2)
            vals.append(str(v))
        sigs.append(tuple(sorted(vals)))
    return sigs


def lenient_match(df1, df2):
    """Same row count, and every column of the smaller result is in the larger one."""
    if len(df1) != len(df2):
        return False
    a = column_signatures(df1)
    b = column_signatures(df2)
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    big = list(big)
    for col in small:
        if col in big:
            big.remove(col)
        else:
            return False
    return True


if __name__ == "__main__":
    with open("test_questions.json", encoding="utf-8") as f:
        questions = json.load(f)

    q1 = questions[0]
    q2 = questions[1]

    result1 = run_sql(q1["sql"])
    print("Q1 vs itself:", same_result(result1, run_sql(q1["sql"])))
    print("Q1 vs Q2:   ", same_result(result1, run_sql(q2["sql"])))