# embed_bank.py — Day 25 step 2: embed the example bank questions once (resumable)
import os
import json
import time
from dotenv import load_dotenv
from google import genai
from google.genai import types

MODEL = "gemini-embedding-001"
TASK_TYPE = "SEMANTIC_SIMILARITY"
BANK_FILE = "example_bank.json"
OUT_FILE = "example_embeddings.json"
PAUSE_SECONDS = 2      # pause between calls (free tier)
RETRY_WAIT = 60        # wait after a 429 / 503 before trying again
MAX_TRIES = 3


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    # write to a temp file first, then replace -> file is never half-written
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def embed_text(client, text):
    for attempt in range(1, MAX_TRIES + 1):
        try:
            resp = client.models.embed_content(
                model=MODEL,
                contents=text,
                config=types.EmbedContentConfig(task_type=TASK_TYPE),
            )
            return resp.embeddings[0].values
        except Exception as e:
            msg = str(e)
            retryable = any(s in msg for s in ["429", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE"])
            if retryable and attempt < MAX_TRIES:
                print(f"   API busy/limit ({msg[:80]}...) - waiting {RETRY_WAIT}s, try {attempt + 1}/{MAX_TRIES}")
                time.sleep(RETRY_WAIT)
            else:
                raise


def main():
    load_dotenv()
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    bank = load_json(BANK_FILE, [])
    store = load_json(OUT_FILE, {})   # { "201": {"question", "model", "task_type", "embedding"} }
    print(f"Bank: {len(bank)} examples | already saved: {len(store)}")

    done = skipped = 0
    for ex in bank:
        key = str(ex["id"])
        old = store.get(key)
        if old and old["question"] == ex["question"] and old["model"] == MODEL and old["task_type"] == TASK_TYPE:
            skipped += 1
            continue

        vec = embed_text(client, ex["question"])
        store[key] = {
            "question": ex["question"],
            "model": MODEL,
            "task_type": TASK_TYPE,
            "embedding": vec,
        }
        save_json(OUT_FILE, store)    # save after every example
        done += 1
        print(f"embedded {key}  (dim {len(vec)})")
        time.sleep(PAUSE_SECONDS)

    # ids in the file that are no longer in the bank (e.g. deleted examples)
    bank_ids = {str(ex["id"]) for ex in bank}
    stale = sorted(k for k in store if k not in bank_ids)

    print()
    print(f"New embeddings: {done} | skipped (already done): {skipped}")
    print(f"Total in {OUT_FILE}: {len(store)}")
    if stale:
        print("WARNING - ids in embeddings file but not in bank:", stale)
    dims = {len(v["embedding"]) for v in store.values()}
    print("Embedding dimensions found:", dims)


if __name__ == "__main__":
    main()