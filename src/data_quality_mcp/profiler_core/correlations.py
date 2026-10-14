"""Pearson (numeric) and Cramér's V (categorical) associations.

Ported from the Data Quality Analyzer (`profiler/correlations.py`) with the
same formulas, threshold and ordering. Deviation (see planning/milestone-2.md):
the numeric column count and categorical cardinality are bounded, to keep the
result size and runtime predictable on wide data.
"""

import numpy as np
import pandas as pd
from scipy.stats import chi2_contingency

from data_quality_mcp import config
from data_quality_mcp.profiler_core.schema import CorrelationInfo, HighCorrelationPair

_NUMERIC_TYPES = ("numeric", "mixed")
_CATEGORICAL_TYPES = ("categorical", "boolean")


def cramers_v(x: pd.Series, y: pd.Series) -> float:
    """Cramér's V association between two categorical series (0 to 1)."""
    table = pd.crosstab(x.dropna(), y.dropna())
    if table.shape[0] < 2 or table.shape[1] < 2:
        return 0.0
    chi2 = float(chi2_contingency(table)[0])
    n = int(table.to_numpy().sum())
    min_dim = min(table.shape) - 1
    if min_dim == 0 or n == 0:
        return 0.0
    return float(np.sqrt(chi2 / (n * min_dim)))


def compute_correlations(
    df: pd.DataFrame,
    type_info: dict[str, dict],
    *,
    threshold: float = config.HIGH_CORRELATION_THRESHOLD,
    max_numeric_columns: int = config.MAX_CORRELATION_NUMERIC_COLUMNS,
    max_cramers_columns: int = config.MAX_CRAMERS_V_COLUMNS,
    max_cramers_cardinality: int = config.MAX_CRAMERS_V_CARDINALITY,
) -> CorrelationInfo:
    """Pearson matrix over numeric/mixed columns, Cramér's V over categorical
    and boolean columns (upper triangle), and every pair whose |value| reaches
    `threshold`, strongest first."""
    numeric_cols = [
        c for c in df.columns if type_info[c]["inferred_type"] in _NUMERIC_TYPES
    ][:max_numeric_columns]
    cat_cols = [
        c
        for c in df.columns
        if type_info[c]["inferred_type"] in _CATEGORICAL_TYPES
        and df[c].nunique(dropna=True) <= max_cramers_cardinality
    ][:max_cramers_columns]

    pearson: dict[str, dict[str, float | None]] = {}
    cramers: dict[str, dict[str, float]] = {}
    high_pairs: list[HighCorrelationPair] = []

    if len(numeric_cols) >= 2:
        matrix = df[numeric_cols].apply(pd.to_numeric, errors="coerce").corr()
        for col_a in numeric_cols:
            pearson[col_a] = {
                col_b: None
                if pd.isna(matrix.loc[col_a, col_b])
                else round(float(matrix.loc[col_a, col_b]), 4)
                for col_b in numeric_cols
            }
        for i, col_a in enumerate(numeric_cols):
            for col_b in numeric_cols[i + 1 :]:
                value = pearson[col_a][col_b]
                if value is not None and abs(value) >= threshold:
                    high_pairs.append(
                        {
                            "col_a": col_a,
                            "col_b": col_b,
                            "method": "pearson",
                            "value": value,
                        }
                    )

    if len(cat_cols) >= 2:
        for i, col_a in enumerate(cat_cols):
            cramers[col_a] = {}
            for col_b in cat_cols[i + 1 :]:
                value = round(cramers_v(df[col_a], df[col_b]), 4)
                cramers[col_a][col_b] = value
                if value >= threshold:
                    high_pairs.append(
                        {
                            "col_a": col_a,
                            "col_b": col_b,
                            "method": "cramers_v",
                            "value": value,
                        }
                    )

    high_pairs.sort(key=lambda pair: abs(pair["value"]), reverse=True)
    return {
        "pearson": pearson,
        "cramers_v": cramers,
        "high_correlation_pairs": high_pairs,
    }
