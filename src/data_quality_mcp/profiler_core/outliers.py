"""Outlier detection per numeric column: IQR fences and classic z-score.

Ported from the Data Quality Analyzer (`profiler/outliers.py`) with the same
methods, guards and percentages. Deviations (see planning/milestone-2.md):
non-applicable columns are None instead of zero counts, IQR fences are
reported, and index lists are sorted and capped.
"""

import pandas as pd

from data_quality_mcp import config
from data_quality_mcp.profiler_core.schema import OutlierInfo

_NUMERIC_TYPES = ("numeric", "mixed")
_MIN_VALUES = 4  # Fewer numeric values than this: detection isn't meaningful


def _capped(mask: pd.Series, max_indices: int) -> tuple[list[int], bool]:
    indices = sorted(int(i) for i in mask[mask].index)
    return indices[:max_indices], len(indices) > max_indices


def detect_column_outliers(
    series: pd.Series,
    n_total: int,
    *,
    iqr_multiplier: float = config.OUTLIER_IQR_MULTIPLIER,
    zscore_threshold: float = config.OUTLIER_ZSCORE_THRESHOLD,
    max_indices: int = config.MAX_REPORTED_INDICES,
) -> OutlierInfo | None:
    """Outliers in one column, or None when there are fewer than 4 numbers."""
    values = pd.to_numeric(series, errors="coerce")
    valid = values.dropna()
    if len(valid) < _MIN_VALUES:
        return None

    q1 = float(valid.quantile(0.25))
    q3 = float(valid.quantile(0.75))
    iqr = q3 - q1
    if iqr == 0:
        lower = upper = None
        iqr_mask = pd.Series(False, index=values.index)
    else:
        lower = q1 - iqr_multiplier * iqr
        upper = q3 + iqr_multiplier * iqr
        iqr_mask = values.notna() & ((values < lower) | (values > upper))

    mean = float(valid.mean())
    std = float(valid.std())
    if std == 0:
        z_mask = pd.Series(False, index=values.index)
    else:
        z_mask = values.notna() & (((values - mean) / std).abs() > zscore_threshold)

    iqr_count = int(iqr_mask.sum())
    z_count = int(z_mask.sum())
    iqr_indices, iqr_cut = _capped(iqr_mask, max_indices)
    z_indices, z_cut = _capped(z_mask, max_indices)
    return {
        "iqr_count": iqr_count,
        "iqr_pct": round(iqr_count / n_total * 100, 2),
        "iqr_lower_bound": lower,
        "iqr_upper_bound": upper,
        "iqr_indices": iqr_indices,
        "zscore_count": z_count,
        "zscore_pct": round(z_count / n_total * 100, 2),
        "zscore_indices": z_indices,
        "indices_truncated": iqr_cut or z_cut,
    }


def detect_outliers(
    df: pd.DataFrame,
    type_info: dict[str, dict],
    *,
    iqr_multiplier: float = config.OUTLIER_IQR_MULTIPLIER,
    zscore_threshold: float = config.OUTLIER_ZSCORE_THRESHOLD,
    max_indices: int = config.MAX_REPORTED_INDICES,
) -> dict[str, OutlierInfo | None]:
    """Return {column: OutlierInfo | None}; None for non-numeric columns.

    Indices are labels of `df.index`; callers pass a 0-based RangeIndex so
    they are row positions.
    """
    n_total = len(df)
    result: dict[str, OutlierInfo | None] = {}
    for col in df.columns:
        if type_info[col]["inferred_type"] not in _NUMERIC_TYPES or n_total == 0:
            result[col] = None
            continue
        result[col] = detect_column_outliers(
            df[col],
            n_total,
            iqr_multiplier=iqr_multiplier,
            zscore_threshold=zscore_threshold,
            max_indices=max_indices,
        )
    return result


def outlier_score_pct(outliers: dict[str, OutlierInfo | None]) -> float:
    """The score's outlier input, as in the analyzer: the average IQR outlier %
    across numeric columns that have any IQR outliers (0.0 if none do)."""
    pcts = [o["iqr_pct"] for o in outliers.values() if o and o["iqr_count"] > 0]
    return sum(pcts) / len(pcts) if pcts else 0.0
