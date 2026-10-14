"""Exact and near-duplicate row detection.

Ported from the Data Quality Analyzer (`profiler/duplicates.py`) with the same
definitions. Deviations (see planning/milestone-2.md): index samples are
sorted for deterministic output and both lists share one cap, reported via
`indices_truncated`.
"""

import pandas as pd

from data_quality_mcp import config
from data_quality_mcp.profiler_core.schema import DuplicateInfo


def analyze_duplicates(
    df: pd.DataFrame,
    *,
    max_indices: int = config.MAX_REPORTED_INDICES,
    max_near_duplicate_columns: int = config.MAX_NEAR_DUPLICATE_COLUMNS,
) -> DuplicateInfo:
    """Exact duplicates and near-duplicates (identical on all columns but one).

    exact_count is the number of rows that would be dropped keeping the first
    copy; sample_indices lists every row in a duplicate group. Near-duplicate
    search is skipped for fewer than 2 or more than `max_near_duplicate_columns`
    columns, and excludes rows that are already exact duplicates. Its cost is
    one full pass per column (about 25 s at 500k rows x 50 columns).

    Indices are labels of `df.index`; callers pass a 0-based RangeIndex (as
    report.py does) so they are row positions.
    """
    n = len(df)
    if n == 0:
        return {
            "exact_count": 0,
            "exact_pct": 0.0,
            "sample_indices": [],
            "near_duplicate_count": 0,
            "near_duplicate_pct": 0.0,
            "near_sample_indices": [],
            "indices_truncated": False,
        }

    in_group = df.duplicated(keep=False)  # every copy of a duplicated row
    exact_count = int(df.duplicated(keep="first").sum())
    exact_rows = sorted(int(i) for i in in_group[in_group].index)

    near_rows: set[int] = set()
    if 2 <= len(df.columns) <= max_near_duplicate_columns:
        exact_set = set(exact_rows)
        for col in df.columns:
            others = [c for c in df.columns if c != col]
            near = df.duplicated(subset=others, keep=False)
            near_rows.update(int(i) for i in near[near].index)
        near_rows -= exact_set
    near_sorted = sorted(near_rows)

    return {
        "exact_count": exact_count,
        "exact_pct": round(exact_count / n * 100, 2),
        "sample_indices": exact_rows[:max_indices],
        "near_duplicate_count": len(near_sorted),
        "near_duplicate_pct": round(len(near_sorted) / n * 100, 2),
        "near_sample_indices": near_sorted[:max_indices],
        "indices_truncated": len(exact_rows) > max_indices
        or len(near_sorted) > max_indices,
    }
