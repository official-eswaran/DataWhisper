"""
chart_advisor.py — Smart chart type recommendation engine.

Given a pandas DataFrame and an optional natural-language question,
returns the most appropriate chart type string consumed by the frontend.

Supported types (mirrored in ResultView.js):
  single_value  — 1 row × 1 col scalar
  pie           — categorical label + numeric value, ≤ 8 distinct labels
  bar           — categorical label + numeric value, 9–40 items
  line          — time-series or ordered numeric X axis
  area          — cumulative / percentage over time
  scatter       — two numeric columns (correlation)
  histogram     — single numeric column (distribution)
  multi_series  — label col + 2+ numeric cols (grouped bar / multi-line)
  table         — everything else
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

# ── Regex patterns for date/time column name heuristics ───────────────────────
_DATE_NAME_RE = re.compile(
    r"\b(date|time|year|month|day|week|quarter|timestamp|period|dt)\b",
    re.IGNORECASE,
)

# Keywords in the question that hint at a specific chart
_TREND_WORDS = re.compile(r"\b(trend|over time|by year|by month|by week|over the|growth|change)\b", re.IGNORECASE)
_DIST_WORDS  = re.compile(r"\b(distribution|spread|range|histogram|frequency|how.*distribut)\b", re.IGNORECASE)
_CORR_WORDS  = re.compile(r"\b(correlat|relationship|vs\.?|versus|scatter|compare.*with)\b", re.IGNORECASE)
_SHARE_WORDS = re.compile(r"\b(share|percent|proportion|breakdown|composition|ratio|split)\b", re.IGNORECASE)
_CUM_WORDS   = re.compile(r"\b(cumulative|running total|running sum|accumulat)\b", re.IGNORECASE)


def _is_datetime_col(series) -> bool:
    """Return True if a series looks like a date/time column."""
    import pandas as pd
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    if pd.api.types.is_integer_dtype(series):
        # Year-like integers: 1990–2100
        if series.between(1990, 2100).all():
            return True
    if pd.api.types.is_object_dtype(series):
        # Try parsing a sample
        sample = series.dropna().head(5)
        try:
            pd.to_datetime(sample, infer_datetime_format=True)
            return True
        except Exception:
            return False
    return False


def _numeric_columns(df) -> list[str]:
    import pandas as pd
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]


def _categorical_columns(df) -> list[str]:
    import pandas as pd
    return [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]


def recommend_chart_type(df, question: str = "") -> str:
    """
    Core recommendation function.

    Parameters
    ----------
    df       : pandas DataFrame (query result)
    question : original natural-language question (optional, improves accuracy)

    Returns
    -------
    One of the chart type strings listed in the module docstring.
    """
    rows, cols = len(df), len(df.columns)

    # ── Trivial cases ─────────────────────────────────────────────────────────
    if rows == 0:
        return "table"

    if rows == 1 and cols == 1:
        return "single_value"

    numeric_cols  = _numeric_columns(df)
    category_cols = _categorical_columns(df)
    n_numeric     = len(numeric_cols)
    n_category    = len(category_cols)

    # ── Single numeric column — distribution histogram ────────────────────────
    if cols == 1 and n_numeric == 1:
        if rows >= 10 or _DIST_WORDS.search(question):
            return "histogram"
        return "single_value"

    # ── Two numeric columns — scatter (correlation) ───────────────────────────
    if n_numeric == 2 and n_category == 0:
        if _CORR_WORDS.search(question) or rows >= 20:
            return "scatter"

    # ── Exactly two columns: label + value ───────────────────────────────────
    if cols == 2 and n_numeric >= 1:
        label_col = category_cols[0] if category_cols else df.columns[0]
        is_time   = _is_datetime_col(df[label_col]) or bool(_DATE_NAME_RE.search(label_col))

        # Cumulative / area hint
        if _CUM_WORDS.search(question) and is_time:
            return "area"

        # Time series / trend → line
        if is_time or _TREND_WORDS.search(question):
            return "line"

        # Share / proportion → pie (only for small cardinality)
        if _SHARE_WORDS.search(question) and rows <= 8:
            return "pie"

        # Distribution hint → histogram (if label is numeric)
        if n_numeric == 2 and _DIST_WORDS.search(question):
            return "histogram"

        # Categorical label
        if n_category == 1:
            distinct = df[label_col].nunique()
            if distinct <= 8:
                return "pie"
            if distinct <= 40:
                return "bar"
            return "line"   # too many categories → line is more readable

        return "bar"

    # ── 3+ columns with one label + multiple numeric → multi_series ──────────
    if n_category == 1 and n_numeric >= 2:
        label_col = category_cols[0]
        is_time   = _is_datetime_col(df[label_col]) or bool(_DATE_NAME_RE.search(label_col))
        if is_time or _TREND_WORDS.search(question):
            return "multi_series"   # will render as multi-line
        return "multi_series"       # will render as grouped bar

    # ── Multiple numeric columns, no label — scatter or table ────────────────
    if n_category == 0 and n_numeric >= 2:
        if n_numeric == 2:
            return "scatter"
        return "table"

    # ── Fallback ──────────────────────────────────────────────────────────────
    return "table"
