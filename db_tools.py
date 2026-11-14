"""Day 32: database tools for the lookup node (0 Gemini calls, read-only).

lookup(filters, picked_tables) -> (results, seconds)
    filters = plan["filters"] = [{"table", "column", "value"}, ...]
facts_to_text(results) -> "Database facts ..." block for write_sql ("" if nothing useful)

Safety: table/column names are validated against information_schema AND the picked tables,
then quoted by SQLAlchemy; values are always bound parameters; SELECT only; 5 s timeout.
Text column  -> does the value exist (row count); if not, similar stored values (ILIKE, max 20);
                if the column has <= 30 distinct values, all of them (shown only when a value is missing).
Date/number column (or a text column named *_date / *_timestamp) -> MIN and MAX.
v2 (Day 32): caches (data is static); all-values list shown only when a value is missing.
v3 (Day 32): parser fixes found on the 240 saved plans - unquoted lists ("SP, RJ, MG") are split;
             IS NULL / IS NOT NULL / NULL -> status "null_check", no facts line.
"""
import re
import time

from sqlalchemy import text

from compare_results import engine

TEXT_TYPES = {"text", "character varying", "character"}
NUM_TYPES = {"integer", "bigint", "smallint", "numeric", "double precision", "real"}
DATE_TYPES = {"date", "timestamp without time zone", "timestamp with time zone"}

MAX_FILTERS = 6
MAX_VALUES_PER_FILTER = 5
MAX_SIMILAR = 20
CATEGORICAL_MAX = 30
TIMEOUT_MS = 5000

NULL_TEST = re.compile(r"^\s*(is\s+)?(not\s+)?null\s*$", re.I)

_COLS = None
_COL_CACHE = {}     # (table, column) -> {"range": [...]} or {"n_distinct": n, "all_values": [...] or None}
_VAL_CACHE = {}     # (table, column, value) -> {"rows": n, "similar": [...]}


def all_columns():
    """{table: {column: data_type}} for the public schema (cached)."""
    global _COLS
    if _COLS is None:
        sql = text("SELECT table_name, column_name, data_type "
                   "FROM information_schema.columns WHERE table_schema = 'public'")
        with engine.connect() as conn:
            rows = conn.execute(sql).fetchall()
        _COLS = {}
        for t, c, d in rows:
            _COLS.setdefault(t, {})[c] = d
    return _COLS


def quote(name):
    return engine.dialect.identifier_preparer.quote(name)


def parse_values(raw):
    """Plan values look like "'delivered'", "IN ('SP','RJ')", "SP, RJ, MG", "= 2017", "RJ"
    -> list of strings (max MAX_VALUES_PER_FILTER)."""
    raw = str(raw or "").strip()
    quoted = re.findall(r"'([^']*)'", raw)
    if quoted:
        vals = quoted
    else:
        s = re.sub(r"^(=|!=|<>|>=|<=|>|<|not in|in|ilike|like)\s*", "", raw, flags=re.I)
        s = s.strip().strip("()").strip()
        vals = re.split(r"\s*,\s*|\s+(?:and|or)\s+", s, flags=re.I)
    out = []
    for v in vals:
        v = v.strip().strip('"').strip("%").strip()
        if v and v not in out:
            out.append(v)
    return out[:MAX_VALUES_PER_FILTER]


def _like_pattern(v):
    v = v.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{v}%"


def _set_timeout(conn):
    conn.execute(text(f"SET statement_timeout = {int(TIMEOUT_MS)}"))


def lookup_filter(conn, f, picked):
    table = str(f.get("table", "")).strip()
    column = str(f.get("column", "")).strip()
    if "." in column:                                  # plan sometimes writes table.column
        t2, column = column.split(".", 1)
        table = table or t2
    res = {"table": table, "column": column, "value": f.get("value")}

    cols = all_columns()
    if table not in cols:
        res["status"] = "unknown_table"
        return res
    if table not in picked:
        res["status"] = "table_not_picked"
        return res
    if column not in cols[table]:
        res["status"] = "unknown_column"
        return res
    if NULL_TEST.match(str(f.get("value") or "")):
        res["status"] = "null_check"                  # nothing to look up
        return res

    dtype = cols[table][column]
    res["type"] = dtype
    T, C = quote(table), quote(column)
    key = (table, column)
    date_like_text = dtype in TEXT_TYPES and re.search(r"(date|timestamp)$", column)

    if dtype in DATE_TYPES or dtype in NUM_TYPES or date_like_text:
        if key not in _COL_CACHE:
            lo, hi = conn.execute(text(f"SELECT MIN({C}), MAX({C}) FROM {T}")).fetchone()
            _COL_CACHE[key] = {"range": [str(lo), str(hi)]}
        res["range"] = _COL_CACHE[key]["range"]
        res["status"] = "ok"
        return res

    if dtype not in TEXT_TYPES:
        res["status"] = "unsupported_type"
        return res

    if key not in _COL_CACHE:
        n_distinct = conn.execute(text(f"SELECT COUNT(DISTINCT {C}) FROM {T}")).scalar()
        all_values = None
        if n_distinct <= CATEGORICAL_MAX:
            rows = conn.execute(text(f"SELECT DISTINCT {C} FROM {T} WHERE {C} IS NOT NULL ORDER BY 1")).fetchall()
            all_values = [r[0] for r in rows]
        _COL_CACHE[key] = {"n_distinct": n_distinct, "all_values": all_values}
    res["n_distinct"] = _COL_CACHE[key]["n_distinct"]
    if _COL_CACHE[key]["all_values"] is not None:
        res["all_values"] = _COL_CACHE[key]["all_values"]

    res["checked"] = []
    for v in parse_values(f.get("value")):
        vkey = (table, column, v)
        if vkey not in _VAL_CACHE:
            n = conn.execute(text(f"SELECT COUNT(*) FROM {T} WHERE {C} = :v"), {"v": v}).scalar()
            entry = {"rows": n}
            if n == 0:
                rows = conn.execute(
                    text(f"SELECT DISTINCT {C} FROM {T} WHERE {C} ILIKE :p ORDER BY 1 LIMIT {MAX_SIMILAR}"),
                    {"p": _like_pattern(v)}).fetchall()
                entry["similar"] = [r[0] for r in rows]
            _VAL_CACHE[vkey] = entry
        res["checked"].append({"value": v, **_VAL_CACHE[vkey]})
    res["status"] = "ok"
    return res


def lookup(filters, picked_tables):
    """Run lookup_filter for each (deduplicated) plan filter. Returns (results, seconds)."""
    t0 = time.perf_counter()
    picked = set(picked_tables or [])
    seen, results = set(), []
    with engine.connect() as conn:
        _set_timeout(conn)
        for f in (filters or []):
            key = (str(f.get("table")), str(f.get("column")), str(f.get("value")))
            if key in seen:
                continue
            seen.add(key)
            if len(results) >= MAX_FILTERS:
                break
            try:
                results.append(lookup_filter(conn, f, picked))
            except Exception as e:
                conn.rollback()                       # aborted transaction -> start clean
                _set_timeout(conn)
                results.append({"table": f.get("table"), "column": f.get("column"),
                                "value": f.get("value"), "status": "error",
                                "error": str(e)[:200]})
        conn.rollback()                               # read-only: never commit
    return results, round(time.perf_counter() - t0, 3)


def _short(v, n=60):
    s = str(v)
    return s if len(s) <= n else s[:n] + "..."


def facts_to_text(results):
    """Results -> 'Database facts' block for write_sql. "" if there is nothing useful."""
    lines = []
    for r in results:
        if r.get("status") != "ok":
            continue
        name = f"{r['table']}.{r['column']}"
        if "range" in r:
            lines.append(f"- {name}: stored values range from {r['range'][0]} to {r['range'][1]}")
            continue
        parts = []
        missing = False
        for e in r["checked"]:
            if e["rows"]:
                parts.append(f"'{_short(e['value'])}' exists ({e['rows']} rows)")
            else:
                missing = True
                if e.get("similar"):
                    sim = ", ".join(f"'{_short(s)}'" for s in e["similar"])
                    parts.append(f"'{_short(e['value'])}' not found; similar stored values: {sim}")
                else:
                    parts.append(f"'{_short(e['value'])}' not found")
        if r.get("all_values") and (missing or not r["checked"]):
            parts.append("all stored values: " + ", ".join(f"'{_short(v)}'" for v in r["all_values"]))
        if parts:
            lines.append(f"- {name}: " + "; ".join(parts))
    if not lines:
        return ""
    return "Database facts (looked up for this question's filters):\n" + "\n".join(lines)


if __name__ == "__main__":
    # smoke test: database only, 0 Gemini calls
    picked = ["orders", "customers", "order_payments", "products"]
    filters = [
        {"table": "orders", "column": "order_status", "value": "'delivered'"},
        {"table": "sellers", "column": "seller_state", "value": "'SP'"},           # table not picked
        {"table": "customers", "column": "customer_state", "value": "SP, RJ, MG"}, # unquoted list
        {"table": "customers", "column": "customer_city", "value": "'Rio de Janeiro'"},
        {"table": "orders", "column": "order_status", "value": "'cancelled'"},     # misspelt -> all values
        {"table": "products", "column": "product_category_name", "value": "IS NULL"},  # null check
    ]
    print("parse_values checks:")
    for raw in ["SP, RJ, MG", "IN ('SP','RJ')", "(SP, RJ)", "SP, RJ and MG", "'delivered'", "= 2017"]:
        print(f"  {raw!r:22} -> {parse_values(raw)}")
    results, secs = lookup(filters, picked)
    print(f"\n{len(results)} results in {secs} s; statuses: {[r['status'] for r in results]}")
    print("\n--- facts_to_text ---")
    print(facts_to_text(results))