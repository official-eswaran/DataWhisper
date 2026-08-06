#!/usr/bin/env python3
"""Warn before a pinned runtime reaches end of life (issue #47).

Dependabot cannot cover this. It ignores `docker` majors by design — a base-image
major swaps the language runtime under the whole app and a green `docker build`
does not run the test suite inside the new image — and it structurally cannot see
the `node-version` inputs in the workflows at all, because the `github-actions`
ecosystem bumps `actions/setup-node` itself, never the version it installs. Node
20 sat three months past EOL in three places before anyone noticed, and only
because an unrelated major PR happened to get read.

Two design choices worth keeping:

* **Pins are parsed out of the real files, never re-declared here.** A list of
  versions maintained alongside the ones that matter is a list that goes stale
  silently, which is the failure this script exists to prevent. If a pin moves
  and this script can't find it, that is a loud error, not a pass.
* **It checks the support window against a live source.** A calendar reminder
  fires on a date somebody guessed; `endoflife.date` also catches the window
  *moving*, which is the case a reminder cannot.

Usage:
    python3 scripts/check_eol.py                  # human-readable, exits 1 if due
    python3 scripts/check_eol.py --format github  # markdown for an issue body
    python3 scripts/check_eol.py --threshold-days 180
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# How far ahead to start warning. Six months is enough to plan a runtime bump
# into a normal release cycle rather than doing it under pressure.
DEFAULT_THRESHOLD_DAYS = 180

API = "https://endoflife.date/api/{product}.json"


@dataclass(frozen=True)
class Pin:
    """A runtime version pinned somewhere in the repo."""

    product: str  # endoflife.date product id
    cycle: str  # the release series, e.g. "3.12"
    where: str  # human-readable location, for the issue body


@dataclass(frozen=True)
class Finding:
    pin: Pin
    eol: date
    days_left: int

    @property
    def expired(self) -> bool:
        return self.days_left < 0


# ── Discovering the pins ──────────────────────────────────────────────────────
#
# Each rule is (file, regex, product). The regex must expose a `cycle` group.
# `required` marks pins that must exist — if one stops matching, the pin moved
# or was renamed and this script has gone blind to it.
_RULES: list[tuple[str, str, str]] = [
    ("backend/Dockerfile", r"^FROM python:(?P<cycle>\d+\.\d+)", "python"),
    ("frontend/Dockerfile", r"^FROM node:(?P<cycle>\d+)", "nodejs"),
    ("frontend/Dockerfile", r"^FROM nginx:(?P<cycle>\d+\.\d+)", "nginx"),
    (".github/workflows/ci.yml", r"python-version:\s*[\"'](?P<cycle>\d+\.\d+)", "python"),
    (".github/workflows/ci.yml", r"node-version:\s*[\"'](?P<cycle>\d+)", "nodejs"),
    (".github/workflows/e2e.yml", r"python-version:\s*[\"'](?P<cycle>\d+\.\d+)", "python"),
    (".github/workflows/e2e.yml", r"node-version:\s*[\"'](?P<cycle>\d+)", "nodejs"),
    (".github/workflows/eval.yml", r"python-version:\s*[\"'](?P<cycle>\d+\.\d+)", "python"),
]


def discover_pins(root: Path = REPO_ROOT) -> list[Pin]:
    """Read the pinned versions out of the files that actually carry them.

    Raises if a rule matches nothing: a silently-skipped pin is exactly the blind
    spot this script exists to close, so it must fail loudly rather than report
    "all clear" on a file it no longer understands.
    """
    pins: list[Pin] = []
    missing: list[str] = []
    for relpath, pattern, product in _RULES:
        path = root / relpath
        if not path.exists():
            missing.append(f"{relpath} (file not found)")
            continue
        text = path.read_text()
        found = list(re.finditer(pattern, text, re.MULTILINE))
        if not found:
            missing.append(f"{relpath} ~ /{pattern}/")
            continue
        for m in found:
            line_no = text[: m.start()].count("\n") + 1
            pins.append(
                Pin(product=product, cycle=m.group("cycle"), where=f"{relpath}:{line_no}")
            )
    if missing:
        raise LookupError(
            "check_eol: these pins no longer match — the version moved or was "
            "renamed, and this check is blind to it until the rule is updated:\n  "
            + "\n  ".join(missing)
        )
    return pins


# ── Looking up support windows ────────────────────────────────────────────────


def fetch_cycles(product: str, *, timeout: int = 20) -> list[dict]:
    with urllib.request.urlopen(API.format(product=product), timeout=timeout) as resp:
        return json.load(resp)


def eol_for(cycles: list[dict], cycle: str) -> date | None:
    """The EOL date for a release series, or None if it has none we can use.

    `eol` is a date string, or a bool: `false` means "supported, no end
    announced" and `true` means "already ended, no date given". A rolling
    release (nginx stable) reports the former, which is not something to warn
    about.
    """
    for entry in cycles:
        if str(entry.get("cycle")) != cycle:
            continue
        raw = entry.get("eol")
        if raw is True:
            # Ended, date unknown — treat as long past so it always reports.
            return date.min
        if not raw or raw is False:
            return None
        return datetime.strptime(str(raw), "%Y-%m-%d").date()
    return None


def evaluate(
    pins: list[Pin],
    cycles_by_product: dict[str, list[dict]],
    today: date,
    threshold_days: int = DEFAULT_THRESHOLD_DAYS,
) -> list[Finding]:
    """Findings for every pin inside the warning window, soonest first.

    Pure: takes the API payload rather than fetching it, so the whole decision is
    testable against a stale pin without touching the network — which is what
    "proven to fire" in issue #47 requires.
    """
    findings: list[Finding] = []
    for pin in pins:
        eol = eol_for(cycles_by_product.get(pin.product, []), pin.cycle)
        if eol is None:
            continue
        days_left = (eol - today).days
        if days_left <= threshold_days:
            findings.append(Finding(pin=pin, eol=eol, days_left=days_left))
    return sorted(findings, key=lambda f: (f.days_left, f.pin.where))


# ── Reporting ─────────────────────────────────────────────────────────────────


def format_report(findings: list[Finding], threshold_days: int) -> str:
    if not findings:
        return f"No pinned runtime reaches EOL within {threshold_days} days."

    expired = [f for f in findings if f.expired]
    lines = [
        "The following pinned runtimes are at or near end of life.",
        "",
        "| Runtime | Pinned | EOL | Status | Where |",
        "|---|---|---|---|---|",
    ]
    for f in findings:
        status = (
            f"**EOL {abs(f.days_left)} days ago**" if f.expired else f"{f.days_left} days left"
        )
        lines.append(
            f"| {f.pin.product} | {f.pin.cycle} | {f.eol.isoformat()} | {status} | "
            f"`{f.pin.where}` |"
        )
    lines += [
        "",
        "Every location for a runtime moves together — the Dockerfile pin and the "
        "workflow `*-version` inputs are the same runtime, and bumping one without "
        "the others means CI stops testing what ships.",
        "",
        "Prefer an **LTS** line. Dependabot once proposed Node 20 → 26 (Current, "
        "not LTS); that PR was correct to reject and is why docker majors stay "
        "ignored (#42).",
    ]
    if expired:
        lines += ["", "⚠️ At least one runtime is **already past EOL** — it is receiving no "
                  "security patches."]
    lines += ["", "_Opened automatically by `scripts/check_eol.py` (issue #47)._"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Warn before a pinned runtime reaches EOL.")
    ap.add_argument("--threshold-days", type=int, default=DEFAULT_THRESHOLD_DAYS)
    ap.add_argument("--format", choices=("text", "github"), default="text")
    ap.add_argument(
        "--today",
        default=None,
        help="Override today's date (YYYY-MM-DD) to rehearse a future warning.",
    )
    args = ap.parse_args(argv)

    today = (
        datetime.strptime(args.today, "%Y-%m-%d").date() if args.today else date.today()
    )

    try:
        pins = discover_pins()
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    cycles_by_product: dict[str, list[dict]] = {}
    for product in sorted({p.product for p in pins}):
        try:
            cycles_by_product[product] = fetch_cycles(product)
        except Exception as exc:  # noqa: BLE001 - network/API shape, reported not raised
            print(f"check_eol: could not fetch {product}: {exc}", file=sys.stderr)
            return 2

    findings = evaluate(pins, cycles_by_product, today, args.threshold_days)
    print(format_report(findings, args.threshold_days))
    # 1 = something needs attention; the workflow turns that into an issue.
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
