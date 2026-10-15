"""Outlier detection per numeric column: IQR fences and classic z-score.

Ported from the Data Quality Analyzer (`profiler/outliers.py`) with the same
methods, guards and percentages. Deviations (see planning/milestone-2.md):
non-applicable columns are None instead of zero counts, IQR fences are
reported, and index lists are sorted and capped.
"""

import numpy as np
import pandas as pd

from data_quality_mcp import config
from data_quality_mcp.profiler_core.schema import OutlierInfo

_NUMERIC_TYPES = ("numeric", "mixed")
_MIN_VALUES = 4  # Fewer numeric values than this: detection isn't meaningful


def _significant(value: float, digits: int = 10) -> float:
    """Round to significant digits: drops float noise (-3.6999999999999993 ->
    -3.7) without flattening small-scale values the way fixed decimals would."""
    return float(f"{value:.{digits}g}")


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
    """Outliers in one column.

    Quartiles, mean and std come from the finite values only, so a few +/-inf
    cells can't distort the fences. +/-inf values themselves are always
    flagged by both methods. With fewer than 4 finite numbers the fences
    aren't meaningful: the result then flags only the infinite values (bounds
    None), or is None when there are none. (Deviation from the analyzer, which
    computes on all values including inf; see planning/milestone-2.md.)
    """
    values = pd.to_numeric(series, errors="coerce")
    infinite = np.isinf(values.to_numpy(dtype=float, na_value=np.nan))
    infinite = pd.Series(infinite, index=values.index)
    finite = values[values.notna() & ~infinite]

    lower = upper = None
    if len(finite) < _MIN_VALUES:
        if not infinite.any():
            return None
        iqr_mask = z_mask = infinite
    else:
        q1 = float(finite.quantile(0.25))
        q3 = float(finite.quantile(0.75))
        iqr = q3 - q1
        if iqr == 0:
            iqr_mask = infinite
        else:
            fence_lo = q1 - iqr_multiplier * iqr
            fence_hi = q3 + iqr_multiplier * iqr
            iqr_mask = values.notna() & ((values < fence_lo) | (values > fence_hi))
            # Compare against the exact fences; report them without float noise
            lower, upper = _significant(fence_lo), _significant(fence_hi)

        mean = float(finite.mean())
        std = float(finite.std())
        if std == 0:
            z_mask = infinite
        else:
            z_mask = infinite | (
                values.notna() & ((values - mean) / std).abs().gt(zscore_threshold)
            )

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
