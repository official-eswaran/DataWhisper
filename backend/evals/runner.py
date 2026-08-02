"""Drive the real pipeline over the case set and score the answers."""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pandas as pd

from app.core.config import settings
from app.nl2sql.pipeline import NL2SQLPipeline
from evals.cases import CASES, Case
from evals.compare import describe, results_match
from evals.datasets import get_connection


@dataclass
class Attempt:
    case_id: str
    category: str
    passed: bool
    reason: str = ""
    generated_sql: str | None = None
    seconds: float = 0.0
    actual: str = ""
    expected: str = ""


@dataclass
class Report:
    attempts: list[Attempt] = field(default_factory=list)
    model: str = ""
    cache_enabled: bool = False
    repeat: int = 1
    started_at: str = ""
    seconds: float = 0.0

    @property
    def total(self) -> int:
        return len(self.attempts)

    @property
    def passed(self) -> int:
        return sum(1 for a in self.attempts if a.passed)

    @property
    def accuracy(self) -> float:
        return (self.passed / self.total * 100.0) if self.total else 0.0

    def by_category(self) -> dict[str, tuple[int, int]]:
        buckets: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for attempt in self.attempts:
            buckets[attempt.category][1] += 1
            if attempt.passed:
                buckets[attempt.category][0] += 1
        return {k: (v[0], v[1]) for k, v in sorted(buckets.items())}

    def failures(self) -> list[Attempt]:
        return [a for a in self.attempts if not a.passed]

    def flaky_ids(self) -> list[str]:
        """Cases that both passed and failed across repeats — the model is
        non-deterministic on them, which a single-run accuracy number hides."""
        outcomes: dict[str, set[bool]] = defaultdict(set)
        for attempt in self.attempts:
            outcomes[attempt.case_id].add(attempt.passed)
        return sorted(cid for cid, seen in outcomes.items() if len(seen) > 1)


def _to_frame(result: dict) -> pd.DataFrame:
    """Rebuild a DataFrame from the API envelope — what the user actually got."""
    records = result.get("data") or []
    if not records:
        return pd.DataFrame(columns=result.get("columns") or [])
    return pd.DataFrame(records)


def run_case(case: Case) -> Attempt:
    conn = get_connection(case.dataset)
    started = time.monotonic()
    try:
        # A fresh pipeline per case: conversation history is shared state, and a
        # prior question leaking into the prompt would make results order-dependent.
        result = NL2SQLPipeline(conn).run(case.question)
    except Exception as exc:  # noqa: BLE001 — an exception is a failed answer, not a crash
        return Attempt(case.id, case.category, False, f"pipeline raised: {exc!r}",
                       seconds=time.monotonic() - started)
    elapsed = time.monotonic() - started
    sql = result.get("sql")

    if case.kind == "chat":
        passed = result.get("type") == "chat"
        reason = "" if passed else f"expected a conversational reply, got type={result.get('type')!r}"
        return Attempt(case.id, case.category, passed, reason, sql, elapsed,
                       actual=str(result.get("summary", ""))[:200])

    if result.get("type") == "error":
        return Attempt(case.id, case.category, False, f"pipeline error: {result.get('message')}",
                       sql, elapsed)

    expected_df = conn.execute(case.reference_sql).fetchdf()
    actual_df = _to_frame(result)
    passed = results_match(actual_df, expected_df, ordered=case.ordered, subset_ok=case.subset_ok)
    return Attempt(
        case.id, case.category, passed,
        "" if passed else "wrong answer",
        sql, elapsed,
        actual=describe(actual_df), expected=describe(expected_df),
    )


def run_eval(cases: list[Case] | None = None, repeat: int = 1, on_result=None) -> Report:
    cases = cases if cases is not None else CASES
    report = Report(
        model=settings.LLM_MODEL,
        cache_enabled=settings.LLM_CACHE_ENABLED,
        repeat=repeat,
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    started = time.monotonic()
    for round_index in range(repeat):
        for case in cases:
            attempt = run_case(case)
            report.attempts.append(attempt)
            if on_result is not None:
                on_result(attempt, round_index)
    report.seconds = time.monotonic() - started
    return report
