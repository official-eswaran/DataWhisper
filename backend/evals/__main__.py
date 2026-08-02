"""CLI: ``python -m evals``.

Environment is set up *before* importing anything from ``app`` — the settings
singleton reads env at import time, so a later assignment would be ignored.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BASELINE_PATH = Path(__file__).resolve().parent / "baseline.json"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals",
        description="Measure NL2SQL answer accuracy against the committed case set.",
    )
    parser.add_argument("--repeat", type=int, default=1,
                        help="run every case N times; surfaces non-determinism (default 1)")
    parser.add_argument("--category", action="append", default=None,
                        help="only run this category (repeatable)")
    parser.add_argument("--case", action="append", default=None,
                        help="only run this case id (repeatable)")
    parser.add_argument("--cache", action="store_true",
                        help="leave the LLM cache ON. Off by default: with it on, a "
                             "second run scores cached responses instead of the model")
    parser.add_argument("--json", type=Path, default=None,
                        help="write the full report as JSON to this path")
    parser.add_argument("--floor", type=float, default=None,
                        help="exit non-zero if accuracy falls below this percentage")
    parser.add_argument("--check-baseline", action="store_true",
                        help=f"use the floor recorded in {BASELINE_PATH.name}")
    parser.add_argument("--write-baseline", action="store_true",
                        help="overwrite the committed baseline with this run")
    parser.add_argument("--quiet", action="store_true", help="suppress per-case progress")
    return parser


def _setup_env(cache_enabled: bool) -> None:
    os.environ.setdefault("DEBUG", "true")
    os.environ["LLM_CACHE_ENABLED"] = "true" if cache_enabled else "false"


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _setup_env(args.cache)

    from app.nl2sql.llm_client import ollama_healthy  # noqa: PLC0415 — must follow _setup_env
    from evals.cases import CASES  # noqa: PLC0415
    from evals.check_cases import validate_cases  # noqa: PLC0415
    from evals.runner import run_eval  # noqa: PLC0415

    problems = validate_cases()
    if problems:
        print("The eval set itself is invalid — fix these before trusting any score:\n")
        print("\n".join(f"  - {p}" for p in problems))
        return 2

    if not ollama_healthy():
        print(
            "Ollama is not reachable. This eval measures the real model end to end, "
            "so there is nothing meaningful to report without it.\n"
            "Start it with `ollama serve` and pull the model named by LLM_MODEL.",
            file=sys.stderr,
        )
        return 3

    selected = CASES
    if args.category:
        wanted = set(args.category)
        selected = [c for c in selected if c.category in wanted]
    if args.case:
        wanted = set(args.case)
        selected = [c for c in selected if c.id in wanted]
    if not selected:
        print("No cases matched the filters.", file=sys.stderr)
        return 2

    total_attempts = len(selected) * args.repeat
    print(f"Running {len(selected)} case(s) x {args.repeat} → {total_attempts} attempts")
    print(f"LLM cache: {'ON' if args.cache else 'OFF'}\n")

    counter = {"n": 0}

    def progress(attempt, _round):
        counter["n"] += 1
        if args.quiet:
            return
        mark = "PASS" if attempt.passed else "FAIL"
        # flush: a full run takes minutes and stdout is block-buffered when
        # redirected to a file or a CI log, which would hide all progress.
        print(f"  [{counter['n']:>3}/{total_attempts}] {mark}  {attempt.case_id} "
              f"({attempt.seconds:.1f}s)", flush=True)

    report = run_eval(selected, repeat=args.repeat, on_result=progress)
    _print_summary(report)

    if args.json:
        args.json.write_text(json.dumps(_as_dict(report), indent=2) + "\n")
        print(f"\nWrote {args.json}")

    if args.write_baseline:
        BASELINE_PATH.write_text(json.dumps(_baseline_payload(report), indent=2) + "\n")
        print(f"Wrote baseline to {BASELINE_PATH}")

    floor = args.floor
    if args.check_baseline:
        if not BASELINE_PATH.exists():
            print(f"\nNo baseline at {BASELINE_PATH}", file=sys.stderr)
            return 2
        floor = json.loads(BASELINE_PATH.read_text())["floor"]

    if floor is not None:
        if report.accuracy + 1e-9 < floor:
            print(f"\nFAIL: accuracy {report.accuracy:.1f}% is below the floor of {floor:.1f}%")
            return 1
        print(f"\nOK: accuracy {report.accuracy:.1f}% meets the floor of {floor:.1f}%")
    return 0


def _print_summary(report) -> None:
    print("\n" + "=" * 68)
    print(f"Accuracy: {report.passed}/{report.total} = {report.accuracy:.1f}%")
    print(f"Model: {report.model}   cache: {'on' if report.cache_enabled else 'off'}   "
          f"{report.seconds:.0f}s total")
    print("=" * 68)

    print("\nBy category:")
    for category, (passed, total) in report.by_category().items():
        pct = passed / total * 100 if total else 0.0
        print(f"  {category:<12} {passed:>3}/{total:<3} {pct:5.1f}%")

    flaky = report.flaky_ids()
    if flaky:
        print("\nNon-deterministic across repeats (passed sometimes, failed others):")
        for case_id in flaky:
            print(f"  - {case_id}")

    failures = report.failures()
    if not failures:
        return
    print(f"\n{len(failures)} failing attempt(s):")
    seen: set[str] = set()
    for attempt in failures:
        if attempt.case_id in seen:
            continue  # one report per case even with --repeat
        seen.add(attempt.case_id)
        print(f"\n  ── {attempt.case_id} ({attempt.category}): {attempt.reason}")
        if attempt.generated_sql:
            print(f"     SQL: {' '.join(attempt.generated_sql.split())[:220]}")
        if attempt.expected:
            print(f"     expected: {attempt.expected.splitlines()[0][:120]}")
        if attempt.actual:
            print(f"     actual:   {attempt.actual.splitlines()[0][:120]}")


def _as_dict(report) -> dict:
    return {
        "started_at": report.started_at,
        "model": report.model,
        "cache_enabled": report.cache_enabled,
        "repeat": report.repeat,
        "seconds": round(report.seconds, 1),
        "total": report.total,
        "passed": report.passed,
        "accuracy": round(report.accuracy, 1),
        "by_category": {k: {"passed": p, "total": t} for k, (p, t) in report.by_category().items()},
        "flaky": report.flaky_ids(),
        "attempts": [
            {
                "case_id": a.case_id,
                "category": a.category,
                "passed": a.passed,
                "reason": a.reason,
                "sql": a.generated_sql,
                "seconds": round(a.seconds, 2),
            }
            for a in report.attempts
        ],
    }


def _baseline_payload(report) -> dict:
    return {
        "recorded_at": report.started_at,
        "model": report.model,
        "repeat": report.repeat,
        "cache_enabled": report.cache_enabled,
        "accuracy": round(report.accuracy, 1),
        "floor": 0.0,
        "by_category": {k: {"passed": p, "total": t} for k, (p, t) in report.by_category().items()},
        "note": "Set 'floor' by hand — see README.md. --write-baseline leaves it at 0.",
    }


if __name__ == "__main__":
    raise SystemExit(main())
