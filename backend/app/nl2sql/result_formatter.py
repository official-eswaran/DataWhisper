"""Shared result-formatting helpers.

Previously the NL→SQL result shaping (NaN scrubbing, summary text, response-type
detection, final envelope) was copy-pasted between the pipeline and the
streaming route, and the two copies had already drifted. This module is the
single source of truth both call paths use.
"""
from __future__ import annotations

import math

import pandas as pd

from app.visualization.chart_advisor import recommend_chart_type


def clean_records(df: pd.DataFrame) -> list[dict]:
    """Convert a DataFrame to JSON-safe records (NaN/Inf -> None)."""
    return [
        {
            k: (None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v)
            for k, v in row.items()
        }
        for row in df.to_dict(orient="records")
    ]


def detect_response_type(df: pd.DataFrame, question: str = "") -> str:
    return recommend_chart_type(df, question)


def generate_summary(question: str, df: pd.DataFrame) -> str:
    rows, cols = len(df), len(df.columns)

    if rows == 0:
        return "No results found for your query."

    if rows == 1 and cols == 1:
        val = df.iloc[0, 0]
        col = df.columns[0]
        if isinstance(val, float):
            formatted = f"{val:,.2f}"
        elif isinstance(val, int):
            formatted = f"{val:,}"
        else:
            formatted = str(val)
        return f"{col}: **{formatted}**"

    if rows == 1:
        parts = []
        for col in df.columns[:5]:
            val = df.iloc[0][col]
            if isinstance(val, float):
                parts.append(f"{col}: {val:,.2f}")
            elif val is not None:
                parts.append(f"{col}: {val}")
        return "Result — " + " | ".join(parts)

    if cols == 2:
        label_col, value_col = df.columns[0], df.columns[1]
        if pd.api.types.is_numeric_dtype(df[value_col]):
            total = df[value_col].sum()
            top_row = df.loc[df[value_col].idxmax()]
            top_val = top_row[value_col]
            top_lbl = top_row[label_col]
            if isinstance(total, float):
                total_str, top_str = f"{total:,.2f}", f"{top_val:,.2f}"
            else:
                total_str, top_str = f"{int(total):,}", f"{int(top_val):,}"
            return (
                f"{rows} results — highest: **{top_lbl}** ({top_str}), "
                f"total {value_col}: {total_str}"
            )
        return f"{rows} results for {label_col}."

    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    if numeric_cols:
        summaries = []
        for col in numeric_cols[:3]:
            col_sum = df[col].sum()
            if isinstance(col_sum, float):
                summaries.append(f"{col} total: {col_sum:,.2f}")
            else:
                summaries.append(f"{col} total: {int(col_sum):,}")
        return f"{rows} rows — " + " | ".join(summaries)

    return f"Found {rows} rows across {cols} columns."


def build_result(df: pd.DataFrame, question: str, sql: str) -> dict:
    """Assemble the final API response envelope from a result DataFrame."""
    return {
        "type": detect_response_type(df, question),
        "data": clean_records(df),
        "columns": list(df.columns),
        "sql": sql,
        "row_count": len(df),
        "summary": generate_summary(question, df),
    }


def chat_result(summary: str) -> dict:
    return {
        "type": "chat",
        "data": [],
        "columns": [],
        "sql": None,
        "row_count": 0,
        "summary": summary,
    }
