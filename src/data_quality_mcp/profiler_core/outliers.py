"""Outlier detection per numeric column: IQR fences and classic z-score.

Ported from the Data Quality Analyzer (`profiler/outliers.py`) with the same
methods, guards and percentages. Deviations (see planning/milestone-2.md):
non-applicable columns are None instead of zero counts, IQR fences are
reported, and index lists are sorted and capped. Added in Milestone 3.5 (D2):
`possible_sentinels`, outlier values that may be placeholder codes.
"""

import re

import numpy as np
import pandas as pd

from data_quality_mcp import config
from data_quality_mcp.profiler_core.schema import OutlierInfo, SentinelValue

_NUMERIC_TYPES = ("numeric", "mixed")
_MIN_VALUES = 4  # Fewer numeric values than this: detection isn't meaningful
_NINES = re.compile(r"9{3,}")


def _significant(value: float, digits: int = 10) -> float:
    """Round to significant digits: drops float noise (-3.6999999999999993 ->
    -3.7) without flattening small-scale values the way fixed decimals would."""
    return float(f"{value:.{digits}g}")


def _capped(mask: pd.Series, max_indices: int) -> tuple[list[int], bool]:
    indices = sorted(int(i) for i in mask[mask].index)
    return indices[:max_indices], len(indices) > max_indices


def possible_sentinels(
    values: pd.Series,
    finite: pd.Series,
    fence_lo: float,
    fence_hi: float,
    kept: pd.Series | None = None,
    *,
    min_unique_ratio: float = config.SENTINEL_MIN_UNIQUE_RATIO,
    max_sentinels: int | None = config.MAX_SENTINELS,
) -> list[SentinelValue]:
    """Outlier values that may be placeholder codes (planning/milestone-3.5.md, D2).

    A candidate is a finite value outside the fences that either repeats on at
    least 2 rows once exact duplicate rows are removed (`kept` marks the rows
    drop_duplicates(keep="first") leaves; None means all rows), in a column
    whose finite values on those rows have a unique ratio of at least
    `min_unique_ratio`; or is an integer made of three or more 9s. `count` is
    the number of input rows holding the value. Sorted by count descending,
    then value; capped at `max_sentinels` (None: uncapped, for the cleaner).
    """
    outside = finite[(finite < fence_lo) | (finite > fence_hi)]
    if outside.empty:
        return []
    kept_finite = finite if kept is None else finite[kept.reindex(finite.index)]
    candidates: set[float] = set()
    if len(kept_finite) and (
        kept_finite.nunique() / len(kept_finite) >= min_unique_ratio
    ):
        kept_outside = kept_finite[(kept_finite < fence_lo) | (kept_finite > fence_hi)]
        repeats = kept_outside.value_counts()
        candidates.update(float(v) for v in repeats[repeats >= 2].index)
    candidates.update(float(v) for v in outside.unique() if _all_nines(v))
    counts = values.value_counts()
    result: list[SentinelValue] = sorted(
        ({"value": v, "count": int(counts[v])} for v in candidates),
        key=lambda s: (-s["count"], s["value"]),
    )
    return result if max_sentinels is None else result[:max_sentinels]


def _all_nines(value: float) -> bool:
    """999, 9999, ... (or negative): the most common numeric placeholder."""
    return float(value).is_integer() and bool(_NINES.fullmatch(str(abs(int(value)))))


def detect_column_outliers(
    series: pd.Series,
    n_total: int,
    *,
    iqr_multiplier: float = config.OUTLIER_IQR_MULTIPLIER,
    zscore_threshold: float = config.OUTLIER_ZSCORE_THRESHOLD,
    max_indices: int = config.MAX_REPORTED_INDICES,
    kept: pd.Series | None = None,
    sentinel_min_unique_ratio: float = config.SENTINEL_MIN_UNIQUE_RATIO,
    max_sentinels: int | None = config.MAX_SENTINELS,
) -> OutlierInfo | None:
    """Outliers in one column.

    Quartiles, mean and std come from the finite values only, so a few +/-inf
    cells can't distort the fences. +/-inf values themselves are always
    flagged by both methods. With fewer than 4 finite numbers the fences
    aren't meaningful: the result then flags only the infinite values (bounds
    None), or is None when there are none. (Deviation from the analyzer, which
    computes on all values including inf; see planning/milestone-2.md.)

    `kept` and the sentinel parameters feed `possible_sentinels`, which is []
    without real fences.
    """
    values = pd.to_numeric(series, errors="coerce")
    infinite = np.isinf(values.to_numpy(dtype=float, na_value=np.nan))
    infinite = pd.Series(infinite, index=values.index)
    finite = values[values.notna() & ~infinite]

    lower = upper = None
    sentinels: list[SentinelValue] = []
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
            sentinels = possible_sentinels(
                values,
                finite,
                fence_lo,
                fence_hi,
                kept,
                min_unique_ratio=sentinel_min_unique_ratio,
                max_sentinels=max_sentinels,
            )

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
        "possible_sentinels": sentinels,
    }


def detect_outliers(
    df: pd.DataFrame,
    type_info: dict[str, dict],
    *,
    iqr_multiplier: float = config.OUTLIER_IQR_MULTIPLIER,
    zscore_threshold: float = config.OUTLIER_ZSCORE_THRESHOLD,
    max_indices: int = config.MAX_REPORTED_INDICES,
    sentinel_min_unique_ratio: float = config.SENTINEL_MIN_UNIQUE_RATIO,
    max_sentinels: int | None = config.MAX_SENTINELS,
    kept: pd.Series | None = None,
) -> dict[str, OutlierInfo | None]:
    """Return {column: OutlierInfo | None}; None for non-numeric columns.

    Indices are labels of `df.index`; callers pass a 0-based RangeIndex so
    they are row positions. `kept` is ~df.duplicated(keep="first"), computed
    here when not given (the report computes it once for every section).
    """
    n_total = len(df)
    if kept is None:
        kept = ~df.duplicated(keep="first")  # sentinel repeats ignore exact copies
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
            kept=kept,
            sentinel_min_unique_ratio=sentinel_min_unique_ratio,
            max_sentinels=max_sentinels,
        )
    return result


def outlier_score_pct(outliers: dict[str, OutlierInfo | None]) -> float:
    """The score's outlier input, as in the analyzer: the average IQR outlier %
    across numeric columns that have any IQR outliers (0.0 if none do)."""
    pcts = [o["iqr_pct"] for o in outliers.values() if o and o["iqr_count"] > 0]
    return sum(pcts) / len(pcts) if pcts else 0.0
