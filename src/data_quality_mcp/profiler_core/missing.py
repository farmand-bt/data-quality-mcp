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


def _numeric_tests(
    indicator: np.ndarray,
    centred: np.ndarray,
    usable: np.ndarray,
    names: list[str],
    min_rows: int,
) -> list[tuple[str, str, float, float]]:
    """Point-biserial r of one 0/1 missing indicator against every numeric
    column at once, each over the rows where that column has a finite number.
    `centred` is the numeric matrix minus each column's mean over its usable
    rows, with 0 where unusable."""
    if not names:
        return []
    n = usable.sum(axis=0).astype(float)
    k_missing = (usable * indicator[:, None]).sum(axis=0)
    k_present = n - k_missing
    sxx = (centred**2).sum(axis=0)
    sxy = (centred * indicator[:, None]).sum(axis=0)  # sum of centred x is 0
    with np.errstate(divide="ignore", invalid="ignore"):
        syy = k_missing * k_present / n
        r = sxy / np.sqrt(sxx * syy)
    eligible = (k_missing >= min_rows) & (k_present >= min_rows) & (sxx > 0)
    p = _pearson_p(np.where(eligible, r, 0.0), n)
    return [
        (names[j], "point_biserial", float(r[j]), float(p[j]))
        for j in np.flatnonzero(eligible)
    ]


def _categorical_test(
    missing: pd.Series, categories: pd.Series, min_rows: int
) -> tuple[float, float] | None:
    """Bias-corrected Cramér's V and chi-square p (no Yates' correction) over
    the rows where the category is present, leaving out categories with fewer
    than 5 rows; None when the pair doesn't qualify."""
    present = categories.notna()
    categories, missing = categories[present], missing[present]
    counts = categories.value_counts()
    common = categories.isin(counts[counts >= _MIN_CATEGORY_ROWS].index)
    categories, missing = categories[common], missing[common]
    n_missing = int(missing.sum())
    if (
        categories.nunique() < 2
        or n_missing < min_rows
        or len(missing) - n_missing < min_rows
    ):
        return None
    table = pd.crosstab(missing, categories)
    chi2, p = chi2_contingency(table, correction=False)[:2]
    return corrected_cramers_v(float(chi2), len(missing), table.shape), float(p)


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

    for i, target in enumerate(targets):
        y = indicators[:, i]
        keep = [j for j, name in enumerate(numeric_names) if name != target]
        tests = _numeric_tests(
            y,
            centred[:, keep],
            usable[:, keep],
            [numeric_names[j] for j in keep],
            min_rows,
        )
        for name in categorical_names:
            if name == target:
                continue
            outcome = _categorical_test(missing[target], df[name], min_rows)
            if outcome is not None:
                tests.append((name, "cramers_v", *outcome))
        others = [j for j in range(len(targets)) if j != i and both_classes[j]]
        if both_classes[i] and others:
            phi = np.array([np.corrcoef(y, indicators[:, j])[0, 1] for j in others])
            p = _pearson_p(phi, np.full(len(others), float(n_rows)))
            tests.extend(
                (targets[j], "missing_together", float(v), float(q))
                for j, v, q in zip(others, phi, p, strict=True)
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
