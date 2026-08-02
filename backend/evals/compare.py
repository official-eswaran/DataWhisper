"""Tolerant result comparison — the definition of "correct" for this eval.

Many different SQL strings answer a question correctly, so comparing SQL text
would measure phrasing rather than correctness. This module compares the
*result* the query denotes (execution accuracy), tolerating the differences that
do not change the answer and nothing else.

Tolerated:

* **Column names.** ``SUM(total_amount)``, ``total``, ``revenue`` are the same
  answer. Values are compared positionally; names are ignored entirely.
* **Column order** for narrow results (see ``_MAX_PERMUTE_COLUMNS``) —
  ``(region, revenue)`` and ``(revenue, region)`` carry the same information.
* **Row order**, unless the question asked for an ordering (``ordered=True``),
  in which case the order *is* part of the answer.
* **Numeric type and precision.** ``110000``, ``110000.0`` and
  ``Decimal("110000.00")`` all match; floats compare to ``DECIMALS`` places.
* **String case and surrounding whitespace**, so ``GROUP BY lower(category)``
  is not marked wrong.
* **Date/time representation.** A ``date``, a ``Timestamp`` at midnight and
  ``"2024-01-15"`` are the same day.

Not tolerated: a different number of rows or columns, extra columns the question
did not ask for, or any different value. Those change the answer.
"""
from __future__ import annotations

import datetime as _dt
import math
from decimal import Decimal
from itertools import permutations

import pandas as pd

DECIMALS = 2

# Above this width, require the model's column order to match the reference.
# 4 columns is 24 permutations; the factorial growth past that costs more than
# the tolerance is worth, and wide results with reordered columns are rare.
_MAX_PERMUTE_COLUMNS = 4


def normalize_value(value, decimals: int = DECIMALS):
    """Reduce one cell to a comparable canonical form."""
    if value is None or value is pd.NaT:
        return None
    # numpy scalars -> python scalars
    item = getattr(value, "item", None)
    if item is not None and not isinstance(value, (str, bytes)):
        try:
            value = item()
        except (ValueError, AttributeError):
            pass

    if isinstance(value, float) and math.isnan(value):
        return None
    # Tagged, and checked before int: bool is an int subclass *and* True == 1.0,
    # so simply returning the bool would still let it compare equal to a number.
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, (int, float, Decimal)):
        return round(float(value), decimals)
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if isinstance(value, _dt.datetime):
        # A date-only column round-trips as midnight; compare it as a date so
        # DATE and TIMESTAMP renderings of the same day agree.
        return value.date().isoformat() if value.time() == _dt.time.min else value.isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        # A stringified date should match a real one.
        try:
            parsed = _dt.datetime.fromisoformat(text)
        except ValueError:
            return text.casefold()
        return parsed.date().isoformat() if parsed.time() == _dt.time.min else parsed.isoformat()
    return str(value).strip().casefold()


def canonical_rows(df: pd.DataFrame, ordered: bool, decimals: int = DECIMALS) -> list[tuple]:
    """Canonical row list: values only, column names discarded."""
    rows = [tuple(normalize_value(v, decimals) for v in row) for row in df.itertuples(index=False)]
    if not ordered:
        # repr gives a total order across mixed/None types that plain sorting lacks.
        rows.sort(key=repr)
    return rows


def results_match(
    actual: pd.DataFrame,
    expected: pd.DataFrame,
    ordered: bool = False,
    subset_ok: bool = False,
    decimals: int = DECIMALS,
) -> bool:
    """True when ``actual`` denotes the same answer as ``expected``.

    With ``subset_ok`` the actual result may carry extra columns, as long as
    some selection of its columns reproduces the expected answer exactly. This
    covers "which region had the most revenue?", where returning the region
    alone and returning the region with its total are both right.
    """
    if actual is None or expected is None:
        return False
    if len(actual) != len(expected):
        return False

    n_expected = expected.shape[1]
    n_actual = actual.shape[1]
    if n_actual < n_expected:
        return False
    if n_actual > n_expected and not subset_ok:
        return False

    expected_rows = canonical_rows(expected, ordered, decimals)
    # Identity first: the common case, and free.
    if n_actual == n_expected and canonical_rows(actual, ordered, decimals) == expected_rows:
        return True
    # A single expected column already covered by the identity check above.
    if n_actual == n_expected == 1:
        return False
    if n_actual > _MAX_PERMUTE_COLUMNS:
        return False

    for selection in permutations(range(n_actual), n_expected):
        if canonical_rows(actual.iloc[:, list(selection)], ordered, decimals) == expected_rows:
            return True
    return False


def describe(df: pd.DataFrame, max_rows: int = 5) -> str:
    """Compact rendering of a result for failure reports."""
    if df is None:
        return "<no result>"
    if df.empty:
        return f"<empty, columns={list(df.columns)}>"
    body = df.head(max_rows).to_string(index=False)
    if len(df) > max_rows:
        body += f"\n… {len(df) - max_rows} more row(s)"
    return body
