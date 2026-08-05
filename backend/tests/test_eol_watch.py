"""EOL-runtime watch (issue #47).

The point of these tests is the one in the issue's "done when": *proven to fire*.
A watchdog nobody has seen bark is not known to work, and this one guards a
failure that already happened once — Node 20 sat three months past EOL in three
places, and the only reason it surfaced was an unrelated Dependabot PR getting
read carefully.

So the tests drive the checker against deliberately stale pins and assert it
reports them, rather than only confirming that today's pins are fine (which they
are, and which would pass just as well if the whole thing were a no-op).

No network: `evaluate` takes the API payload as an argument precisely so the
decision is testable offline.
"""
import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_eol.py"


def _load():
    """Import the script by path — it lives in scripts/, not on sys.path."""
    spec = importlib.util.spec_from_file_location("check_eol", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: @dataclass resolves annotations via
    # sys.modules[cls.__module__], which is absent for a path-loaded module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check_eol = _load()
Pin = check_eol.Pin


# Trimmed copies of the real endoflife.date payloads.
CYCLES = {
    "python": [
        {"cycle": "3.14", "eol": "2030-10-31"},
        {"cycle": "3.13", "eol": "2029-10-31"},
        {"cycle": "3.12", "eol": "2028-10-31"},
        {"cycle": "3.9", "eol": "2025-10-31"},
    ],
    "nodejs": [
        {"cycle": "24", "eol": "2028-04-30", "lts": True},
        {"cycle": "22", "eol": "2027-04-30", "lts": True},
        {"cycle": "20", "eol": "2026-04-30", "lts": True},
    ],
    # nginx stable reports eol: false — supported, no announced end.
    "nginx": [
        {"cycle": "1.31", "eol": False},
        {"cycle": "1.29", "eol": "2026-05-13"},
    ],
}


# ── It fires ──────────────────────────────────────────────────────────────────


def test_fires_on_a_runtime_already_past_eol():
    """The Node 20 case, replayed. This is the regression that motivated #47."""
    pins = [Pin("nodejs", "20", "frontend/Dockerfile:6")]
    findings = check_eol.evaluate(pins, CYCLES, today=date(2026, 7, 28))
    assert len(findings) == 1
    assert findings[0].expired
    assert findings[0].days_left < 0


def test_fires_before_eol_inside_the_warning_window():
    """Warning only once it has expired would defeat the purpose."""
    pins = [Pin("nodejs", "20", "frontend/Dockerfile:6")]
    # 2026-01-01 is ~119 days ahead of node 20's 2026-04-30.
    findings = check_eol.evaluate(pins, CYCLES, today=date(2026, 1, 1))
    assert len(findings) == 1
    assert not findings[0].expired
    assert 0 < findings[0].days_left <= 180


def test_silent_outside_the_window():
    pins = [Pin("python", "3.12", "backend/Dockerfile:2")]
    assert check_eol.evaluate(pins, CYCLES, today=date(2026, 8, 5)) == []


def test_threshold_boundary_is_inclusive():
    """Exactly at the threshold must warn — a strict `<` here would skip a run."""
    pins = [Pin("nodejs", "20", "x")]
    exactly_180_before = date(2025, 11, 1)  # 2026-04-30 minus 180 days
    assert (CYCLES["nodejs"][2]["eol"]) == "2026-04-30"
    findings = check_eol.evaluate(pins, CYCLES, today=exactly_180_before, threshold_days=180)
    assert len(findings) == 1
    assert findings[0].days_left == 180


def test_findings_are_ordered_most_urgent_first():
    pins = [
        Pin("python", "3.12", "backend/Dockerfile:2"),
        Pin("nodejs", "20", "frontend/Dockerfile:6"),
    ]
    findings = check_eol.evaluate(pins, CYCLES, today=date(2026, 4, 1), threshold_days=10_000)
    assert [f.pin.product for f in findings] == ["nodejs", "python"]


# ── It does not fire on things that aren't EOL ────────────────────────────────


def test_rolling_release_with_no_announced_eol_is_not_reported():
    """nginx stable reports `eol: false`. Warning about it every month would
    train everyone to ignore this issue."""
    pins = [Pin("nginx", "1.31", "frontend/Dockerfile:15")]
    assert check_eol.evaluate(pins, CYCLES, today=date(2026, 8, 5)) == []


def test_a_pinned_nginx_with_a_real_eol_still_fires():
    """...but the rule must not be dead. Pin an older stable and it reports."""
    pins = [Pin("nginx", "1.29", "frontend/Dockerfile:15")]
    findings = check_eol.evaluate(pins, CYCLES, today=date(2026, 8, 5))
    assert len(findings) == 1 and findings[0].expired


def test_unknown_cycle_is_not_silently_treated_as_supported():
    """A cycle the API doesn't list yields no finding — documented here so the
    behaviour is a decision rather than an accident. discover_pins() is what
    guards against the pin itself going unnoticed."""
    pins = [Pin("python", "4.99", "backend/Dockerfile:2")]
    assert check_eol.evaluate(pins, CYCLES, today=date(2026, 8, 5)) == []


def test_eol_true_without_a_date_is_treated_as_expired():
    cycles = {"python": [{"cycle": "2.7", "eol": True}]}
    findings = check_eol.evaluate(
        [Pin("python", "2.7", "x")], cycles, today=date(2026, 8, 5)
    )
    assert len(findings) == 1 and findings[0].expired


# ── Pin discovery reads the real files ────────────────────────────────────────


def test_discovers_every_pin_including_the_ones_dependabot_cannot_see():
    """The `node-version:` inputs are the whole reason this exists — the
    github-actions ecosystem bumps setup-node, never the version it installs."""
    pins = check_eol.discover_pins(REPO_ROOT)
    where = {p.where for p in pins}

    assert any("backend/Dockerfile" in w for w in where)
    assert any("frontend/Dockerfile" in w for w in where)
    # The three Dependabot is structurally blind to:
    assert any("ci.yml" in w for w in where)
    assert any("e2e.yml" in w for w in where)
    assert any("eval.yml" in w for w in where)

    products = {p.product for p in pins}
    assert products == {"python", "nodejs", "nginx"}


def test_every_location_of_a_runtime_is_found_across_files():
    """Node 20 was stale in *three* places. Reporting one would have left two."""
    pins = check_eol.discover_pins(REPO_ROOT)
    node_pins = [p for p in pins if p.product == "nodejs"]
    assert len(node_pins) >= 3, f"only found {[p.where for p in node_pins]}"
    # And they agree, which is itself worth knowing.
    assert len({p.cycle for p in node_pins}) == 1


def _synthetic_repo(root: Path, ci_yml: str) -> None:
    """A minimal tree satisfying every discovery rule, so tests can vary one file.

    discover_pins() insists every rule matches, so a partial tree raises for the
    wrong reason.
    """
    (root / "backend").mkdir(parents=True, exist_ok=True)
    (root / "frontend").mkdir(parents=True, exist_ok=True)
    (root / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (root / "backend" / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (root / "frontend" / "Dockerfile").write_text(
        "FROM node:24-alpine AS build\nFROM nginx:1.31-alpine\n"
    )
    (root / ".github" / "workflows" / "ci.yml").write_text(ci_yml)
    (root / ".github" / "workflows" / "e2e.yml").write_text(
        'python-version: "3.12"\nnode-version: "24"\n'
    )
    (root / ".github" / "workflows" / "eval.yml").write_text('python-version: "3.12"\n')


def test_multiple_pins_in_one_file_are_all_reported(tmp_path):
    """The `for m in found` loop, tested directly.

    Today no single file pins the same runtime twice, so the repo-wide test
    above passes even if only the first match per file were kept — it was
    vacuous, and a mutation caught it. A workflow gaining a second job with its
    own `node-version` is an ordinary change, and half of it going unwatched is
    exactly the Node 20 failure again.
    """
    _synthetic_repo(
        tmp_path,
        'python-version: "3.12"\nnode-version: "24"\nnode-version: "20"\n',
    )
    pins = check_eol.discover_pins(tmp_path)
    ci_node = [p for p in pins if p.product == "nodejs" and "ci.yml" in p.where]
    assert len(ci_node) == 2, [p.where for p in ci_node]
    assert {p.cycle for p in ci_node} == {"24", "20"}
    # And the stale one is on different lines, so the issue body points at both.
    assert len({p.where for p in ci_node}) == 2


def test_a_stale_second_pin_in_one_file_actually_fires(tmp_path):
    """End to end: discovery + evaluation, on the shape above."""
    _synthetic_repo(
        tmp_path,
        'python-version: "3.12"\nnode-version: "24"\nnode-version: "20"\n',
    )
    findings = check_eol.evaluate(
        check_eol.discover_pins(tmp_path), CYCLES, today=date(2026, 7, 28)
    )
    stale = [f for f in findings if f.pin.cycle == "20"]
    assert len(stale) == 1 and stale[0].expired


def test_pins_carry_a_file_and_line_for_the_issue_body():
    for pin in check_eol.discover_pins(REPO_ROOT):
        path, _, line = pin.where.rpartition(":")
        assert (REPO_ROOT / path).exists(), pin.where
        assert line.isdigit(), pin.where


def test_a_pin_that_stops_matching_is_a_loud_error(tmp_path):
    """The blind spot this script exists to close.

    If a pin is renamed or moved and the rule silently matches nothing, the
    check reports "all clear" forever — worse than not having it, because it
    manufactures confidence. It must raise instead.
    """
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "Dockerfile").write_text("FROM ubuntu:24.04\n")
    with pytest.raises(LookupError) as exc:
        check_eol.discover_pins(tmp_path)
    assert "blind" in str(exc.value)


def test_current_pins_are_not_due(tmp_path):
    """Sanity: the repo as it stands today is fine. Deliberately last — on its
    own this test would pass against a completely broken checker."""
    pins = check_eol.discover_pins(REPO_ROOT)
    assert check_eol.evaluate(pins, CYCLES, today=date(2026, 8, 5)) == []


# ── Report ────────────────────────────────────────────────────────────────────


def test_report_names_the_file_the_version_and_the_deadline():
    findings = check_eol.evaluate(
        [Pin("nodejs", "20", "frontend/Dockerfile:6")], CYCLES, today=date(2026, 7, 28)
    )
    body = check_eol.format_report(findings, 180)
    assert "nodejs" in body
    assert "frontend/Dockerfile:6" in body
    assert "2026-04-30" in body
    assert "past EOL" in body


def test_report_warns_against_jumping_to_a_non_lts_line():
    """Dependabot proposed Node 20 → 26 (Current, not LTS) and was rightly
    rejected (#42). The issue body should carry that lesson to whoever reads it
    two years from now."""
    findings = check_eol.evaluate(
        [Pin("nodejs", "20", "frontend/Dockerfile:6")], CYCLES, today=date(2026, 7, 28)
    )
    body = check_eol.format_report(findings, 180)
    assert "LTS" in body
    assert "moves together" in body


def test_clean_report_says_so():
    body = check_eol.format_report([], 180)
    assert "No pinned runtime" in body
    assert "|" not in body  # no empty table
