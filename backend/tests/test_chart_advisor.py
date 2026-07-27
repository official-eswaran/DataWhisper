"""Chart-type recommendation (issue #28 — this module was at 20% coverage).

Every branch here decides what a user sees after asking a question, and it was
almost entirely untested. The cases below are the actual decision table, not
smoke tests: each asserts the chart a specific shape of result should get.
"""
import pandas as pd
import pytest

from app.visualization.chart_advisor import recommend_chart_type


def _df(**cols):
    return pd.DataFrame(cols)


# ── Trivial shapes ─────────────────────────────────────────────────────────

def test_empty_result_is_a_table():
    assert recommend_chart_type(pd.DataFrame()) == "table"


def test_single_cell_is_a_scalar():
    assert recommend_chart_type(_df(total=[42])) == "single_value"


def test_short_single_numeric_column_is_still_scalar():
    assert recommend_chart_type(_df(revenue=[1, 2, 3])) == "single_value"


def test_long_single_numeric_column_is_a_histogram():
    assert recommend_chart_type(_df(revenue=list(range(10)))) == "histogram"


def test_distribution_question_forces_a_histogram():
    assert recommend_chart_type(_df(revenue=[1, 2, 3]), "what is the distribution") == "histogram"


# ── Two numeric columns ────────────────────────────────────────────────────

def test_many_numeric_pairs_scatter():
    df = _df(x=list(range(25)), y=list(range(25)))
    assert recommend_chart_type(df) == "scatter"


def test_correlation_question_scatters_even_when_short():
    df = _df(height=[1, 2, 3], weight=[4, 5, 6])
    assert recommend_chart_type(df, "is height correlated with weight?") == "scatter"


# ── Label + value ──────────────────────────────────────────────────────────

def test_time_labelled_series_is_a_line():
    df = _df(month=["2024-01", "2024-02", "2024-03", "2024-04"], revenue=[1, 2, 3, 4])
    assert recommend_chart_type(df) == "line"


def test_trend_question_is_a_line():
    df = _df(region=["N", "S", "E", "W", "C"], revenue=[1, 2, 3, 4, 5])
    assert recommend_chart_type(df, "show the trend") == "line"


def test_cumulative_question_over_time_is_an_area():
    df = _df(date=pd.to_datetime(["2024-01-01", "2024-02-01"]), revenue=[1, 2])
    assert recommend_chart_type(df, "running total of revenue") == "area"


def test_share_question_with_small_cardinality_is_a_pie():
    df = _df(region=["N", "S", "E", "W", "C"], revenue=[1, 2, 3, 4, 5])
    assert recommend_chart_type(df, "what is the breakdown by region?") == "pie"


def test_very_small_category_set_is_a_pie_without_a_hint():
    df = _df(region=["N", "S"], revenue=[1, 2])
    assert recommend_chart_type(df) == "pie"


def test_medium_category_set_is_a_bar():
    labels = [f"cat{i}" for i in range(10)]
    assert recommend_chart_type(_df(category=labels, revenue=list(range(10)))) == "bar"


def test_huge_category_set_falls_back_to_line():
    labels = [f"cat{i}" for i in range(50)]
    assert recommend_chart_type(_df(category=labels, revenue=list(range(50)))) == "line"


def test_named_entity_lists_stay_tables():
    """A ranked list of people is a list, not a chart."""
    names = [f"person{i}" for i in range(10)]
    assert recommend_chart_type(_df(employee_name=names, salary=list(range(10)))) == "table"


def test_named_entity_with_few_rows_still_charts():
    df = _df(customer_name=["a", "b", "c"], spend=[1, 2, 3])
    assert recommend_chart_type(df) == "pie"


@pytest.mark.parametrize("label", [
    "employee_name", "customer_name", "full_name", "first_name", "last_name",
    "emp_name", "username", "contact",
])
def test_entity_columns_are_recognised_in_snake_case(label):
    """_name_words() is what lets the bare 'name' alternative reach these.

    Without the separator split, `\\b(name)\\b` cannot match `customer_name` —
    `_` is a word character — so every one of these silently charted as a bar.
    """
    df = pd.DataFrame({label: [f"v{i}" for i in range(10)], "salary": list(range(10))})
    assert recommend_chart_type(df) == "table"


@pytest.mark.parametrize("label", [
    "last_login", "first_seen", "emp_id", "full_amount", "last_status",
])
def test_stem_lookalike_columns_are_not_treated_as_entity_names(label):
    """first/last/full/emp are not entity names on their own.

    Widening _NAME_COL_RE to those bare stems would add nothing (the cases above
    already match via 'name') while forcing these chartable results to a table.
    """
    df = pd.DataFrame({label: [f"v{i}" for i in range(10)], "total": list(range(10))})
    assert recommend_chart_type(df) == "bar"


# ── Wider results ──────────────────────────────────────────────────────────

def test_label_plus_two_numerics_is_multi_series():
    df = _df(region=["N", "S"], revenue=[1, 2], cost=[3, 4])
    assert recommend_chart_type(df) == "multi_series"


def test_time_label_plus_two_numerics_is_multi_series():
    df = _df(month=["2024-01", "2024-02"], revenue=[1, 2], cost=[3, 4])
    assert recommend_chart_type(df, "trend over time") == "multi_series"


def test_time_like_numeric_column_is_promoted_to_a_line_axis():
    """EXTRACT(year …) comes back numeric but is really the X axis."""
    df = _df(year=[2020, 2021, 2022], revenue=[1, 2, 3])
    assert recommend_chart_type(df) == "line"


def test_time_like_numeric_with_several_values_is_multi_series():
    df = _df(year=[2020, 2021], revenue=[1, 2], cost=[3, 4])
    assert recommend_chart_type(df) == "multi_series"


def test_three_plain_numerics_is_a_table():
    df = _df(a=[1.5, 2.5], b=[3.5, 4.5], c=[5.5, 6.5])
    assert recommend_chart_type(df) == "table"


def test_all_categorical_is_a_table():
    df = _df(a=["x", "y"], b=["p", "q"], c=["m", "n"])
    assert recommend_chart_type(df) == "table"


# ── Date detection ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("label", ["order_date", "timestamp", "quarter", "week"])
def test_date_named_columns_are_treated_as_time(label):
    df = pd.DataFrame({label: ["a", "b", "c"], "revenue": [1, 2, 3]})
    assert recommend_chart_type(df) == "line"


def test_unparseable_object_column_is_not_a_date():
    df = _df(region=["north", "south", "east", "west"], revenue=[1, 2, 3, 4])
    assert recommend_chart_type(df) == "bar"
