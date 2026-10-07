"""Missing-value counts and percentages.

Simplified from the Data Quality Analyzer: the heuristic MCAR/MAR/MNAR
classification is dropped because it is not part of the report schema.
"""

import pandas as pd


def analyze_missing(df: pd.DataFrame) -> dict:
    """Return overall and per-column missing-value statistics.

    Returns:
        {
            "overall_missing_pct": float,   # % of all cells that are missing
            "total_missing_cells": int,
            "per_column": {col: {"missing_count": int, "missing_pct": float}},
        }
    """
    n_rows = len(df)
    per_column_counts = df.isna().sum()
    total_missing = int(per_column_counts.sum())
    total_cells = df.size

    per_column = {
        col: {
            "missing_count": int(per_column_counts[col]),
            "missing_pct": (
                round(per_column_counts[col] / n_rows * 100, 2) if n_rows else 0.0
            ),
        }
        for col in df.columns
    }
    return {
        "overall_missing_pct": (
            round(total_missing / total_cells * 100, 2) if total_cells else 0.0
        ),
        "total_missing_cells": total_missing,
        "per_column": per_column,
    }
