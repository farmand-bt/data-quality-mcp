"""Pearson (numeric) and Cramér's V (categorical) associations.

Ported from the Data Quality Analyzer (`profiler/correlations.py`) with the
same formulas, threshold and ordering. Deviations: the numeric column count and
categorical cardinality are bounded, to keep the result size and runtime
predictable on wide data (planning/milestone-2.md); a pair with fewer than
`min_rows` rows holding both values is None, because two rows always give
r = +/-1 (planning/milestone-3.5.md, D5); and Cramér's V is bias-corrected,
without Yates' correction, because the plain V of sparse tables reaches the
high-pair threshold between unrelated columns (milestone-3.5.md, Amendment:
bias-corrected Cramér's V; to be ported back).
"""

import numpy as np
import pandas as pd
from scipy.stats import chi2_contingency

from data_quality_mcp import config
from data_quality_mcp.profiler_core.schema import CorrelationInfo, HighCorrelationPair

_NUMERIC_TYPES = ("numeric", "mixed")
_CATEGORICAL_TYPES = ("categorical", "boolean")


def corrected_cramers_v(chi2: float, n: int, shape: tuple[int, int]) -> float:
    """Bias-corrected Cramér's V (Bergsma 2013) from a chi-square without
    Yates' correction on an r x c table of n rows. It subtracts what chance
    gives a table of that shape, so unrelated columns come out near 0 even on
    sparse tables, where the plain V can reach 0.9. 0.0 when no association
    remains, or when a side has a different value on every row."""
    rows, cols = shape
    phi2 = max(0.0, chi2 / n - (rows - 1) * (cols - 1) / (n - 1))
    rows_c = rows - (rows - 1) ** 2 / (n - 1)
    cols_c = cols - (cols - 1) ** 2 / (n - 1)
    denominator = min(rows_c - 1, cols_c - 1)
    return float(np.sqrt(phi2 / denominator)) if denominator > 0 else 0.0


def cramers_v(x: pd.Series, y: pd.Series) -> float:
    """Bias-corrected Cramér's V between two categorical series (0 to 1), over
    the rows where both are present; 0.0 when either has fewer than 2
    categories there."""
    return _cramers_v_codes(pd.factorize(x)[0], pd.factorize(y)[0])


def _shared_codes(codes: np.ndarray) -> tuple[np.ndarray, int]:
    """Renumber codes 0..k-1 by the categories that occur, and return k."""
    occurs = np.bincount(codes) > 0
    return (np.cumsum(occurs) - 1)[codes], int(occurs.sum())


def _cramers_v_codes(a: np.ndarray, b: np.ndarray) -> float:
    """cramers_v from factorize codes of the whole columns (-1 = missing), so
    each column is factorized once rather than once per pair."""
    both = (a >= 0) & (b >= 0)
    if not both.any():
        return 0.0
    # Only the categories on the shared rows, so every row and column of the
    # table has counts (as with pd.crosstab), which chi2_contingency requires
    a, ka = _shared_codes(a[both])
    b, kb = _shared_codes(b[both])
    if ka < 2 or kb < 2:
        return 0.0
    # The table by counting codes (much faster than pd.crosstab)
    table = np.bincount(a * kb + b, minlength=ka * kb).reshape(ka, kb)
    chi2 = float(chi2_contingency(table, correction=False)[0])
    return corrected_cramers_v(chi2, len(a), table.shape)


def compute_correlations(
    df: pd.DataFrame,
    type_info: dict[str, dict],
    *,
    threshold: float = config.HIGH_CORRELATION_THRESHOLD,
    max_numeric_columns: int = config.MAX_CORRELATION_NUMERIC_COLUMNS,
    max_cramers_columns: int = config.MAX_CRAMERS_V_COLUMNS,
    max_cramers_cardinality: int = config.MAX_CRAMERS_V_CARDINALITY,
    min_rows: int = config.MIN_CORRELATION_ROWS,
) -> CorrelationInfo:
    """Pearson matrix over numeric/mixed columns, Cramér's V over categorical
    and boolean columns (upper triangle), and every pair whose |value| reaches
    `threshold`, strongest first. A pair with fewer than `min_rows` rows where
    both values are present is None and never a high pair."""
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
    cramers: dict[str, dict[str, float | None]] = {}
    high_pairs: list[HighCorrelationPair] = []

    if len(numeric_cols) >= 2:
        matrix = (
            df[numeric_cols]
            .apply(pd.to_numeric, errors="coerce")
            .corr(min_periods=min_rows)
        )
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
        codes = {c: pd.factorize(df[c])[0] for c in cat_cols}
        for i, col_a in enumerate(cat_cols):
            cramers[col_a] = {}
            for col_b in cat_cols[i + 1 :]:
                shared = int(((codes[col_a] >= 0) & (codes[col_b] >= 0)).sum())
                if shared < min_rows:
                    cramers[col_a][col_b] = None
                    continue
                value = round(_cramers_v_codes(codes[col_a], codes[col_b]), 4)
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
