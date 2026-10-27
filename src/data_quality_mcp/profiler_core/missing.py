"""Missing-value counts and percentages, and what the gaps go with.

The counts are simplified from the Data Quality Analyzer. Its MCAR/MAR/MNAR
labels are replaced by evidence: `missing_associations` lists the observed
columns whose values go with a column's missingness, with calibrated tests
(planning/milestone-3.5.md, D1). Data alone can't show that gaps are random
(MCAR) or depend on the missing values themselves (MNAR), so no label is given.
"""

import warnings

import numpy as np
import pandas as pd
from scipy.stats import chi2_contingency
from scipy.stats import t as t_distribution

from data_quality_mcp import config
from data_quality_mcp.profiler_core.schema import MissingAssociation

_NUMERIC_TYPES = ("numeric", "mixed")
_CATEGORICAL_TYPES = ("categorical", "boolean")
_MIN_CATEGORY_ROWS = 5  # Rarer categories are left out of Cramér's V (D1)


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


def _pearson_p(r: np.ndarray, n: np.ndarray) -> np.ndarray:
    """Two-sided p-value of Pearson r from n rows (t distribution, n - 2 df)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        dof = n - 2
        t = np.abs(r) * np.sqrt(dof / np.maximum(1.0 - r**2, 0.0))
        return np.where(np.abs(r) >= 1.0, 0.0, 2.0 * t_distribution.sf(t, dof))


def corrected_cramers_v(chi2: float, n: int, shape: tuple[int, int]) -> float:
    """Bias-corrected Cramér's V (Bergsma 2013). The plain V of the correlations
    section reaches 0.3 by chance on sparse tables (D1's calibration)."""
    rows, cols = shape
    phi2 = max(0.0, chi2 / n - (rows - 1) * (cols - 1) / (n - 1))
    rows_c = rows - (rows - 1) ** 2 / (n - 1)
    cols_c = cols - (cols - 1) ** 2 / (n - 1)
    denominator = min(rows_c - 1, cols_c - 1)
    return float(np.sqrt(phi2 / denominator)) if denominator > 0 else 0.0


def _point_biserial(
    indicators: np.ndarray,
    centred: np.ndarray,
    usable: np.ndarray,
    varies: np.ndarray,
    min_rows: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Point-biserial r of every missing indicator (n x t) against every
    numeric column (n x k) at once, each pair over the rows where the column
    has a finite number. `centred` is the numeric matrix minus each column's
    mean over its usable rows, with 0 where unusable; `varies` marks columns
    that aren't constant. Returns r, p and which pairs qualify, all k x t."""
    n = usable.sum(axis=0).astype(float)[:, None]
    k_missing = usable.T.astype(float) @ indicators
    k_present = n - k_missing
    sxx = (centred**2).sum(axis=0)[:, None]
    sxy = centred.T @ indicators  # the centred column sums to 0 on its rows
    with np.errstate(divide="ignore", invalid="ignore"):
        r = sxy / np.sqrt(sxx * (k_missing * k_present / n))
    eligible = (k_missing >= min_rows) & (k_present >= min_rows) & varies[:, None]
    r = np.where(eligible, r, 0.0)
    return r, _pearson_p(r, np.broadcast_to(n, r.shape)), eligible


def _categorical_test(
    missing: pd.Series, categories: pd.Series, min_rows: int
) -> tuple[float, float] | None:
    """Bias-corrected Cramér's V and chi-square p (no Yates' correction) over
    the rows where the category is present, leaving out categories with fewer
    than 5 rows; None when the pair doesn't qualify."""
    codes, _ = pd.factorize(categories)  # -1 where the category is missing
    is_missing = missing.to_numpy(dtype=bool)
    present = codes >= 0
    codes, is_missing = codes[present], is_missing[present]
    if len(codes) == 0:
        return None
    # The 2 x k table by counting codes (much faster than pd.crosstab)
    k = int(codes.max()) + 1
    table = np.bincount(codes + k * is_missing, minlength=2 * k).reshape(2, k)
    table = table[:, table.sum(axis=0) >= _MIN_CATEGORY_ROWS]  # common categories
    n_missing, n_present = int(table[1].sum()), int(table[0].sum())
    if table.shape[1] < 2 or n_missing < min_rows or n_present < min_rows:
        return None
    chi2, p = chi2_contingency(table, correction=False)[:2]
    n = n_missing + n_present
    return corrected_cramers_v(float(chi2), n, table.shape), float(p)


def missing_associations(
    df: pd.DataFrame,
    type_info: dict[str, dict],
    *,
    max_target_columns: int = config.MAX_MISSING_PATTERN_COLUMNS,
    min_rows: int = config.MIN_MISSING_PATTERN_ROWS,
    threshold: float = config.MISSING_ASSOCIATION_THRESHOLD,
    max_p: float = config.MISSING_ASSOCIATION_MAX_P,
    max_associations: int = config.MAX_MISSING_ASSOCIATIONS,
    max_numeric_columns: int = config.MAX_CORRELATION_NUMERIC_COLUMNS,
    max_categorical_columns: int = config.MAX_CRAMERS_V_COLUMNS,
    max_cardinality: int = config.MAX_CRAMERS_V_CARDINALITY,
) -> dict[str, list[MissingAssociation] | None]:
    """For each column, the observed columns whose values go with its
    missingness (planning/milestone-3.5.md, D1).

    Each column with missing values (the first `max_target_columns`) is
    compared with numeric/mixed columns (point-biserial r), categorical and
    boolean columns (bias-corrected Cramér's V) and the other columns'
    missing indicators (phi, positive only: "missing_together"). Text and
    datetime columns aren't comparison columns. A pair is tested only with at
    least `min_rows` missing and `min_rows` present target values among the
    rows it uses. An association is listed when |value| >= `threshold` and
    p < `max_p` / (pairs tested for that column), strongest first.

    Returns None for a column that wasn't tested (no missing values, past the
    column cap, or no pair qualified) and [] when pairs were tested but none
    was listed, which is consistent with random gaps but doesn't prove them.
    """
    result: dict[str, list[MissingAssociation] | None] = {c: None for c in df.columns}
    missing = df.isna()
    targets = [c for c in df.columns if missing[c].any()][:max_target_columns]
    if not targets:
        return result

    numeric_names = [
        c for c in df.columns if type_info[c]["inferred_type"] in _NUMERIC_TYPES
    ][:max_numeric_columns]
    matrix = np.empty((len(df), 0))
    if numeric_names:
        matrix = (
            df[numeric_names]
            .apply(pd.to_numeric, errors="coerce")
            .to_numpy(dtype=float, na_value=np.nan, copy=True)  # may be a view
        )
        matrix[~np.isfinite(matrix)] = np.nan
    usable = ~np.isnan(matrix)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN columns
        centred = np.where(usable, matrix - np.nanmean(matrix, axis=0), 0.0)
        # Constant from the raw values: a float mean's rounding error would make
        # a column of 0.1s look as if it varied
        varies = np.nanmax(matrix, axis=0, initial=-np.inf) > np.nanmin(
            matrix, axis=0, initial=np.inf
        )
    categorical_names = [
        c
        for c in df.columns
        if type_info[c]["inferred_type"] in _CATEGORICAL_TYPES
        and df[c].nunique(dropna=True) <= max_cardinality
    ][:max_categorical_columns]
    indicators = missing[targets].to_numpy(dtype=float)
    n_rows = len(df)
    counts = indicators.sum(axis=0)
    both_classes = (counts >= min_rows) & (n_rows - counts >= min_rows)
    r_numeric, p_numeric, numeric_ok = _point_biserial(
        indicators, centred, usable, varies, min_rows
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        phi = np.atleast_2d(np.corrcoef(indicators, rowvar=False))
    p_phi = _pearson_p(np.nan_to_num(phi), np.full(phi.shape, float(n_rows)))

    for i, target in enumerate(targets):
        tests = [
            (name, "point_biserial", float(r_numeric[j, i]), float(p_numeric[j, i]))
            for j, name in enumerate(numeric_names)
            if name != target and numeric_ok[j, i]
        ]
        for name in categorical_names:
            if name == target:
                continue
            outcome = _categorical_test(missing[target], df[name], min_rows)
            if outcome is not None:
                tests.append((name, "cramers_v", *outcome))
        if both_classes[i]:
            tests.extend(
                (targets[j], "missing_together", float(phi[i, j]), float(p_phi[i, j]))
                for j in range(len(targets))
                if j != i and both_classes[j]
            )
        if not tests:
            continue
        limit = max_p / len(tests)  # Bonferroni over the pairs actually tested
        listed = sorted(
            (
                (name, method, value)
                for name, method, value, p_value in tests
                if abs(value) >= threshold
                and p_value < limit
                and (method == "point_biserial" or value > 0)
            ),
            key=lambda item: (-abs(item[2]), item[0]),
        )
        result[target] = [
            {"column": name, "method": method, "value": round(value, 3)}
            for name, method, value in listed[:max_associations]
        ]
    return result
