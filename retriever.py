# retriever.py — Day 25 step 4: top-k example retrieval by cosine similarity
# Day 27: + retrieve_schema_aware(): cosine + W_TABLES * Jaccard(example tables, linked tables)
import json

import numpy as np
from embed_bank import MODEL, TASK_TYPE, load_json, embed_text
from check_linking import gold_tables                  # NEW (Day 27)

BANK_FILE = "example_bank.json"
EMB_FILE = "example_embeddings.json"
Q_EMB_FILE = "question_embeddings.json"

W_TABLES = 0.05      # NEW (Day 27): chosen offline on the tuned set (check_example_retrieval.py)

_cache = {}


def _load():
    if _cache:
        return
    bank = {str(e["id"]): e for e in load_json(BANK_FILE, [])}
    emb = load_json(EMB_FILE, {})

    # every bank example must have an up-to-date embedding
    bad = [k for k in bank
           if k not in emb
           or emb[k]["question"] != bank[k]["question"]
           or emb[k]["model"] != MODEL
           or emb[k]["task_type"] != TASK_TYPE]
    if bad:
        raise RuntimeError(f"Embeddings missing/outdated for examples {bad} - run embed_bank.py")

    ids = sorted(bank, key=int)
    mat = np.array([emb[k]["embedding"] for k in ids], dtype=np.float32)
    mat /= np.linalg.norm(mat, axis=1, keepdims=True)   # normalise -> dot product = cosine

    q_emb = load_json(Q_EMB_FILE, {})
    q_by_text = {v["question"]: v["embedding"] for v in q_emb.values()
                 if v["model"] == MODEL and v["task_type"] == TASK_TYPE}

    # NEW (Day 27): tables each example's SQL uses, in the same order as ids
    ex_tables = [set(gold_tables(bank[k]["sql"])) for k in ids]

    _cache.update(bank=bank, ids=ids, mat=mat, q_by_text=q_by_text, ex_tables=ex_tables)


def question_vector(question, client=None):
    """Return (vector, source). Uses saved embedding if the exact question text is cached."""
    _load()
    vec = _cache["q_by_text"].get(question)
    if vec is not None:
        return vec, "cache"
    if client is None:
        raise RuntimeError("Question not in cache and no client given")
    return embed_text(client, question), "api"


def _cosines(question_vec):
    v = np.asarray(question_vec, dtype=np.float32)
    v = v / np.linalg.norm(v)
    return _cache["mat"] @ v


def retrieve(question_vec, k=3):
    """Return the top-k bank examples (dicts with an added 'score'), best first."""
    _load()
    sims = _cosines(question_vec)
    order = np.argsort(-sims, kind="stable")[:k]
    return [{**_cache["bank"][_cache["ids"][i]], "score": float(sims[i])} for i in order]


def _jaccard(a, b):
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def retrieve_schema_aware(question_vec, tables, k=3, w=W_TABLES):
    """NEW (Day 27): rank by cosine + w * Jaccard(example tables, linked tables).
    tables = tables picked by the schema linker. Empty tables -> plain cosine."""
    _load()
    linked = set(tables or [])
    sims = _cosines(question_vec)
    jac = np.array([_jaccard(linked, t) for t in _cache["ex_tables"]], dtype=np.float32)
    score = sims + w * jac
    order = np.argsort(-score, kind="stable")[:k]
    return [{**_cache["bank"][_cache["ids"][i]],
             "score": float(score[i]), "cosine": float(sims[i]), "table_overlap": float(jac[i])}
            for i in order]


if __name__ == "__main__":
    # Offline check (Day 27): saved vectors only, no API calls.
    # 1) w=0 must equal plain retrieve(); 2) w=W_TABLES must equal yesterday's grid
    #    (example_retrieval_day27.json) for all 60 tuned questions, using gold tables.
    q_emb = load_json(Q_EMB_FILE, {})
    with open("example_tables_day27.json", encoding="utf-8") as f:
        q_tables = json.load(f)["tuned_questions"]
    with open("example_retrieval_day27.json", encoding="utf-8") as f:
        grid_top3 = json.load(f)[str(W_TABLES)]["top3"]

    same_plain, same_grid, mismatches = 0, 0, []
    for qid in sorted(q_tables, key=int):
        vec, source = question_vector(q_emb[qid]["question"])     # no client -> cache only
        plain = [e["id"] for e in retrieve(vec, k=3)]
        w0 = [e["id"] for e in retrieve_schema_aware(vec, q_tables[qid], k=3, w=0.0)]
        same_plain += [str(x) for x in plain] == [str(x) for x in w0]

        top = retrieve_schema_aware(vec, q_tables[qid], k=3)
        ids = [str(e["id"]) for e in top]
        if ids == grid_top3[qid]:
            same_grid += 1
        else:
            mismatches.append((qid, ids, grid_top3[qid], top))

    n = len(q_tables)
    print(f"w=0 equals plain retrieve(): {same_plain}/{n}")
    print(f"w={W_TABLES} equals Day 27 grid:  {same_grid}/{n}")
    for qid, got, want, top in mismatches:
        print(f"  Q{qid}: got {got}, grid had {want}")
        for e in top:
            print(f"     {e['id']}  score {e['score']:.4f}  cos {e['cosine']:.4f}  overlap {e['table_overlap']:.2f}")

    print("\nWatch list at w =", W_TABLES)
    for qid in ["51", "28"]:
        vec, _ = question_vector(q_emb[qid]["question"])
        for e in retrieve_schema_aware(vec, q_tables[qid], k=3):
            print(f"  Q{qid}  {e['id']}  score {e['score']:.3f}  cos {e['cosine']:.3f}  "
                  f"overlap {e['table_overlap']:.2f}  {e['question'][:70]}")