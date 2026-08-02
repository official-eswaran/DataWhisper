"""Validate the eval set itself — no LLM involved.

An accuracy number is only as trustworthy as its ground truth. A reference query
that returns nothing, or a top-N question with a tie at the cutoff, produces a
case the model can fail (or pass) for reasons unrelated to its ability. These
checks run in the normal test suite so a bad case is caught when it is written,
not when someone is puzzling over a dip in the score.
"""
from __future__ import annotations

import re

from evals.cases import CASES, Case
from evals.compare import normalize_value
from evals.datasets import DATASETS, get_connection

_LIMIT_RE = re.compile(r"\blimit\s+(\d+)\s*$", re.IGNORECASE)


def _limit_of(sql: str) -> int | None:
    match = _LIMIT_RE.search(sql.strip())
    return int(match.group(1)) if match else None


def _check_one(case: Case) -> list[str]:
    problems = []

    if case.kind == "chat":
        if case.reference_sql is not None:
            problems.append(f"{case.id}: chat case must not carry reference_sql")
        return problems

    if not case.reference_sql:
        problems.append(f"{case.id}: data case needs a reference_sql")
        return problems
    if case.dataset not in DATASETS:
        problems.append(f"{case.id}: unknown dataset {case.dataset!r}")
        return problems

    conn = get_connection(case.dataset)
    try:
        expected = conn.execute(case.reference_sql).fetchdf()
    except Exception as exc:  # noqa: BLE001 — reported, not raised
        problems.append(f"{case.id}: reference_sql failed: {exc}")
        return problems

    if expected.empty:
        problems.append(
            f"{case.id}: reference_sql returns no rows — an empty expected answer "
            "is almost always a mistake in the case, and the model can match it by "
            "generating something equally wrong"
        )

    if not case.ordered:
        if case.rank_sql:
            problems.append(f"{case.id}: rank_sql is only meaningful with ordered=True")
        return problems

    # Ordered case: prove the answer is unique at the cutoff.
    if not case.rank_sql:
        problems.append(f"{case.id}: ordered case must supply rank_sql for the tie check")
        return problems

    limit = _limit_of(case.reference_sql)
    if limit is None:
        problems.append(f"{case.id}: ordered case should end in an explicit LIMIT")
        return problems

    try:
        ranked = conn.execute(case.rank_sql).fetchdf()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"{case.id}: rank_sql failed: {exc}")
        return problems

    if ranked.shape[1] != 1:
        problems.append(f"{case.id}: rank_sql must select exactly one measure column")
        return problems

    values = [normalize_value(v) for v in ranked.iloc[:, 0].tolist()]
    if len(values) <= limit:
        # Fewer ranked entities than the limit: nothing sits past the cutoff, so
        # the answer is the whole set and no tie can change it.
        return problems
    if values[limit - 1] == values[limit]:
        problems.append(
            f"{case.id}: tie at the cutoff — rank {limit} and {limit + 1} both have "
            f"measure {values[limit - 1]!r}, so more than one answer is correct"
        )
    return problems


def validate_cases(cases: list[Case] | None = None) -> list[str]:
    """Return a list of human-readable problems; empty means the set is sound."""
    cases = cases if cases is not None else CASES
    problems: list[str] = []

    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            problems.append(f"{case.id}: duplicate case id")
        seen.add(case.id)
        if case.kind not in ("data", "chat"):
            problems.append(f"{case.id}: unknown kind {case.kind!r}")
        problems.extend(_check_one(case))
    return problems


if __name__ == "__main__":  # pragma: no cover - convenience entry point
    found = validate_cases()
    print("\n".join(found) if found else f"{len(CASES)} cases OK")
    raise SystemExit(1 if found else 0)
