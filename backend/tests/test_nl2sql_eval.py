"""Tests for the NL2SQL eval harness — no LLM, no Ollama.

The accuracy number the eval reports is only trustworthy if two things hold:
the comparison accepts exactly the right answers and no others, and the case set
is sound. Both are testable without the model, so they are tested here in the
normal suite rather than only under the eval workflow.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pandas as pd
import pytest

from evals.cases import CASES
from evals.check_cases import validate_cases
from evals.compare import canonical_rows, normalize_value, results_match

# ── normalize_value ───────────────────────────────────────────────────────────


def test_numeric_types_are_interchangeable():
    assert normalize_value(110000) == normalize_value(110000.0) == normalize_value(Decimal("110000.00"))


def test_floats_compare_to_two_places():
    # 23382.352941176472 is a real expected value in the case set; a result
    # reported to fewer places is the same answer.
    assert normalize_value(23382.352941176472) == normalize_value(23382.35)
    assert normalize_value(1 / 3) == normalize_value(0.33333)
    assert normalize_value(4.04) != normalize_value(4.06)


def test_bool_is_not_collapsed_into_int():
    # bool is an int subclass, so a naive numeric branch would make True == 1.
    assert normalize_value(True) != normalize_value(1)


def test_missing_values_normalize_to_none():
    assert normalize_value(None) is None
    assert normalize_value(float("nan")) is None
    assert normalize_value(pd.NaT) is None


def test_strings_ignore_case_and_padding():
    assert normalize_value("  Electronics ") == normalize_value("electronics")


def test_dates_match_across_representations():
    day = normalize_value(dt.date(2024, 1, 15))
    assert day == normalize_value(dt.datetime(2024, 1, 15, 0, 0))
    assert day == normalize_value(pd.Timestamp("2024-01-15"))
    assert day == normalize_value("2024-01-15")


def test_datetime_with_a_real_time_is_not_flattened_to_a_date():
    assert normalize_value(dt.datetime(2024, 1, 15, 9, 30)) != normalize_value(dt.date(2024, 1, 15))


# ── results_match ─────────────────────────────────────────────────────────────

def _df(rows, columns):
    return pd.DataFrame(rows, columns=columns)


def test_column_names_are_ignored():
    expected = _df([["East", 1036000]], ["region", "sum(total_amount)"])
    actual = _df([["East", 1036000.0]], ["reg", "revenue"])
    assert results_match(actual, expected)


def test_row_order_is_tolerated_when_the_question_did_not_ask_for_one():
    expected = _df([["East", 2], ["West", 1]], ["region", "n"])
    actual = _df([["West", 1], ["East", 2]], ["region", "n"])
    assert results_match(actual, expected)


def test_row_order_is_enforced_for_ranked_questions():
    expected = _df([["Smartphone"], ["Laptop"]], ["product"])
    actual = _df([["Laptop"], ["Smartphone"]], ["product"])
    assert results_match(actual, expected)  # unordered: same set
    assert not results_match(actual, expected, ordered=True)


def test_column_order_is_tolerated():
    expected = _df([["East", 1036000]], ["region", "revenue"])
    actual = _df([[1036000, "East"]], ["revenue", "region"])
    assert results_match(actual, expected)


def test_extra_columns_are_rejected_unless_the_case_allows_them():
    expected = _df([["East"]], ["region"])
    actual = _df([["East", 1036000]], ["region", "revenue"])
    assert not results_match(actual, expected)
    assert results_match(actual, expected, subset_ok=True)


def test_missing_columns_are_always_wrong():
    expected = _df([["East", 1036000]], ["region", "revenue"])
    actual = _df([["East"]], ["region"])
    assert not results_match(actual, expected, subset_ok=True)


def test_a_different_row_count_is_wrong():
    expected = _df([["East"], ["West"]], ["region"])
    actual = _df([["East"]], ["region"])
    assert not results_match(actual, expected)


def test_a_wrong_value_is_wrong():
    expected = _df([[3015500]], ["total"])
    actual = _df([[3015501]], ["total"])
    assert not results_match(actual, expected)


def test_subset_match_requires_the_same_rows_not_merely_the_same_columns():
    expected = _df([["East"], ["West"]], ["region"])
    actual = _df([["East", 1], ["South", 2]], ["region", "n"])
    assert not results_match(actual, expected, subset_ok=True)


def test_canonical_rows_sorts_mixed_types_without_raising():
    frame = _df([[None], ["b"], [1]], ["c"])
    assert len(canonical_rows(frame, ordered=False)) == 3


# ── the case set ──────────────────────────────────────────────────────────────

def test_shipped_case_set_is_valid():
    assert validate_cases() == []


def test_case_set_is_substantial_and_broad():
    assert len(CASES) >= 50
    assert len({c.category for c in CASES}) >= 6


def test_every_case_id_is_unique():
    ids = [c.id for c in CASES]
    assert len(ids) == len(set(ids))


# ── the checker itself ────────────────────────────────────────────────────────
# If validate_cases() cannot fail, "the case set is valid" means nothing.

def _case(**overrides):
    from evals.cases import Case

    base = {
        "id": "planted",
        "dataset": "sales_data",
        "question": "q",
        "category": "test",
        "reference_sql": "SELECT COUNT(*) FROM sales_data",
    }
    base.update(overrides)
    return Case(**base)


def test_checker_rejects_a_reference_query_that_returns_nothing():
    bad = _case(reference_sql="SELECT * FROM sales_data WHERE region = 'Atlantis'")
    problems = validate_cases([bad])
    assert any("no rows" in p for p in problems)


def test_checker_rejects_a_reference_query_that_does_not_run():
    problems = validate_cases([_case(reference_sql="SELECT nope FROM sales_data")])
    assert any("reference_sql failed" in p for p in problems)


def test_checker_rejects_an_ordered_case_without_a_tie_guard():
    bad = _case(reference_sql="SELECT region FROM sales_data LIMIT 1", ordered=True)
    problems = validate_cases([bad])
    assert any("rank_sql" in p for p in problems)


def test_checker_detects_a_tie_at_the_cutoff():
    # Both categories tie at 1 order each once filtered to a single order per
    # category, so "the top category" has two correct answers.
    bad = _case(
        reference_sql=(
            "SELECT category FROM sales_data WHERE order_id IN (1001, 1002) "
            "GROUP BY category ORDER BY COUNT(*) DESC LIMIT 1"
        ),
        ordered=True,
        rank_sql=(
            "SELECT COUNT(*) FROM sales_data WHERE order_id IN (1001, 1002) "
            "GROUP BY category ORDER BY 1 DESC"
        ),
    )
    problems = validate_cases([bad])
    assert any("tie at the cutoff" in p for p in problems)


def test_checker_rejects_a_duplicate_case_id():
    problems = validate_cases([_case(), _case()])
    assert any("duplicate case id" in p for p in problems)


def test_checker_rejects_a_chat_case_carrying_sql():
    problems = validate_cases([_case(kind="chat")])
    assert any("must not carry reference_sql" in p for p in problems)


def test_checker_rejects_an_unknown_dataset():
    problems = validate_cases([_case(dataset="nope")])
    assert any("unknown dataset" in p for p in problems)


@pytest.mark.parametrize("case", [c for c in CASES if c.kind == "chat"])
def test_chat_cases_carry_no_sql(case):
    assert case.reference_sql is None
