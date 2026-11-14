"""Day 41: LLMOps report over the final tools agent. 0 Gemini calls (reads saved results).
Final 150 = Day 33 tuned (60) + variant (20) + Day 33 new70 (70), scored by benchmark_v2 set labels."""
import json
from collections import defaultdict
import sqlglot
from lenient_score import lenient, subset
from compare_results import run_sql, same_result

BENCH = json.load(open("benchmark_v2.json", encoding="utf-8"))
SET_OF = {b["id"]: b["set"] for b in BENCH}
HELD = {b["id"] for b in BENCH if b["held_out"]}

# where each id's result lives: old 80 in day33 tuned/heldout, new 70 in day33 new70
FILES = {"tuned": "agent_day33_tuned", "variant": "agent_day33_heldout", "new70": "agent_day33_new70"}
REPS = 3


def load(prefix, rep):
    return json.load(open(f"{prefix}_rep{rep}_results.json", encoding="utf-8"))


def source(qid):
    if 1 <= qid <= 60:
        return "tuned"
    if 101 <= qid <= 120:
        return "variant"
    return "new70"


def valid_sql(sql):
    try:
        sqlglot.parse_one(sql, read="postgres")
        run_sql(sql)
        return True
    except Exception:
        return False


# gather per-question records across reps
records = defaultdict(list)   # qid -> [result dict per rep]
for rep in range(1, REPS + 1):
    cache = {src: load(pre, rep) for src, pre in FILES.items()}
    for b in BENCH:
        qid = b["id"]
        r = cache[source(qid)].get(str(qid))
        if r:
            records[qid].append(r)

# metrics per report-set
def report_set(qid):
    s = SET_OF[qid]
    return "heldout" if qid in HELD else s

sets = ["tuned", "variant", "tuned_ext", "heldout", "ALL"]
acc_strict = defaultdict(list); acc_subset = defaultdict(list)
validity = defaultdict(list); corr_used = defaultdict(list); corr_saved = defaultdict(list)
tokens = defaultdict(list); latency = defaultdict(list)

for qid, reps in records.items():
    rs = report_set(qid)
    for r in reps:
        for key in (rs, "ALL"):
            acc_strict[key].append(1 if r["correct"] else 0)
            try:
                gold = run_sql(r["gold_sql"]); pred = run_sql(r["gemini_sql"]) if r.get("gemini_sql") else None
                acc_subset[key].append(1 if (pred is not None and subset(gold, pred)) else 0)
            except Exception:
                acc_subset[key].append(0)
            validity[key].append(1 if valid_sql(r.get("first_sql", "")) else 0)
            used = r.get("fixes", 0) > 0
            corr_used[key].append(1 if used else 0)
            if used:
                corr_saved[key].append(1 if r["correct"] else 0)
            tokens[key].append(r.get("usage", {}).get("total_tokens", 0))
            latency[key].append(r.get("total_seconds", 0))

def pct(x): return 100 * sum(x) / len(x) if x else 0
def med(x): return sorted(x)[len(x)//2] if x else 0

print(f"{'set':10} {'n':>4} {'strict%':>8} {'subset%':>8} {'valid%':>7} {'fix%':>6} {'fix_rec%':>8} {'tok/q':>7} {'med_s':>6}")
for s in sets:
    n = len(acc_strict[s])
    rec = pct(corr_saved[s]) if corr_saved[s] else 0
    print(f"{s:10} {n:>4} {pct(acc_strict[s]):>8.1f} {pct(acc_subset[s]):>8.1f} "
          f"{pct(validity[s]):>7.1f} {pct(corr_used[s]):>6.1f} {rec:>8.1f} "
          f"{sum(tokens[s])/max(1,n):>7.0f} {med(latency[s]):>6.1f}")

print("\nHeld-out strict is the generalisation number. Validity = parses+runs on first try (pre-fix).")
print("fix% = used the self-correction loop; fix_rec% = of those, how many ended correct.")