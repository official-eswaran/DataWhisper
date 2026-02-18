import pandas as pd


def detect_anomalies(df: pd.DataFrame, table_name: str) -> list[dict]:
    """Proactively detect anomalies in uploaded data."""
    anomalies = []

    # Check for missing values
    null_counts = df.isnull().sum()
    for col, count in null_counts.items():
        if count > 0:
            pct = round(count / len(df) * 100, 1)
            if pct > 20:
                anomalies.append({
                    "type": "missing_data",
                    "severity": "high" if pct > 50 else "medium",
                    "message": f"Column '{col}' has {pct}% missing values ({count}/{len(df)} rows)",
                })

    # Check numeric columns for outliers (IQR method)
    for col in df.select_dtypes(include=["number"]).columns:
        q1 = df[col].quantile(0.25)
        q3 = df[col].quantile(0.75)
        iqr = q3 - q1
        if iqr == 0:
            continue
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        outlier_count = ((df[col] < lower) | (df[col] > upper)).sum()
        if outlier_count > 0:
            anomalies.append({
                "type": "outlier",
                "severity": "medium",
                "message": f"Column '{col}' has {outlier_count} outlier values (outside {lower:.1f} - {upper:.1f})",
            })

    # Check for duplicate rows
    dup_count = df.duplicated().sum()
    if dup_count > 0:
        anomalies.append({
            "type": "duplicates",
            "severity": "low",
            "message": f"Found {dup_count} duplicate rows in '{table_name}'",
        })

    # Check for sudden changes in date-sorted numeric columns
    date_cols = df.select_dtypes(include=["datetime64"]).columns
    if len(date_cols) > 0:
        date_col = date_cols[0]
        sorted_df = df.sort_values(date_col)
        for col in df.select_dtypes(include=["number"]).columns:
            pct_change = sorted_df[col].pct_change().abs()
            big_changes = pct_change[pct_change > 0.5]
            if len(big_changes) > 0:
                anomalies.append({
                    "type": "sudden_change",
                    "severity": "high",
                    "message": f"Column '{col}' has {len(big_changes)} sudden changes (>50%) over time",
                })

    return anomalies
