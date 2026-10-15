"""Exact and near-duplicate row detection.

Ported from the Data Quality Analyzer (`profiler/duplicates.py`) with the same
definitions. Deviations (see planning/milestone-2.md): index samples are
sorted for deterministic output and both lists share one cap, reported via
`indices_truncated`.
"""

import numpy as np
import pandas as pd

from data_quality_mcp import config
from data_quality_mcp.profiler_core.schema import DuplicateInfo

# Odd multipliers make each column's hash contribution distinct, so rows whose
# values are merely permuted across columns rarely produce the same key.
_COLUMN_MIX = np.uint64(0x9E3779B97F4A7C15)


def _near_duplicate_rows(df: pd.DataFrame) -> set[int]:
    """Rows equal to at least one other row on every column but one.

    Same result as running `df.duplicated(subset=<all but c>, keep=False)` for
    each column c, but each cell is hashed once instead of every column being
    re-compared per pass. A row's key for "all columns except c" is the
    (wrapping) sum of its per-column hashes minus column c's term, so each pass
    only deduplicates one uint64 array. Hash equality is only a filter: every
    candidate group is confirmed on the real values, so a hash collision can
    never add a false near-duplicate.
    """
    mixers = (
        np.arange(len(df.columns), dtype=np.uint64) * np.uint64(2) + np.uint64(1)
    ) * _COLUMN_MIX
    terms = (
        np.column_stack(
            [
                pd.util.hash_pandas_object(df[col], index=False).to_numpy(np.uint64)
                for col in df.columns
            ]
        )
        * mixers
    )  # uint64 arithmetic wraps modulo 2**64, which is intended
    total = terms.sum(axis=1, dtype=np.uint64)

    rows: set[int] = set()
    for j, col in enumerate(df.columns):
        candidates = pd.Series(total - terms[:, j]).duplicated(keep=False).to_numpy()
        if not candidates.any():
            continue
        others = [c for c in df.columns if c != col]
        subset = df.loc[candidates, others]
        confirmed = subset.duplicated(keep=False).to_numpy()
        rows.update(int(i) for i in subset.index[confirmed])
    return rows


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
    columns, and excludes rows that are already exact duplicates.

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
        near_rows = _near_duplicate_rows(df) - set(exact_rows)
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
