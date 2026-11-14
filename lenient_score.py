import json
import os
import datetime
from decimal import Decimal

from compare_results import run_sql


def norm(v):
    """Make one value comparable: None/NaN/NaT -> 'None', dates unified, numbers rounded to 2dp."""
    if v is None or v != v:                      # None, NaN, NaT
        return "None"
    if isinstance(v, datetime.datetime):
        if v.day == 1 and v.hour == 0 and v.minute == 0 and v.second == 0:
            return v.strftime("%Y-%m")           # month start, same as TO_CHAR 'YYYY-MM'
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, datetime.date):
        return v.strftime("%Y-%m") if v.day == 1 else v.isoformat()
    if isinstance(v, (float, Decimal)):
        return str(round(float(v), 2))
    return str(v)


def get_rows(res):
    return res.values.tolist() if hasattr(res, "values") else [list(r) for r in res]


def lenient(gold, pred):
    """Same rows, any row order, any column order."""
    g = sorted(tuple(sorted(norm(v) for v in row)) for row in get_rows(gold))
    p = sorted(tuple(sorted(norm(v) for v in row)) for row in get_rows(pred))
    return g == p


def subset(gold, pred):
    """Same row count, and every column of the narrower result appears in the wider one."""
    g_rows, p_rows = get_rows(gold), get_rows(pred)
    if len(g_rows) != len(p_rows):
        return False
    g_cols = [sorted(norm(v) for v in col) for col in zip(*g_rows)]
    p_cols = [sorted(norm(v) for v in col) for col in zip(*p_rows)]
    small, big = (p_cols, g_cols) if len(p_cols) <= len(g_cols) else (g_cols, p_cols)
    remaining = list(big)
    for col in small:
        if col not in remaining:
            return False
        remaining.remove(col)
    return True


def score(filename):
    with open(filename, encoding="utf-8") as f:
        results = json.load(f)
    strict_fail, len_fail, sub_fail = [], [], []
    for k in sorted(results, key=int):
        r = results[k]
        if not r.get("correct", False):          # NEW (Day 23): strict = 'correct' saved by the run
            strict_fail.append(k)
        try:
            gold, pred = run_sql(r["gold_sql"]), run_sql(r["gemini_sql"])
            ok_len, ok_sub = lenient(gold, pred), subset(gold, pred)
        except Exception:
            ok_len = ok_sub = False
        if not ok_len:
            len_fail.append(k)
        if not ok_sub:
            sub_fail.append(k)
    n = len(results)
    print(filename)
    print(f"  strict : {n - len(strict_fail)}/{n}   failed: {strict_fail}")
    print(f"  lenient: {n - len(len_fail)}/{n}   failed: {len_fail}")
    print(f"  subset : {n - len(sub_fail)}/{n}   failed: {sub_fail}\n")
    # NEW (Day 23): return the numbers so score_repeats can combine them
    return {"n": n,
            "strict": n - len(strict_fail), "lenient": n - len(len_fail),
            "subset": n - len(sub_fail),
            "strict_fail": strict_fail, "len_fail": len_fail, "sub_fail": sub_fail}


def score_repeats(prefix, reps=3):
    """NEW (Day 23): score {prefix}_rep1..repN_results.json, print mean and range."""
    runs = []
    for rep in range(1, reps + 1):
        fn = f"{prefix}_rep{rep}_results.json"
        if not os.path.exists(fn):
            print(f"{fn}: not found, skipping\n")
            continue
        runs.append(score(fn))
    if not runs:
        return None

    ns = [s["n"] for s in runs]
    print(f"===== {prefix}: {len(runs)} repeat(s) =====")
    if len(set(ns)) > 1:
        print(f"  WARNING: repeats have different question counts {ns} (a run is unfinished)")
    for metric in ["strict", "lenient", "subset"]:
        vals = [s[metric] for s in runs]
        mean = sum(vals) / len(vals)
        print(f"  {metric:7}: mean {mean:.1f}/{ns[0]}   range {min(vals)}-{max(vals)}   runs {vals}")

    fails = [set(s["sub_fail"]) for s in runs]
    always = set.intersection(*fails)
    some = set.union(*fails) - always
    print(f"  subset failed in every repeat: {sorted(always, key=int)}")
    print(f"  subset failed in some repeats (flaky): {sorted(some, key=int)}\n")
    return runs


if __name__ == "__main__":
    # Older single runs (uncomment to rescore; each one re-runs gold + Gemini SQL on Postgres)
    # score("results_gemini-3.5-flash-lite.json")              # Day 8 v2 baseline
    # score("results_gemini-3.5-flash-lite_day9_rules_v2.json")
    # score("results_gemini-3.5-flash-lite_day10_selfcorrect_t0.json")
    # score("results_gemini-3.5-flash-lite_heldout_day10.json")
    # score("results_gemini-3.5-flash-lite_day12_fewshot_tuned.json")   # NEW (Day 12)
    # score("results_gemini-3.5-flash-lite_day12_fewshot_heldout.json")   # Day 12, held-out
    # score("results_gemini-3.5-flash-lite_day13_linking_tuned.json")
    # score("results_gemini-3.5-flash-lite_day13_linking_heldout.json")
    # score("agent_day19_tuned_results.json")                  # Day 19 agent, tuned
    # score("agent_day19_heldout_results.json")                # Day 19 agent, held-out

    # Day 23: repeated runs (mean and range)
    # score_repeats("agent_day23_heldout", reps=3)             # Day 23 baseline, held-out
    # score_repeats("agent_day23_tuned", reps=3)               # Day 23 baseline, tuned
    score_repeats("agent_day24_heldout", 3)
