import datetime
import json
import os

LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "agent_runs.jsonl")


def new_run_id():
    """One id per `python agent.py` call, e.g. 20261106_141530."""
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def log_question(record):
    """Append one JSON line per question. Append-only, so it is safe to resume."""
    os.makedirs(LOG_DIR, exist_ok=True)
    record = {"logged_at": datetime.datetime.now().isoformat(timespec="seconds"), **record}
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")