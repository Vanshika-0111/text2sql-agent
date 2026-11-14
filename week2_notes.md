# Week 2 — Improving accuracy (Days 8–14)

Model: gemini-3.5-flash-lite, temperature 0 (from Day 10).
Tuned set: 60 questions (test_questions.json), used while improving the prompt.
Held-out set: 20 questions (heldout_questions.json, ids 101–120), never used for tuning.
All runs rescored with lenient_score.py against the current gold SQL.
Main metric = subset (Gemini's result contains the gold result).

## Comparison table

| Stage | What changed | Tuned strict | Tuned lenient | Tuned subset | Held-out (strict / lenient / subset) |
|---|---|---|---|---|---|
| Day 8 — baseline v2 | consistent test set, plain prompt | 38/60 | 42/60 | 47/60 | not built yet |
| Day 9 — prompt rules | 12 business-convention rules | 50/60 | 52/60 | 57/60 | not built yet |
| Day 10 — self-correction | Postgres errors fed back to Gemini (max 2 fixes) | 52/60 | 53/60 | 58/60 | 16 / 18 / 18 |
| Day 12 — few-shot | 4 hand-written example queries | 51/60 | 52/60 | 58/60 | 16 / 18 / 18 |
| Day 13 — schema linking | Gemini picks tables first (avg 1.9 of 9 sent) | 50/60 | 51/60 | 59/60 | 16 / 18 / 18 |
| Day 14 — SQLGlot safety | only one read-only SELECT reaches Postgres | 50/60 | 51/60 | 59/60 | 16 / 18 / 18 |

Strict numbers come from each run's "correct" field (Day 8 strict was scored before the Q48 gold fix).
Day 14 = Day 13: the safety gate passed all 160 Day 13 Gemini queries, so no rerun was needed.

## Takeaways

- Tuned subset went from 47/60 (78%) to 59/60 (98%).
- The big gains came from prompt rules (+10) and self-correction (+1, plus it removed the
  Postgres-error failure Q34). Most baseline failures were convention mismatches, not SQL skill.
- Few-shot, schema linking and safety were accuracy-neutral, but each added value:
  schema linking cut the schema in the prompt to ~2 of 9 tables, and safety guarantees
  no write/DDL statement can ever run.
- Held-out stayed at 18/20 subset across every stage -> no sign of overfitting to the tuned set,
  but there is a real gap: 98% tuned vs 90% held-out. 90% is the honest number to quote.

## Remaining failures

- Q56 (tuned): drops the NULL first-month growth row — convention choice.
- Q114 (held-out): duration convention mismatch.
- Q119 (held-out): counted reviews instead of orders.