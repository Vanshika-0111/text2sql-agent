import sqlglot
from sqlglot import exp

# statement types that change the database (getattr skips names missing in your sqlglot version)
WRITE_NAMES = ["Insert", "Update", "Delete", "Drop", "Create", "Alter",
               "AlterTable", "Merge", "TruncateTable", "Command"]
WRITE_TYPES = tuple(t for t in (getattr(exp, n, None) for n in WRITE_NAMES) if t)

READ_TYPES = (exp.Select, exp.Union, exp.Intersect, exp.Except)


def check_sql(sql):
    """Return (True, "ok") if sql is one read-only query, else (False, reason)."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except Exception as e:
        return False, f"could not parse: {str(e)[:150]}"

    if len(statements) != 1:
        return False, f"expected 1 statement, got {len(statements)}"

    stmt = statements[0]
    if not isinstance(stmt, READ_TYPES):
        return False, f"not a SELECT query (got {type(stmt).__name__})"

    bad = stmt.find(*WRITE_TYPES)   # searches the whole tree, including CTEs
    if bad is not None:
        return False, f"contains a write statement ({type(bad).__name__})"

    return True, "ok"


if __name__ == "__main__":
    tests = [
        "SELECT COUNT(*) FROM orders",
        "WITH t AS (SELECT * FROM orders) SELECT COUNT(*) FROM t",
        "SELECT 1 UNION SELECT 2",
        "DROP TABLE orders",
        "DELETE FROM orders WHERE 1=1",
        "SELECT 1; DROP TABLE orders",
        "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d",
        "SELEC * FROM orders",
    ]
    for sql in tests:
        ok, reason = check_sql(sql)
        print(f"{'SAFE   ' if ok else 'BLOCKED'} | {reason:<45} | {sql}")