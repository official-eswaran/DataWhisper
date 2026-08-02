# NL2SQL accuracy eval

Answers the question nothing else in this repo answers: **is the generated SQL
correct?**

`test_sql_validator.py` proves generated SQL is *safe*. The E2E proves a query
*runs* and renders. Neither notices if "total revenue by region" returns the
right numbers under the wrong labels, or sums the wrong column. A wrong `SUM()`
on financial data destroys trust permanently, and the default model is a 3B doing
schema-aware generation, so this is not a hypothetical risk.

## Running it

Needs a live Ollama with the model named by `LLM_MODEL` (default
`llama3.2:3b`) — it measures the real pipeline end to end.

```bash
cd backend
python -m evals                       # full set, ~2 min on a warm laptop
python -m evals --repeat 3            # 3x each; reports non-determinism
python -m evals --category group_by   # one slice
python -m evals --case sales_top_region
python -m evals --check-baseline      # exit 1 if below the committed floor
python -m evals --json report.json    # machine-readable output
```

**The LLM cache is off by default.** With it on, the second run scores cached
responses rather than the model and every number is meaningless — the same trap
recorded for the load test in `loadtest/README.md` (38.3s cold vs 61ms warm).
`--cache` re-enables it if you are deliberately testing the cache.

## What "correct" means

Comparison is **execution accuracy**: run the model's SQL, run the case's
reference SQL, compare the results. Comparing SQL *text* would measure phrasing —
`SUM(x)` vs `sum(x)`, different join order, different alias — none of which
change the answer.

Tolerated, because they do not change the answer: column names, column order
(for narrow results), row order (unless the question asked for an ordering),
numeric type and precision to 2 dp, string case and padding, and date
representation. Not tolerated: different values, different row counts, or extra
columns the question did not ask for (unless the case sets `subset_ok`).

All of this lives in `compare.py` and is unit-tested in
`tests/test_nl2sql_eval.py`, which runs in the normal suite without Ollama.

## Ground truth

Each case pairs a question with a **reference SQL query**, not a hand-typed
answer. The reference runs against the same connection the pipeline used, so
expected answers cannot drift from the data and there is nothing to mistype.

`check_cases.py` validates the set itself and runs as part of the normal test
suite. It rejects a reference query that fails or returns no rows, a chat case
carrying SQL, a duplicate id, and — the subtle one — **a tie at the cutoff of a
ranked question**, where two different answers would both be correct and a case
could pass or fail on a coin flip. Every `ordered` case therefore carries a
`rank_sql` that exposes the ranking measure so the tie check can prove
uniqueness.

Two deliberate omissions in the case set, because they measure formatting rather
than correctness:

- **No `GROUP BY month`.** `1`, `"January"` and `"2024-01"` are all defensible
  renderings of the same group. Date handling is covered by filters instead,
  which have exactly one right answer.
- **No free-text summary assertions.** The prose summary is not the answer.

## The baseline and the floor

`baseline.json` records a measured run. `--check-baseline` fails below its
`floor`.

**The floor is set below the measured accuracy on purpose.** It exists to catch
a regression — a prompt edit, a model bump, a schema-detector change — not to
assert a target. Setting it at the measured value would make the job fail on
ordinary run-to-run variance from a non-deterministic model, and a job that
cries wolf gets ignored. Raise the floor when a real improvement lands, and
record what changed.

`--repeat N` runs every case N times and reports cases that passed sometimes and
failed others. Those are where a single-run number is least trustworthy, and
they are why the floor has headroom.

## Limitations — read before quoting the number

- **Two small datasets** (25 sales rows, 20 employees). Enough to catch wrong
  aggregation, grouping and filtering; not enough to say anything about joins
  across many tables, wide schemas, or large-cardinality grouping.
- **Single-table questions only.** The product loads one table per session, so
  that is what is measured. Multi-table accuracy is unknown.
- **One model, one machine.** The number moves with `LLM_MODEL` and with
  hardware. Re-measure after either changes; the baseline records both.
- **Not a safety measure.** Injection and DDL rejection are covered by
  `test_sql_validator.py`. The four `intent` cases here check routing only.
- **English, and one phrasing per question.** Robustness to paraphrase is not
  measured.
